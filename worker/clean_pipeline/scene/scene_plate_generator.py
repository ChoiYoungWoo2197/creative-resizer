"""SCENE_GENERATION stage (P5 v3): project → remove ads → restore → outpaint → scene plate.

Two-step AI flow at TARGET canvas (max 2 API calls):
  1. Project canonical onto target canvas (no AI).
  2. Call 1 — remove ad objects from projected image using proj_removal mask.
  3. Restore protected-subject pixels from original_projection → clean_projection.
  4. Call 2 — fill letterbox regions (skipped if no letterbox).
  5. Restore projected-area pixels from clean_projection → scene_plate.

Why project-first (v3 vs v2):
  v2 cleaned at canonical size → AI saw the bright left side and extended it into the dark
  right-side removal area (wrong background). v3 projects first so the AI sees the full
  target layout: dark background surrounding the removal area → AI fills dark correctly.

Fail codes:
  CANONICAL_LOAD_FAILED, MASK_LOAD_FAILED, MASK_SIZE_MISMATCH,
  CLEANUP_SIZE_MISMATCH (removal cleanup wrong size),
  OUTPAINT_SIZE_MISMATCH (letterbox outpaint wrong size)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# Extra dilation applied to the projected removal mask before sending to AI.
# Closes narrow gaps between removal regions and covers boundary remnants.
_REMOVAL_DILATION_PX = 30

# Padding (px) around the removal bounding box when cropping for context-isolated cleanup.
# Sending only the local crop prevents gpt-image-1 from hallucinating the bright subject
# background into a dark-background removal area.
_CLEANUP_CROP_PADDING = 40

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

    # ── Step 1: Project canonical onto target canvas ─────────────────────────
    tx = target_transform.compute(
        canonical.width, canonical.height, target_width, target_height
    )

    original_projection = target_transform.apply_rgb(canonical, tx)
    original_proj_path = stage_dir / "original_projection.png"
    original_projection.save(str(original_proj_path))
    logger.artifact_written(STAGE.value, str(original_proj_path),
                            f"original projection (with ads) {tx.proj_width}×{tx.proj_height} "
                            f"offset ({tx.offset_x},{tx.offset_y})")

    # Project removal/restore masks to target canvas
    proj_removal_base = target_transform.apply_mask(removal_mask, tx)
    proj_rem_path = stage_dir / "projected_removal_mask.png"
    proj_removal_base.save(str(proj_rem_path))
    logger.artifact_written(STAGE.value, str(proj_rem_path), "projected removal mask (original)")

    # Dilate projected removal mask to close inter-region gaps and cover boundary
    # remnants (e.g. jar rim just outside the mask, text char in a narrow gap).
    proj_removal = proj_removal_base.filter(
        ImageFilter.MaxFilter(size=2 * _REMOVAL_DILATION_PX + 1)
    )
    proj_rem_dilated_path = stage_dir / "projected_removal_mask_dilated.png"
    proj_removal.save(str(proj_rem_dilated_path))
    logger.artifact_written(STAGE.value, str(proj_rem_dilated_path),
                            f"projected removal mask dilated (+{_REMOVAL_DILATION_PX}px)")

    # Build letterbox mask early (needed to intersect with proj_restore)
    outpaint_arr = np.zeros((target_height, target_width), dtype=np.uint8)
    if tx.offset_x > 0:
        outpaint_arr[:, :tx.offset_x] = 255
        outpaint_arr[:, tx.offset_x + tx.proj_width:] = 255
    if tx.offset_y > 0:
        outpaint_arr[:tx.offset_y, :] = 255
        outpaint_arr[tx.offset_y + tx.proj_height:, :] = 255
    inverted_arr = (255 - outpaint_arr).astype(np.uint8)  # white = projected area

    # proj_restore = (inverse of dilated removal) ∩ (projected area)
    # Intersecting with inverted_letterbox excludes letterbox pixels so that the
    # deterministic check holds only in the projected non-removal area.
    proj_rem_arr = np.array(proj_removal, dtype=np.uint8)
    proj_restore_arr = np.minimum(255 - proj_rem_arr, inverted_arr).astype(np.uint8)
    proj_restore = Image.fromarray(proj_restore_arr, "L")
    proj_res_path = stage_dir / "projected_restore_mask.png"
    proj_restore.save(str(proj_res_path))
    logger.artifact_written(STAGE.value, str(proj_res_path),
                            "projected restore mask (non-removal ∩ projected area)")

    # ── Step 2: Call 1 — remove ad objects (context-isolated crop) ───────────
    # Crop the projected image to the removal bounding box + padding so gpt-image-1
    # only sees the local dark-background context, not the bright subject on the far
    # left.  Without cropping, the AI extends the bright-subject background into the
    # dark-background removal area, producing a visible seam after pixel restore.
    rows, cols = np.where(proj_rem_arr > 128)
    if len(rows) > 0:
        pad = _CLEANUP_CROP_PADDING
        cx0 = max(0, int(cols.min()) - pad)
        cy0 = max(0, int(rows.min()) - pad)
        cx1 = min(target_width,  int(cols.max()) + pad)
        cy1 = min(target_height, int(rows.max()) + pad)
    else:
        cx0 = cy0 = 0
        cx1, cy1 = target_width, target_height
    crop_w = cx1 - cx0
    crop_h = cy1 - cy0

    crop_img  = original_projection.crop((cx0, cy0, cx1, cy1))
    crop_mask = proj_removal.crop((cx0, cy0, cx1, cy1))
    crop_img_path = stage_dir / "removal_crop_input.png"
    crop_img.save(str(crop_img_path))
    logger.artifact_written(STAGE.value, str(crop_img_path),
                            f"removal crop input ({cx0},{cy0})-({cx1},{cy1}) = {crop_w}×{crop_h}")

    removal_ai_crop, fail_code, fail_reason = openai_cleanup.cleanup(
        projected_image=crop_img,
        projected_removal_mask=crop_mask,
        target_width=crop_w,
        target_height=crop_h,
        api_key=api_key,
        prompt=CLEANUP_PROMPT,
    )
    if removal_ai_crop is None:
        return _fail(logger, fail_code, fail_reason)

    # Save raw crop output for debugging (shows what AI generated before paste-back)
    removal_ai_crop_path = stage_dir / "removal_ai_crop.png"
    removal_ai_crop.save(str(removal_ai_crop_path))
    logger.artifact_written(STAGE.value, str(removal_ai_crop_path),
                            f"raw AI removal crop result ({crop_w}×{crop_h})")

    # Paste AI crop result back into a copy of the full projection
    removal_ai = original_projection.copy()
    removal_ai.paste(removal_ai_crop, (cx0, cy0))

    removal_ai_path = stage_dir / "source_cleanup_ai.png"
    removal_ai.save(str(removal_ai_path))
    logger.artifact_written(STAGE.value, str(removal_ai_path), "AI removal result (target canvas, pasted)")

    # ── Step 3: Restore protected-subject pixels from original_projection ────
    clean_projection = immutable_pixel_restorer.restore(
        removal_ai, original_projection, proj_restore
    )
    clean_proj_path = stage_dir / "clean_source_projection.png"
    clean_projection.save(str(clean_proj_path))
    logger.artifact_written(STAGE.value, str(clean_proj_path),
                            "clean projection (ads removed, subject restored)")

    # ── Step 4: Finalize letterbox / inverted masks ───────────────────────────
    letterbox_mask = Image.fromarray(outpaint_arr, "L")
    outpaint_mask_path = stage_dir / "outpaint_mask.png"
    letterbox_mask.save(str(outpaint_mask_path))
    logger.artifact_written(STAGE.value, str(outpaint_mask_path),
                            "outpaint mask (letterbox regions)")

    inverted_letterbox = Image.fromarray(inverted_arr, "L")
    inverted_mask_path = stage_dir / "inverted_outpaint_mask.png"
    inverted_letterbox.save(str(inverted_mask_path))
    logger.artifact_written(STAGE.value, str(inverted_mask_path),
                            "inverted outpaint mask (projected region)")

    # ── Step 5: Call 2 — fill letterbox regions (skipped if no letterbox) ───
    has_letterbox = tx.offset_x > 0 or tx.offset_y > 0
    outpaint_api_call_count = 0
    target_outpaint_ai_path_str = ""

    if has_letterbox:
        outpaint_img, fail_code, fail_reason = openai_cleanup.cleanup(
            projected_image=clean_projection,
            projected_removal_mask=letterbox_mask,
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
        logger.artifact_written(STAGE.value, target_outpaint_ai_path_str, "AI letterbox outpaint result")
        outpaint_api_call_count = 1
    else:
        outpaint_img = clean_projection

    # ── Step 6: Restore projected-area pixels from clean_projection ──────────
    # inverted_letterbox (white = projected area) guarantees clean_projection pixels
    # in the non-letterbox region; letterbox pixels come from outpaint_img.
    scene_plate = immutable_pixel_restorer.restore(
        outpaint_img, clean_projection, inverted_letterbox
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
        # P6 comparison: original_projection (with ads) for AI validator;
        # proj_restore (white=protected subject only) for deterministic check.
        # scene[proj_restore > 128] == original_projection[proj_restore > 128]
        # is guaranteed: step 3 restores original_projection into clean_projection
        # for proj_restore pixels; step 6 preserves clean_projection in projected area.
        source_projection_path=str(original_proj_path),
        projected_removal_mask_path=str(proj_rem_path),
        projected_restore_mask_path=str(proj_res_path),
        ai_cleanup_path=str(removal_ai_path),
        scene_plate_path=str(scene_plate_path),
        scene_json_path="",
        api_call_count=1,  # always 1 (removal call); kept for backward compat
        # P5 v3 artifact paths
        original_projection_path=str(original_proj_path),
        source_cleanup_ai_path=str(removal_ai_path),
        clean_source_plate_path=str(clean_proj_path),
        clean_source_projection_path=str(clean_proj_path),
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
            "clean_source_projection": str(clean_proj_path),
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
