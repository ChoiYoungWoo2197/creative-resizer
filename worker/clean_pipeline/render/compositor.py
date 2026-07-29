"""FINAL_VALIDATION stage — step 1: composite placed objects onto the scene plate.

Processing:
  1. Load scene_plate.png as RGBA canvas
  2. Sort placed objects by z_index (ascending = lower drawn first)
  3. For each object: open RGBA crop, resize to placement dimensions, alpha-composite
  4. Save result.png (RGB)

Rules:
  - No new text rendering
  - No product regeneration
  - No fallback — any load failure → COMPOSITE_FAILED
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from clean_pipeline.analysis.models import SceneManifest
from clean_pipeline.contracts import StageName
from clean_pipeline.layout.models import PlacedObject
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.FINAL_VALIDATION
_OUTPUT_SUBDIR = Path("clean_v1") / "08_final"


def composite(
    scene_plate_path: str,
    placed: list[PlacedObject],
    manifest: SceneManifest,
    target_width: int,
    target_height: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[str | None, str, str]:
    """Composite placed objects onto scene_plate in z_index order.

    Returns (result_png_path, "", "") on success,
            (None, fail_code, fail_reason) on failure.
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    try:
        canvas = Image.open(scene_plate_path).convert("RGBA")
    except Exception as exc:
        return None, "SCENE_PLATE_LOAD_FAILED", f"Cannot open scene_plate: {exc}"

    if canvas.size != (target_width, target_height):
        return (
            None,
            "SCENE_PLATE_SIZE_MISMATCH",
            f"scene_plate is {canvas.size}, expected ({target_width}, {target_height})",
        )

    z_index_map = {obj.id: obj.z_index for obj in manifest.objects}
    required_ids = {obj.id for obj in manifest.objects if obj.required}
    # Skip required=false objects (e.g. decorative_ad panels) — they were removed from
    # the scene and must not be re-composited back onto the clean scene plate.
    sorted_placed = sorted(
        [po for po in placed if po.object_id in required_ids],
        key=lambda po: z_index_map.get(po.object_id, 0),
    )

    for po in sorted_placed:
        try:
            crop = Image.open(po.source_rgba_path).convert("RGBA")
        except Exception as exc:
            return None, "COMPOSITE_FAILED", f"Cannot open RGBA for '{po.object_id}': {exc}"

        resized = crop.resize((po.width, po.height), Image.LANCZOS)
        canvas.paste(resized, (po.x, po.y), resized)

    result_path = stage_dir / "result.png"
    canvas.convert("RGB").save(str(result_path), format="PNG")
    logger.artifact_written(STAGE.value, str(result_path), f"{len(placed)} objects composited")

    return str(result_path), "", ""
