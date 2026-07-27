"""Unified Pipeline V2: isolated end-to-end orchestration.

Processing order per job:
  1. Load source → CanonicalSource (existing load_source_image + build_canonical_source)
  2. GPT Vision full-image analysis → V2DetectedObject list (once per job)
  3. Extract RGBA per object from canonical pixels → V2FgLayer list (once per job)
  4. Per spec: AI inpaint with (outpaint ∪ removal) mask → scene plate
  5. Per spec: layout planning with existing plan_foreground_layout
  6. Per spec: composite fg_layers onto scene plate → final image

Fail-closed: any unrecoverable step raises RuntimeError — no fake/mock results.
"""
from __future__ import annotations

import hashlib
import os
import time

import numpy as np
from PIL import Image

from unified_v2.contracts import V2FgLayer, V2Manifest


def run_unified_v2(
    psd_path: str,
    specs: list,
    output_format: str,
    job_output_dir: str,
    source_type: str,
    job_id: str = "",
    **_kwargs,
) -> tuple[list, list]:
    """V2 pipeline entry point — called from app.py when UNIFIED_PIPELINE_V2_ENABLED=true.

    Args:
        psd_path:       Absolute path to source image (PSD, PNG, JPG).
        specs:          List of target spec dicts (width, height, ratioType, safe zone …).
        output_format:  "png" or "jpg".
        job_output_dir: Directory to write output images.
        source_type:    "psd", "png", "jpg", or "image".
        job_id:         Unique job identifier for structured logging.

    Returns:
        (result_items, missing_ratio_types)
        result_items:         list of {filePath, ratioType, width, height, valid, renderSource}
        missing_ratio_types:  list of spec ratioType strings that failed.

    Raises:
        RuntimeError if source load, GPT analysis, or extraction fails fatally.
    """
    t_start = time.time()
    print(
        f"[V2_PIPELINE_START] jobId={job_id}"
        f" specCount={len(specs)}"
        f" sourceType={source_type!r}",
        flush=True,
    )

    # ── Step 1: Load source → CanonicalSource ────────────────────────────────
    from resizer import load_source_image
    from scene_cleanup.canonical_source import build_canonical_source, log_canonical_source

    source_img, _meta = load_source_image(psd_path)

    with open(psd_path, "rb") as _fh:
        source_file_sha = hashlib.sha256(_fh.read()).hexdigest()[:16]

    ext = os.path.splitext(psd_path)[1].lstrip(".").lower()
    canonical = build_canonical_source(
        image=source_img,
        source_type=source_type,
        source_file_sha256=source_file_sha,
        original_filename=os.path.basename(psd_path),
        input_format=ext,
    )
    log_canonical_source(canonical, job_id=job_id)
    print(
        f"[V2_CANONICAL_SOURCE] jobId={job_id}"
        f" size={canonical.width}x{canonical.height}"
        f" sha={canonical.canonical_image_sha256}"
        f" sourceType={canonical.source_type!r}",
        flush=True,
    )

    # ── Step 2: GPT Vision analysis (once per job) ───────────────────────────
    from unified_v2.analyzer import analyze_source_objects

    detected_objects, gpt_model = analyze_source_objects(canonical.image, job_id=job_id)

    if not detected_objects:
        raise RuntimeError(
            "V2_NO_OBJECTS_DETECTED: GPT Vision found 0 objects in source image — FAIL"
        )

    # ── Step 3: Extract RGBA per object ─────────────────────────────────────
    from unified_v2.extractor import extract_fg_layer

    fg_layers: list[V2FgLayer] = []
    for obj in detected_objects:
        layer = extract_fg_layer(canonical.image, obj, job_id=job_id)
        if layer is not None:
            fg_layers.append(layer)

    manifest = V2Manifest(
        canonical_sha=canonical.canonical_image_sha256,
        source_w=canonical.width,
        source_h=canonical.height,
        gpt_model=gpt_model,
        detected_objects=detected_objects,
        fg_layers=fg_layers,
        detection_count=len(detected_objects),
        extraction_count=len(fg_layers),
    )

    print(
        f"[V2_MANIFEST] jobId={job_id}"
        f" detectedObjects={manifest.detection_count}"
        f" extractedLayers={manifest.extraction_count}"
        f" model={manifest.gpt_model!r}",
        flush=True,
    )

    if not fg_layers:
        raise RuntimeError(
            "V2_EXTRACTION_FAILED: 0 fg_layers successfully extracted — FAIL"
        )

    # ── Steps 4–6: Per-spec processing ───────────────────────────────────────
    from background.external_provider import ProviderFactory

    provider = ProviderFactory.create(enable_external=True)
    os.makedirs(job_output_dir, exist_ok=True)

    result_items: list[dict] = []
    missing_ratio_types: list[str] = []

    for spec_idx, spec in enumerate(specs):
        spec_id = str(spec.get("ratioType") or spec.get("id") or spec_idx)
        target_w = int(spec.get("width", 1200))
        target_h = int(spec.get("height", 628))

        try:
            item = _process_spec(
                canonical=canonical,
                fg_layers=fg_layers,
                spec=spec,
                spec_id=spec_id,
                target_w=target_w,
                target_h=target_h,
                provider=provider,
                source_file_sha=source_file_sha,
                source_type=source_type,
                output_dir=job_output_dir,
                output_format=output_format,
                job_id=job_id,
            )
            result_items.append(item)
        except Exception as exc:
            print(
                f"[V2_SPEC_FAIL] jobId={job_id} specId={spec_id} error={exc}",
                flush=True,
            )
            missing_ratio_types.append(spec_id)

    elapsed_ms = int((time.time() - t_start) * 1000)
    valid_count = sum(1 for r in result_items if r.get("valid"))
    print(
        f"[V2_FINAL_SUMMARY] jobId={job_id}"
        f" elapsedMs={elapsed_ms}"
        f" totalSpecs={len(specs)}"
        f" validCount={valid_count}"
        f" missingCount={len(missing_ratio_types)}",
        flush=True,
    )

    if not result_items:
        raise RuntimeError(
            f"V2_ALL_SPECS_FAILED: 0/{len(specs)} specs produced output — FAIL"
        )

    return result_items, missing_ratio_types


