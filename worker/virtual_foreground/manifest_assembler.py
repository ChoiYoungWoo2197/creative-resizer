"""D-2: Orchestrate virtual foreground extraction and assemble fg_layers.

Entry point: run_virtual_foreground_extraction()
  1. Determine D-2 applicability (native-first policy)
  2. Analyze flattened objects (object_analyzer)
  3. Build source-aligned reference (source_reference via D-1)
  4. Extract per-object alpha masks (mask_extractor + mask_refiner)
  5. Validate quality (quality_validator)
  6. Deduplicate against native layers (native_matcher)
  7. Assemble fg_layers list (extract_foreground_layers() compatible format)

Spec Section 36: FORBIDDEN — human_subject as virtual layer,
  opaque bbox crop, native priority violation, D-2 skipping required objects.
"""
from __future__ import annotations

import hashlib
import os

from PIL import Image

from virtual_foreground.models import (
    VirtualObjectExtraction,
    VirtualForegroundExtractionResult,
    EXTRACTABLE_ROLES,
    FORBIDDEN_VIRTUAL_ROLES,
    METHOD_PAIRED_DIFFERENCE,
    D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS,
    D2_REASON_APPLICABLE_FLATTENED_PNG,
    D2_REASON_APPLICABLE_FLATTENED_JPG,
    D2_REASON_APPLICABLE_FLATTENED_UNKNOWN,
)
from virtual_foreground.object_analyzer import analyze_flattened_objects
from virtual_foreground.source_reference import build_source_aligned_reference
from virtual_foreground.mask_extractor import extract_object_mask
from virtual_foreground.mask_refiner import refine_alpha_mask
from virtual_foreground.quality_validator import validate_extraction_quality
from virtual_foreground.native_matcher import filter_virtual_detections


