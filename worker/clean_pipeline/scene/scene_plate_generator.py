"""SCENE_GENERATION stage (P5 v2): source cleanup → project → outpaint → scene plate.

Two-step AI flow (max 2 API calls):
  1. Source cleanup  — remove ad objects at original (canonical) resolution.
  2. Source restore  — paste protected-subject pixels back from canonical.
  3. Project         — contain-fit clean source onto target canvas.
  4. Outpaint        — fill letterbox regions with AI (skipped if no letterbox).
  5. Projection restore — paste clean-source-projection pixels back deterministically.

Fail codes:
  CANONICAL_LOAD_FAILED, MASK_LOAD_FAILED, MASK_SIZE_MISMATCH,
  CLEANUP_SIZE_MISMATCH (source cleanup wrong size),
  OUTPAINT_SIZE_MISMATCH (outpaint wrong size)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.removal.models import RemovalMaskResult
from clean_pipeline.scene import (
    immutable_pixel_restorer,
    openai_cleanup,
    target_transform,
)
from clean_pipeline.scene.cleanup_prompt import CLEANUP_PROMPT, OUTPAINT_PROMPT
from clean_pipeline.scene.models import ScenePlateResult

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

    # ── Load canonical and masks ────────────────────────────────────────────
    try:
        canonical = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open canonical: {exc}")

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

    # ── Step 1: Source cleanup — remove ads at canonical resolution ─────────
    source_cleanup_ai, fail_code, fail_reason = openai_cleanup.cleanup(
        projected_image=canonical,
        projected_removal_mask=removal_mask,
        target_width=canonical.width,
        target_height=canonical.height,
        api_key=api_key,
        prompt=CLEANUP_PROMPT,
    )
    if source_cleanup_ai is None:
        return _fail(logger, fail_code, fail_reason)

    source_cleanup_ai_path = stage_dir / "source_cleanup_ai.png"
    source_cleanup_ai.save(str(source_cleanup_ai_path))
    logger.artifact_written(STAGE.value, str(source_cleanup_ai_path), "AI source cleanup result")

    # ── Step 2: Restore protected-subject pixels from canonical ─────────────
    clean_source_plate = immutable_pixel_restorer.restore(
        source_cleanup_ai, canonical, restore_mask
    )
    clean_source_plate_path = stage_dir / "clean_source_plate.png"
    clean_source_plate.save(str(clean_source_plate_path))
    logger.artifact_written(STAGE.value, str(clean_source_plate_path), "clean source plate")

    # ── Step 3: Project clean source onto target canvas ─────────────────────
    tx = target_transform.compute(
        canonical.width, canonical.height, target_width, target_height
    )

    # Save original projection (with ads) as debug artifact
    original_projection = target_transform.apply_rgb(canonical, tx)
    original_proj_path = stage_dir / "original_projection.png"
    original_projection.save(str(original_proj_path))
    logger.artifact_written(STAGE.value, str(original_proj_path),
                            f"original projection (with ads) {tx.proj_width}×{tx.proj_height} "
                            f"offset ({tx.offset_x},{tx.offset_y})")

    # Project removal/restore masks (for debug artifacts only)
    proj_removal = target_transform.apply_mask(removal_mask, tx)
    proj_restore = target_transform.apply_mask(restore_mask, tx)
    proj_rem_path = stage_dir / "projected_removal_mask.png"
    proj_res_path = stage_dir / "projected_restore_mask.png"
    proj_removal.save(str(proj_rem_path))
    proj_restore.save(str(proj_res_path))
    logger.artifact_written(STAGE.value, str(proj_rem_path), "projected removal mask")
    logger.artifact_written(STAGE.value, str(proj_res_path), "projected restore mask")

    # Project clean source
    clean_source_projection = target_transform.apply_rgb(clean_source_plate, tx)
    clean_source_proj_path = stage_dir / "clean_source_projection.png"
    clean_source_projection.save(str(clean_source_proj_path))
    logger.artifact_written(STAGE.value, str(clean_source_proj_path), "clean source projection")

    # Build outpaint mask: white = letterbox regions, black = projected source
    outpaint_arr = np.zeros((target_height, target_width), dtype=np.uint8)
    if tx.offset_x > 0:
        outpaint_arr[:, :tx.offset_x] = 255
        outpaint_arr[:, tx.offset_x + tx.proj_width:] = 255
    if tx.offset_y > 0:
        outpaint_arr[:tx.offset_y, :] = 255
        outpaint_arr[tx.offset_y + tx.proj_height:, :] = 255
    outpaint_mask = Image.fromarray(outpaint_arr, "L")

    outpaint_mask_path = stage_dir / "outpaint_mask.png"
    outpaint_mask.save(str(outpaint_mask_path))
    logger.artifact_written(STAGE.value, str(outpaint_mask_path), "outpaint mask (letterbox regions)")

    # Inverted outpaint mask: white = projected source region (used for P6 deterministic check)
    inverted_arr = (255 - outpaint_arr).astype(np.uint8)
    inverted_outpaint_mask = Image.fromarray(inverted_arr, "L")
    inverted_mask_path = stage_dir / "inverted_outpaint_mask.png"
    inverted_outpaint_mask.save(str(inverted_mask_path))
    logger.artifact_written(STAGE.value, str(inverted_mask_path), "inverted outpaint mask (projected region)")

    # ── Step 4: Outpaint letterbox regions (skipped if no letterbox) ────────
    has_letterbox = tx.offset_x > 0 or tx.offset_y > 0
    outpaint_api_call_count = 0
    target_outpaint_ai_path_str = ""

    if has_letterbox:
        outpaint_img, fail_code, fail_reason = openai_cleanup.cleanup(
            projected_image=clean_source_projection,
            projected_removal_mask=outpaint_mask,
            target_width=target_width,
            target_height=target_height,
            api_key=api_key,
            prompt=OUTPAINT_PROMPT,
        )
        if outpaint_img is None:
            if fail_code == "CLEANUP_SIZE_MISMATCH":
                fail_code = "OUTPAINT_SIZE_MISMATCH"
            return _fail(logger, fail_code, fail_reason)

        target_outpaint_ai_path = stage_dir / "target_outpaint_ai.png"
        outpaint_img.save(str(target_outpaint_ai_path))
        target_outpaint_ai_path_str = str(target_outpaint_ai_path)
        logger.artifact_written(STAGE.value, target_outpaint_ai_path_str, "AI outpaint result")
        outpaint_api_call_count = 1
    else:
        outpaint_img = clean_source_projection

    # ── Step 5: Restore clean-source-projection pixels deterministically ────
    scene_plate = immutable_pixel_restorer.restore(
        outpaint_img, clean_source_projection, inverted_outpaint_mask
    )
    scene_plate_path = stage_dir / "scene_plate.png"
    scene_plate.save(str(scene_plate_path))
    logger.artifact_written(STAGE.value, str(scene_plate_path), "scene plate (final)")

    # ── Build result ────────────────────────────────────────────────────────
    total_api_call_count = 1 + outpaint_api_call_count

    result_obj = ScenePlateResult(
        job_id=job_id,
        target_width=target_width,
        target_height=target_height,
        # P6 comparison fields: clean_source_projection is the reference
        source_projection_path=str(clean_source_proj_path),
        projected_removal_mask_path=str(proj_rem_path),
        projected_restore_mask_path=str(inverted_mask_path),
        # Backward-compat field pointing to the source cleanup AI output
        ai_cleanup_path=str(source_cleanup_ai_path),
        scene_plate_path=str(scene_plate_path),
        scene_json_path="",
        api_call_count=1,  # always 1 (source cleanup); kept for backward compat
        # New P5 v2 artifacts
        original_projection_path=str(original_proj_path),
        source_cleanup_ai_path=str(source_cleanup_ai_path),
        clean_source_plate_path=str(clean_source_plate_path),
        clean_source_projection_path=str(clean_source_proj_path),
        outpaint_mask_path=str(outpaint_mask_path),
        target_outpaint_ai_path=target_outpaint_ai_path_str,
        # Metrics
        source_cleanup_api_call_count=1,
        outpaint_api_call_count=outpaint_api_call_count,
        total_api_call_count=total_api_call_count,
    )

    scene_json_path = stage_dir / "scene_generation.json"
    scene_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.scene_json_path = str(scene_json_path)
    logger.artifact_written(STAGE.value, str(scene_json_path), "scene generation summary")

    logger.stage_pass(
        STAGE.value,
        f"scene plate generated at {target_width}×{target_height} "
        f"(apiCalls={total_api_call_count} outpaint={'yes' if has_letterbox else 'no'})",
        metrics={
            "targetWidth": target_width,
            "targetHeight": target_height,
            "totalApiCallCount": total_api_call_count,
            "outpaintApiCallCount": outpaint_api_call_count,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "targetWidth": target_width,
            "targetHeight": target_height,
            "totalApiCallCount": total_api_call_count,
        },
        artifacts={
            "original_projection": str(original_proj_path),
            "clean_source_projection": str(clean_source_proj_path),
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
