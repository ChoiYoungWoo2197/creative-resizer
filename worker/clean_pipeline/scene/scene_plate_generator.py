"""SCENE_GENERATION stage: project source → cleanup → restore → scene plate.

Processing:
  1. Load canonical → contain-transform to target canvas → source_projection.png
  2. Apply same transform to removalMask → projected_removal_mask.png
  3. Apply same transform to restoreMask → projected_restore_mask.png
  4. openai_cleanup: exactly 1 API call → ai_cleanup.png
  5. Verify ai_cleanup.size == target (FAIL otherwise)
  6. immutable_pixel_restorer: paste source_projection pixels over restore region
  7. Save scene_plate.png + scene_generation.json

Forbidden:
  - source crop
  - aspect-ratio distortion
  - center-region full restore
  - bbox full restore
  - protected_subject bbox restore
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from worker.clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.removal.models import RemovalMaskResult
from worker.clean_pipeline.scene import (
    immutable_pixel_restorer,
    openai_cleanup,
    target_transform,
)
from worker.clean_pipeline.scene.models import ScenePlateResult

STAGE = StageName.SCENE_GENERATION
_OUTPUT_SUBDIR = Path("clean_v1") / "05_scene"


def generate(
    canonical_path: str,
    removal_result: RemovalMaskResult,
    target_width: int,
    target_height: int,
    api_key: str,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, ScenePlateResult | None]:

    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"canonical={canonical_path} target={target_width}×{target_height}",
        metrics={"targetWidth": target_width, "targetHeight": target_height},
    )

    # ── 1. Load canonical and compute contain transform ─────────────────────
    try:
        canonical = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open canonical: {exc}")

    tx = target_transform.compute(
        canonical.width, canonical.height, target_width, target_height
    )

    source_projection = target_transform.apply_rgb(canonical, tx)
    source_proj_path = stage_dir / "source_projection.png"
    source_projection.save(str(source_proj_path))
    logger.artifact_written(STAGE.value, str(source_proj_path),
                            f"contain projection {tx.proj_width}×{tx.proj_height} "
                            f"offset ({tx.offset_x},{tx.offset_y})")

    # ── 2–3. Project masks with same transform ──────────────────────────────
    try:
        removal_mask = Image.open(removal_result.removal_mask_path).convert("L")
        restore_mask = Image.open(removal_result.restore_mask_path).convert("L")
    except Exception as exc:
        return _fail(logger, "MASK_LOAD_FAILED", f"Cannot open removal/restore mask: {exc}")

    if removal_mask.size != (removal_result.image_width, removal_result.image_height):
        return _fail(
            logger, "MASK_SIZE_MISMATCH",
            f"removal_mask size {removal_mask.size} != "
            f"declared {removal_result.image_width}×{removal_result.image_height}",
        )

    proj_removal = target_transform.apply_mask(removal_mask, tx)
    proj_restore = target_transform.apply_mask(restore_mask, tx)

    proj_rem_path = stage_dir / "projected_removal_mask.png"
    proj_res_path = stage_dir / "projected_restore_mask.png"
    proj_removal.save(str(proj_rem_path))
    proj_restore.save(str(proj_res_path))
    logger.artifact_written(STAGE.value, str(proj_rem_path), "projected removal mask")
    logger.artifact_written(STAGE.value, str(proj_res_path), "projected restore mask")

    # ── 4. OpenAI cleanup — exactly 1 API call ─────────────────────────────
    cleanup_img, fail_code, fail_reason = openai_cleanup.cleanup(
        projected_image=source_projection,
        projected_removal_mask=proj_removal,
        target_width=target_width,
        target_height=target_height,
        api_key=api_key,
    )
    if cleanup_img is None:
        return _fail(logger, fail_code, fail_reason)

    # ── 5. Size validation ──────────────────────────────────────────────────
    if cleanup_img.size != (target_width, target_height):
        return _fail(
            logger,
            "CLEANUP_SIZE_MISMATCH",
            f"AI cleanup returned {cleanup_img.size[0]}×{cleanup_img.size[1]}, "
            f"expected {target_width}×{target_height}",
        )

    ai_cleanup_path = stage_dir / "ai_cleanup.png"
    cleanup_img.save(str(ai_cleanup_path))
    logger.artifact_written(STAGE.value, str(ai_cleanup_path), "AI cleanup result")

    # ── 6. Restore immutable pixels ─────────────────────────────────────────
    scene_plate = immutable_pixel_restorer.restore(cleanup_img, source_projection, proj_restore)

    scene_plate_path = stage_dir / "scene_plate.png"
    scene_plate.save(str(scene_plate_path))
    logger.artifact_written(STAGE.value, str(scene_plate_path), "scene plate (final)")

    # ── 7. scene_generation.json ────────────────────────────────────────────
    result_obj = ScenePlateResult(
        job_id=job_id,
        target_width=target_width,
        target_height=target_height,
        source_projection_path=str(source_proj_path),
        projected_removal_mask_path=str(proj_rem_path),
        projected_restore_mask_path=str(proj_res_path),
        ai_cleanup_path=str(ai_cleanup_path),
        scene_plate_path=str(scene_plate_path),
        scene_json_path="",
        api_call_count=1,
    )

    scene_json_path = stage_dir / "scene_generation.json"
    scene_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.scene_json_path = str(scene_json_path)
    logger.artifact_written(STAGE.value, str(scene_json_path), "scene generation summary")

    logger.stage_pass(
        STAGE.value,
        f"scene plate generated at {target_width}×{target_height}",
        metrics={"targetWidth": target_width, "targetHeight": target_height, "apiCallCount": 1},
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={"targetWidth": target_width, "targetHeight": target_height, "apiCallCount": 1},
        artifacts={
            "source_projection": str(source_proj_path),
            "projected_removal_mask": str(proj_rem_path),
            "projected_restore_mask": str(proj_res_path),
            "ai_cleanup": str(ai_cleanup_path),
            "scene_plate": str(scene_plate_path),
        },
    ), result_obj


# ── Helpers ───────────────────────────────────────────────────────────────────


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