def _virtual_object_id(detection_id: str, semantic_role: str, bbox: dict) -> str:
    """Deterministic objectId for a virtual object (spec Section 15)."""
    raw = (
        f"virtual::{detection_id}::{semantic_role}"
        f"::{bbox.get('x',0)},{bbox.get('y',0)}"
        f",{bbox.get('width',0)}x{bbox.get('height',0)}"
    )
    return "virt_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def run_virtual_foreground_extraction(
    *,
    source_image: Image.Image,
    source_path: str,
    source_sha256: str,
    source_type: str,
    native_layers: list[dict] | None = None,
    background_provider: object,
    analysis_provider: object,
    output_dir: str,
    job_id: str = "",
) -> VirtualForegroundExtractionResult:
    """Run the full D-2 virtual foreground extraction pipeline.

    Args:
        source_image:       Full advertisement composite
        source_path:        File path to source
        source_sha256:      SHA-256 of source file
        source_type:        "png", "jpg", "psd", etc.
        native_layers:      PSD classified layers (if any — native takes priority)
        background_provider: BackgroundProvider (D-1 inpaint interface)
        analysis_provider:  ObjectAnalysisProvider (analyze interface)
        output_dir:         Work directory
        job_id:             For logging

    Returns:
        VirtualForegroundExtractionResult — never raises.
    """
    _native_layers = native_layers or []
    warnings: list[str] = []

    # ── Applicability check: native-first policy (spec Section 10) ────────────
    if _native_layers:
        print(
            f"[D2_PIPELINE] NOT_APPLICABLE jobId={job_id}"
            f" reason={D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS}"
            f" nativeLayerCount={len(_native_layers)}",
            flush=True,
        )
        return VirtualForegroundExtractionResult(
            success=True,
            source_type=source_type,
            source_sha256=source_sha256,
            d2_applicable=False,
            d2_reason=D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS,
            d2_implemented=True,
            fg_layers=[],
            warnings=warnings,
        )

    # D-2 is applicable — determine reason code
    st = (source_type or "").lower()
    if "png" in st:
        d2_reason = D2_REASON_APPLICABLE_FLATTENED_PNG
    elif "jpg" in st or "jpeg" in st:
        d2_reason = D2_REASON_APPLICABLE_FLATTENED_JPG
    else:
        d2_reason = D2_REASON_APPLICABLE_FLATTENED_UNKNOWN

    src_w, src_h = source_image.size
    print(
        f"[D2_PIPELINE] START jobId={job_id}"
        f" sourceType={source_type} reason={d2_reason}"
        f" sourceSize={src_w}x{src_h}",
        flush=True,
    )

    os.makedirs(output_dir, exist_ok=True)
    provider_request_count = 0

    # ── Step 1: Object analysis ───────────────────────────────────────────────
    obj_map = analyze_flattened_objects(
        source_image=source_image,
        source_sha256=source_sha256,
        provider=analysis_provider,
        job_id=job_id,
    )
    provider_request_count += 1
    warnings.extend(obj_map.warnings)

    if not obj_map.detections:
        return VirtualForegroundExtractionResult(
            success=False,
            failure_reason="D2_NO_OBJECTS_DETECTED",
            source_type=source_type,
            source_sha256=source_sha256,
            d2_applicable=True,
            d2_reason=d2_reason,
            d2_implemented=True,
            object_analysis_succeeded=False,
            detected_object_count=0,
            warnings=warnings,
        )

    detected_count = len(obj_map.detections)

    # ── Step 2: Source-aligned reference (D-1 at source dims) ─────────────────
    _ref_dir = os.path.join(output_dir, "source_reference")
    ref_result = build_source_aligned_reference(
        source_image=source_image,
        source_path=source_path,
        source_type=source_type,
        source_file_sha256=source_sha256,
        composite_sha256=source_sha256,
        provider=background_provider,
        output_dir=_ref_dir,
        has_native_layers=False,
        composite_render_method=source_type,
        job_id=job_id,
    )
    provider_request_count += ref_result.actual_provider_request_count

    if not ref_result.success or ref_result.scene_plate_image is None:
        return VirtualForegroundExtractionResult(
            success=False,
            failure_reason="D2_SOURCE_REFERENCE_BUILD_FAILED",
            source_type=source_type,
            source_sha256=source_sha256,
            d2_applicable=True,
            d2_reason=d2_reason,
            d2_implemented=True,
            object_analysis_succeeded=True,
            detected_object_count=detected_count,
            source_aligned_reference_sha256="",
            provider_request_count=provider_request_count,
            warnings=warnings + [f"ref_failure={ref_result.failure_reason}"],
        )

    reference_image = ref_result.scene_plate_image
    if reference_image.size != (src_w, src_h):
        reference_image = reference_image.resize((src_w, src_h), Image.LANCZOS)
    source_ref_sha256 = ref_result.scene_plate_sha256

    # ── Step 3: Native dedup ──────────────────────────────────────────────────
    filtered_detections, _match_logs = filter_virtual_detections(
        obj_map.detections, _native_layers, job_id=job_id
    )

    # ── Step 4: Per-detection virtual extraction ──────────────────────────────
    extracted_objects: list[VirtualObjectExtraction] = []
    virtual_rejected_count = 0

    for det in filtered_detections:
        role = det.semantic_role

        # Spec Section 36: FORBIDDEN — human_subject as virtual layer
        if role in FORBIDDEN_VIRTUAL_ROLES:
            warnings.append(
                f"SKIP_FORBIDDEN_ROLE detId={det.detection_id} role={role}"
            )
            print(
                f"[D2_EXTRACT] SKIP_HUMAN_SUBJECT"
                f" detId={det.detection_id} jobId={job_id}",
                flush=True,
            )
            continue

        if role not in EXTRACTABLE_ROLES:
            warnings.append(
                f"SKIP_NON_EXTRACTABLE_ROLE detId={det.detection_id} role={role}"
            )
            continue

        # Mask extraction via paired difference
        rgba_raw, mask_metrics = extract_object_mask(
            source_image=source_image,
            reference_image=reference_image,
            bbox=det.bbox,
            job_id=job_id,
            detection_id=det.detection_id,
        )

        if rgba_raw is None:
            err = mask_metrics.get("error", "UNKNOWN")
            vobj = VirtualObjectExtraction(
                detection_id=det.detection_id,
                semantic_role=role,
                layout_role=det.layout_role or role,
                extraction_success=False,
                failure_reason=f"MASK_EXTRACTION_FAILED:{err}",
                rejection_reason=err,
                source_bbox=dict(det.bbox),
            )
            extracted_objects.append(vobj)
            virtual_rejected_count += 1
            warnings.append(f"MASK_FAIL detId={det.detection_id} error={err}")
            continue

        # Refine alpha
        rgba_refined, _refine_meta = refine_alpha_mask(rgba_raw)

        # Quality validation
        quality = validate_extraction_quality(
            rgba_refined,
            source_bbox=det.bbox,
            detection_id=det.detection_id,
            job_id=job_id,
        )

        if not quality["passed"]:
            reasons = quality.get("failure_reasons", [])
            q = quality["metrics"]
            vobj = VirtualObjectExtraction(
                detection_id=det.detection_id,
                semantic_role=role,
                layout_role=det.layout_role or role,
                extraction_success=False,
                failure_reason=f"QUALITY_VALIDATION_FAILED:{reasons}",
                rejection_reason=(reasons[0] if reasons else "QUALITY_FAIL"),
                source_bbox=dict(det.bbox),
                alpha_coverage_ratio=q.get("alphaCoverageRatio", 0.0),
                border_alpha_ratio=q.get("borderAlphaRatio", 0.0),
                background_contamination_score=q.get(
                    "backgroundContaminationScore", 0.0
                ),
                component_count=q.get("componentCount", 0),
            )
            extracted_objects.append(vobj)
            virtual_rejected_count += 1
            warnings.append(
                f"QUALITY_FAIL detId={det.detection_id} reasons={reasons}"
            )
            continue

        # Success
        q = quality["metrics"]
        object_id = _virtual_object_id(det.detection_id, role, det.bbox)
        vobj = VirtualObjectExtraction(
            detection_id=det.detection_id,
            object_id=object_id,
            semantic_role=role,
            layout_role=det.layout_role or role,
            extraction_success=True,
            rgba_image=rgba_refined,
            source_bbox=dict(det.bbox),
            alpha_coverage_ratio=q.get("alphaCoverageRatio", 0.0),
            opaque_coverage_ratio=q.get("opaqueCoverageRatio", 0.0),
            border_alpha_ratio=q.get("borderAlphaRatio", 0.0),
            component_count=q.get("componentCount", 0),
            background_contamination_score=q.get(
                "backgroundContaminationScore", 0.0
            ),
            extraction_confidence=float(det.confidence),
            extraction_method=METHOD_PAIRED_DIFFERENCE,
            mask_sha256=mask_metrics.get("maskSha256", ""),
            pixel_sha256=mask_metrics.get("pixelSha256", ""),
        )
        extracted_objects.append(vobj)

    # ── Step 5: Build fg_layers ───────────────────────────────────────────────
    successful = [v for v in extracted_objects if v.extraction_success]
    fg_layers = _build_fg_layers(successful, src_w, src_h)

    virtual_extracted_count = len(successful)
    success = virtual_extracted_count > 0
    final_recomposition_possible = success

    print(
        f"[D2_PIPELINE] END jobId={job_id}"
        f" success={success}"
        f" detected={detected_count}"
        f" extracted={virtual_extracted_count}"
        f" rejected={virtual_rejected_count}"
        f" fgLayers={len(fg_layers)}"
        f" providerRequests={provider_request_count}",
        flush=True,
    )

    return VirtualForegroundExtractionResult(
        success=success,
        failure_reason="" if success else "NO_VIRTUAL_OBJECTS_EXTRACTED",
        source_type=source_type,
        source_sha256=source_sha256,
        d2_applicable=True,
        d2_reason=d2_reason,
        d2_implemented=True,
        object_analysis_succeeded=True,
        detected_object_count=detected_count,
        virtual_extracted_count=virtual_extracted_count,
        virtual_rejected_count=virtual_rejected_count,
        extracted_objects=extracted_objects,
        fg_layers=fg_layers,
        source_aligned_reference_sha256=source_ref_sha256,
        provider_request_count=provider_request_count,
        final_recomposition_possible=final_recomposition_possible,
        warnings=warnings,
    )


