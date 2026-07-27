"""LAYOUT stage: validate + orchestrate full layout pipeline.

Hard Fail codes:
  NO_VALID_CANDIDATES           — required object has no valid placement
  DUPLICATE_OBJECT              — same object_id placed twice
  REQUIRED_OBJECT_MISSING       — required manifest object not placed
  SAFE_ZONE_VIOLATION           — required placed object outside safe zone
  CANVAS_OVERFLOW               — any placed object outside canvas
  EXCESSIVE_OVERLAP             — two placed objects overlap > 30%
  PROTECTED_SUBJECT_OCCLUDED    — placed object covers > 50% of protected subject

No origin-position fallback. No AI coordinate decisions.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from worker.clean_pipeline.analysis.models import SceneManifest
from worker.clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from worker.clean_pipeline.extraction.models import ExtractionResult
from worker.clean_pipeline.layout import candidate_generator, constraint_solver
from worker.clean_pipeline.layout.models import LayoutResult, PlacedObject, SafeZone
from worker.clean_pipeline.layout.safe_zone import (
    compute as compute_safe_zone,
    contains as sz_contains,
    intersection_area,
    within_canvas,
)
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.scene.models import ScenePlateResult

STAGE = StageName.LAYOUT
_OVERLAP_THRESHOLD = 0.30
_PROTECTED_COVERAGE_THRESHOLD = 0.50
_OUTPUT_SUBDIR = Path("clean_v1") / "07_layout"


# ── Public: post-placement validation ────────────────────────────────────────


def validate(
    placed: list[PlacedObject],
    manifest: SceneManifest,
    safe_zone: SafeZone,
    target_width: int,
    target_height: int,
) -> tuple[bool, str, str]:
    """Final sanity check on the placed list. Returns (ok, fail_code, reason)."""
    ids = [po.object_id for po in placed]
    if len(ids) != len(set(ids)):
        dupes = [i for i in set(ids) if ids.count(i) > 1]
        return False, "DUPLICATE_OBJECT", f"Duplicate placed object ids: {dupes}"

    # Required manifest objects (movable+removable, not protected)
    required_ids = {
        o.id for o in manifest.objects
        if o.required and o.movable and o.removable_from_scene
    }
    placed_ids = {po.object_id for po in placed}
    missing = required_ids - placed_ids
    if missing:
        return False, "REQUIRED_OBJECT_MISSING", f"Required objects not placed: {missing}"

    for po in placed:
        # Canvas bounds
        if not within_canvas(po.x, po.y, po.width, po.height, target_width, target_height):
            return False, "CANVAS_OVERFLOW", f"Object '{po.object_id}' outside canvas"
        # Safe zone (required only)
        if po.required and not sz_contains(safe_zone, po.x, po.y, po.width, po.height):
            return False, "SAFE_ZONE_VIOLATION", \
                f"Required object '{po.object_id}' outside safe zone"

    # Pairwise overlap
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            overlap = intersection_area(a.x, a.y, a.width, a.height, b.x, b.y, b.width, b.height)
            if overlap == 0:
                continue
            min_area = min(a.width * a.height, b.width * b.height)
            if min_area > 0 and overlap / min_area > _OVERLAP_THRESHOLD:
                return (
                    False,
                    "EXCESSIVE_OVERLAP",
                    f"Objects '{a.object_id}' and '{b.object_id}' overlap "
                    f"{overlap}/{min_area} = {overlap/min_area:.2f} > {_OVERLAP_THRESHOLD}",
                )

    return True, "", ""


# ── Public: orchestrator ──────────────────────────────────────────────────────


def run(
    extraction: ExtractionResult,
    scene_plate_result: ScenePlateResult,
    manifest: SceneManifest,
    safe_left: int,
    safe_top: int,
    safe_right: int,
    safe_bottom: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, LayoutResult | None]:

    target_width = scene_plate_result.target_width
    target_height = scene_plate_result.target_height
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"objects={len(extraction.objects)} target={target_width}×{target_height} "
        f"safe=({safe_left},{safe_top},{safe_right},{safe_bottom})",
        metrics={
            "targetWidth": target_width,
            "targetHeight": target_height,
            "objectCount": len(extraction.objects),
        },
    )

    if not extraction.objects:
        return _fail(logger, "NO_REMOVABLE_OBJECTS", "ExtractionResult has no objects to place")

    # Safe zone
    sz = compute_safe_zone(target_width, target_height, safe_left, safe_top, safe_right, safe_bottom)
    if sz.width <= 0 or sz.height <= 0:
        return _fail(
            logger, "INVALID_SAFE_ZONE",
            f"Safe zone has zero or negative area: {sz.width}×{sz.height}",
        )

    # Protected subject bboxes in target canvas coordinates
    protected_bboxes = _project_protected_bboxes(manifest, target_width, target_height)

    # Generate candidates
    all_candidates = candidate_generator.generate(extraction.objects, sz)

    # Save candidates.json
    candidates_dict = {
        obj_id: [
            {"anchor": c.anchor, "scale": c.scale, "x": c.x, "y": c.y, "w": c.width, "h": c.height}
            for c in cands
        ]
        for obj_id, cands in all_candidates.items()
    }
    candidates_json_path = stage_dir / "candidates.json"
    candidates_json_path.write_text(
        json.dumps(candidates_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.artifact_written(STAGE.value, str(candidates_json_path), "all candidates")

    # Solve
    placed, fail_code, fail_reason = constraint_solver.solve(
        all_candidates,
        extraction.objects,
        sz,
        target_width,
        target_height,
        protected_bboxes,
    )
    if placed is None:
        return _fail(logger, fail_code, fail_reason)

    # Final validation
    is_valid, val_code, val_reason = validate(placed, manifest, sz, target_width, target_height)
    if not is_valid:
        return _fail(logger, val_code, val_reason)

    # Save layout.json
    result_obj = LayoutResult(
        job_id=job_id,
        target_width=target_width,
        target_height=target_height,
        placed=placed,
    )
    layout_json_path = stage_dir / "layout.json"
    layout_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.layout_json_path = str(layout_json_path)
    logger.artifact_written(STAGE.value, str(layout_json_path), f"{len(placed)} objects placed")

    # Debug image
    debug_path = stage_dir / "layout_debug.png"
    _render_debug(
        scene_plate_result.scene_plate_path, placed, sz, target_width, target_height, str(debug_path)
    )
    result_obj.candidates_json_path = str(candidates_json_path)
    result_obj.layout_debug_path = str(debug_path)
    logger.artifact_written(STAGE.value, str(debug_path), "debug composite")

    logger.stage_pass(
        STAGE.value,
        f"{len(placed)} objects placed",
        metrics={"placedCount": len(placed)},
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={"placedCount": len(placed)},
        artifacts={
            "candidates_json": str(candidates_json_path),
            "layout_json": str(layout_json_path),
            "layout_debug": str(debug_path),
        },
    ), result_obj


# ── Helpers ───────────────────────────────────────────────────────────────────


def _project_protected_bboxes(
    manifest: SceneManifest,
    target_width: int,
    target_height: int,
) -> list[tuple[int, int, int, int]]:
    """Return protected subject bounding boxes projected into target canvas coordinates."""
    from worker.clean_pipeline.scene.target_transform import compute as compute_tx
    tx = compute_tx(manifest.image_width, manifest.image_height, target_width, target_height)
    scale_x = tx.proj_width / manifest.image_width
    scale_y = tx.proj_height / manifest.image_height
    result = []
    for obj in manifest.objects:
        if obj.role == "protected_subject":
            px = tx.offset_x + round(obj.bbox.x * scale_x)
            py = tx.offset_y + round(obj.bbox.y * scale_y)
            pw = max(1, round(obj.bbox.width * scale_x))
            ph = max(1, round(obj.bbox.height * scale_y))
            result.append((px, py, pw, ph))
    return result


def _render_debug(
    scene_plate_path: str,
    placed: list[PlacedObject],
    sz: SafeZone,
    target_width: int,
    target_height: int,
    out_path: str,
) -> None:
    try:
        canvas = Image.open(scene_plate_path).convert("RGBA")
    except Exception:
        canvas = Image.new("RGBA", (target_width, target_height), (30, 30, 30, 255))

    # Composite placed RGBA crops
    for po in placed:
        try:
            crop = Image.open(po.source_rgba_path).convert("RGBA")
            resized = crop.resize((po.width, po.height), Image.LANCZOS)
            canvas.paste(resized, (po.x, po.y), resized)
        except Exception:
            pass

    debug = canvas.convert("RGB")
    draw = ImageDraw.Draw(debug)

    # Safe zone outline (green)
    draw.rectangle(
        [(sz.left, sz.top), (sz.right - 1, sz.bottom - 1)],
        outline=(0, 220, 0),
        width=2,
    )

    # Placed object outlines (red for required, blue for optional)
    for po in placed:
        color = (220, 40, 40) if po.required else (40, 100, 220)
        draw.rectangle(
            [(po.x, po.y), (po.x + po.width - 1, po.y + po.height - 1)],
            outline=color,
            width=1,
        )

    debug.save(out_path, format="PNG")


def _fail(
    logger: PipelineLogger,
    code: str,
    message: str,
) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