# ── Per-spec helper ──────────────────────────────────────────────────────────

def _process_spec(
    *,
    canonical,
    fg_layers: list[V2FgLayer],
    spec: dict,
    spec_id: str,
    target_w: int,
    target_h: int,
    provider,
    source_file_sha: str,
    source_type: str,
    output_dir: str,
    output_format: str,
    job_id: str,
) -> dict:
    """AI cleanup + layout + composite for one target spec.

    Returns a result_item dict compatible with the /generate endpoint contract.
    Raises RuntimeError on any failure — fail-closed.
    """
    from scene_cleanup.canvas_builder import build_provider_canvas_outpaint, build_canonical_at_target
    from scene_cleanup.full_image_source import build_full_image_source
    from scene_cleanup.prompt_builder import build_semantic_prompt
    from background.external_provider import normalize_provider_result
    from layout.reflow_engine import plan_foreground_layout
    from unified_v2.renderer import composite_on_scene_plate

    # Build FullImageSource wrapper (required by canvas_builder)
    full_source = build_full_image_source(
        source_image=canonical.image,
        source_path="",
        source_file_sha256=source_file_sha,
        composite_sha256=canonical.canonical_image_sha256,
        source_type=source_type,
        has_native_layers=(source_type == "psd"),
        composite_render_method="unified_v2",
    )

    # Contain-scale source into target canvas; outpaint regions get AI fill
    provider_input, _outpaint_mask_img, canvas_transform, outpaint_mask_arr = (
        build_provider_canvas_outpaint(full_source, target_w, target_h)
    )

    scale = canvas_transform.scale
    offset_x = canvas_transform.paste_offset_x
    offset_y = canvas_transform.paste_offset_y

    # Build removal mask: source-coord fg_layer bboxes mapped to canvas coords
    removal_mask = np.zeros((target_h, target_w), dtype=np.uint8)
    for layer in fg_layers:
        sb = layer.source_bbox
        cx1 = int(sb["x"] * scale + offset_x)
        cy1 = int(sb["y"] * scale + offset_y)
        cx2 = int((sb["x"] + sb["width"]) * scale + offset_x)
        cy2 = int((sb["y"] + sb["height"]) * scale + offset_y)
        cx1 = max(0, cx1)
        cy1 = max(0, cy1)
        cx2 = min(target_w, cx2)
        cy2 = min(target_h, cy2)
        if cx2 > cx1 and cy2 > cy1:
            removal_mask[cy1:cy2, cx1:cx2] = 255

    # Combined mask: outpaint region + removal region → AI fills both
    combined_mask_arr = np.maximum(outpaint_mask_arr, removal_mask)
    combined_mask_img = Image.fromarray(combined_mask_arr, "L")

    prompt, prompt_version = build_semantic_prompt(target_w, target_h)

    print(
        f"[V2_SCENE_CLEANUP_START] jobId={job_id} specId={spec_id}"
        f" target={target_w}x{target_h}"
        f" outpaintPixels={int(np.sum(outpaint_mask_arr > 0))}"
        f" removalPixels={int(np.sum(removal_mask > 0))}",
        flush=True,
    )

    raw_result = provider.inpaint(
        provider_input,
        combined_mask_img,
        prompt,
        {"prompt_version": prompt_version},
    )
    scene_plate_img, provider_name = normalize_provider_result(raw_result)

    if scene_plate_img is None:
        raise RuntimeError(
            f"V2_SCENE_CLEANUP_PROVIDER_FAILED: specId={spec_id} provider returned None"
        )

    if scene_plate_img.size != (target_w, target_h):
        scene_plate_img = scene_plate_img.resize((target_w, target_h), Image.LANCZOS)

    # Pixel restore: regions outside (outpaint ∪ removal) keep canonical pixels
    canonical_at_target = build_canonical_at_target(canonical.image, canvas_transform)
    scene_arr = np.array(scene_plate_img.convert("RGB"))
    canon_arr = np.array(canonical_at_target.convert("RGB"))
    immutable_mask = combined_mask_arr == 0
    scene_arr[immutable_mask] = canon_arr[immutable_mask]
    scene_plate_img = Image.fromarray(scene_arr, "RGB")

    print(
        f"[V2_SCENE_CLEANUP_END] jobId={job_id} specId={spec_id}"
        f" provider={provider_name!r}"
        f" promptVersion={prompt_version!r}"
        f" restoredPixels={int(np.sum(immutable_mask))}",
        flush=True,
    )

    # ── Layout planning ───────────────────────────────────────────────────────
    layout_fg = [
        {
            "objectId": layer.object_id,
            "role": layer.role,
            "image": layer.image,
            "bbox": dict(layer.source_bbox),
            "sourceBBox": dict(layer.source_bbox),
            "maskSha256": layer.mask_sha256,
        }
        for layer in fg_layers
    ]

    layout_result = plan_foreground_layout(
        fg_layers=layout_fg,
        spec=spec,
        canvas_w=target_w,
        canvas_h=target_h,
        target_w=target_w,
        target_h=target_h,
        job_id=job_id,
        spec_id=spec_id,
    )

    # Build placed V2FgLayer list from layout objectPlacements
    placed_layers: list[V2FgLayer] = []
    layer_by_id = {l.object_id: l for l in fg_layers}

    for placement in (layout_result.objectPlacements or []):
        oid = getattr(placement, "objectId", "")
        target_bbox = getattr(placement, "targetBBox", None) or {}
        match = layer_by_id.get(oid)
        if match is None:
            continue
        placed_layers.append(
            V2FgLayer(
                object_id=match.object_id,
                role=match.role,
                image=match.image,
                source_bbox=match.source_bbox,
                bbox={
                    "x": int(target_bbox.get("x", match.source_bbox["x"])),
                    "y": int(target_bbox.get("y", match.source_bbox["y"])),
                    "width": int(target_bbox.get("width", match.source_bbox["width"])),
                    "height": int(target_bbox.get("height", match.source_bbox["height"])),
                },
                mask_sha256=match.mask_sha256,
                pixel_sha256=match.pixel_sha256,
                text_content=match.text_content,
            )
        )

    # Fallback: scale source-coords to canvas coords if layout gave no placements
    if not placed_layers:
        print(
            f"[V2_LAYOUT_FALLBACK] jobId={job_id} specId={spec_id}"
            f" reason=NO_PLACEMENTS",
            flush=True,
        )
        for layer in fg_layers:
            sb = layer.source_bbox
            placed_layers.append(
                V2FgLayer(
                    object_id=layer.object_id,
                    role=layer.role,
                    image=layer.image,
                    source_bbox=layer.source_bbox,
                    bbox={
                        "x": int(sb["x"] * scale + offset_x),
                        "y": int(sb["y"] * scale + offset_y),
                        "width": max(1, int(sb["width"] * scale)),
                        "height": max(1, int(sb["height"] * scale)),
                    },
                    mask_sha256=layer.mask_sha256,
                    pixel_sha256=layer.pixel_sha256,
                    text_content=layer.text_content,
                )
            )

    # ── Composite ─────────────────────────────────────────────────────────────
    final_img = composite_on_scene_plate(
        scene_plate=scene_plate_img,
        fg_layers=placed_layers,
        job_id=job_id,
        spec_id=spec_id,
    )

    # ── Save output ───────────────────────────────────────────────────────────
    ext_str = "jpg" if output_format == "jpg" else "png"
    out_filename = f"{spec_id}_{target_w}x{target_h}.{ext_str}"
    out_path = os.path.join(output_dir, out_filename)

    if ext_str == "jpg":
        final_img.convert("RGB").save(out_path, "JPEG", quality=90)
    else:
        final_img.save(out_path, "PNG")

    return {
        "filePath": out_path,
        "ratioType": spec_id,
        "width": target_w,
        "height": target_h,
        "valid": True,
        "renderSource": "unified_v2",
    }