def _build_fg_layers(
    successful: list[VirtualObjectExtraction],
    source_w: int,
    source_h: int,
) -> list[dict]:
    """Convert successful VirtualObjectExtraction list to fg_layers format.

    Produces dicts compatible with extract_foreground_layers() output.
    bbox is stored in source coords — call scale_virtual_fg_layers() per spec.
    """
    layers = []
    for i, vobj in enumerate(successful):
        if not vobj.extraction_success or vobj.rgba_image is None:
            continue
        bbox = vobj.source_bbox
        layers.append({
            "role": vobj.semantic_role,
            "name": f"virtual_{vobj.semantic_role}_{vobj.detection_id}",
            "image": vobj.rgba_image,         # RGBA PIL image at source size
            "bbox": dict(bbox),               # source coords — scale per spec
            "sourceBBox": dict(bbox),
            "depth": i,
            "layerId": "",
            "objectId": vobj.object_id,
            "sourcePixelSha256": vobj.pixel_sha256,
            "compositedCount": 0,
            "isVirtual": True,
            "extractionMethod": vobj.extraction_method,
            "maskSha256": vobj.mask_sha256,
        })
    return layers


def scale_virtual_fg_layers(
    virtual_fg_layers: list[dict],
    source_w: int,
    source_h: int,
    target_w: int,
    target_h: int,
) -> list[dict]:
    """Scale virtual fg_layers from source coords to target canvas coords.

    Mirrors extract_foreground_layers() scaling:
      positions → scale_x / scale_y  (preserves relative layout)
      dimensions → scale_uniform      (preserves aspect ratio)
    """
    if not virtual_fg_layers or source_w <= 0 or source_h <= 0:
        return []

    scale_x = target_w / source_w
    scale_y = target_h / source_h
    scale_uniform = min(scale_x, scale_y)

    result = []
    for layer in virtual_fg_layers:
        src_bbox = layer.get("sourceBBox") or layer.get("bbox", {})
        ox = int(src_bbox.get("x", 0))
        oy = int(src_bbox.get("y", 0))
        ow = int(src_bbox.get("width", 0))
        oh = int(src_bbox.get("height", 0))

        if ow <= 0 or oh <= 0:
            continue

        rgba_img = layer.get("image")
        if rgba_img is None:
            continue

        sx = round(ox * scale_x)
        sy = round(oy * scale_y)
        sw = max(1, round(ow * scale_uniform))
        sh = max(1, round(oh * scale_uniform))

        if rgba_img.width != sw or rgba_img.height != sh:
            rgba_scaled = rgba_img.resize((sw, sh), Image.LANCZOS)
        else:
            rgba_scaled = rgba_img

        scaled = dict(layer)
        scaled["image"] = rgba_scaled
        scaled["bbox"] = {"x": sx, "y": sy, "width": sw, "height": sh}
        result.append(scaled)

    return result
