"""SCENE_GENERATION stage (P5 v3): project → remove ads → restore → outpaint → scene plate.

Removal fill strategy (Step 2):
  The 30-px dilation expansion band (pixels dilated into but not originally in the
  removal area) represents the immediate background surrounding the ad objects.

  - If that band is UNIFORM (max per-channel std < _UNIFORM_BG_STD_THRESHOLD):
      Fill the removal area directly with the sampled background color.
      No API call needed.  Produces pixel-perfect, seam-free result.
  - If COMPLEX (textured / gradient):
      Fall back to gpt-image-1 images.edit.
      Before calling, pre-fill the removal area + _AI_CONTEXT_BUFFER_PX buffer
      with natural background sampled from outside the removal region.
      This prevents dark overlay panel pixels from appearing as OPAQUE context
      in DALL-E's mask and causing it to generate dark/black content.

Steps:
  1. Project canonical onto target canvas (no AI).
  2. Fill removal area: direct color fill OR AI cleanup (see above).
  3. Restore protected-subject pixels from original_projection → clean_projection.
  4. Fill letterbox regions via AI outpaint (skipped if no letterbox).
  5. Restore projected-area pixels from clean_projection → scene_plate.

Fail codes:
  CANONICAL_LOAD_FAILED, MASK_LOAD_FAILED, MASK_SIZE_MISMATCH,
  CLEANUP_SIZE_MISMATCH (removal AI wrong size),
  OUTPAINT_SIZE_MISMATCH (letterbox outpaint wrong size)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# Extra dilation applied to the projected removal mask before sending to AI.
# Closes narrow gaps between removal regions and covers boundary remnants.
_REMOVAL_DILATION_PX = 30

# If the per-channel std-dev of the dilation expansion band pixels is below this
# threshold the background is considered uniform → direct fill (no API call).
_UNIFORM_BG_STD_THRESHOLD = 25

# If the removal area covers this fraction of the total canvas or more,
# always use the AI path regardless of bg_std.
# Large removals (e.g. a half-image solid-color panel) need AI to naturally
# extend the surrounding photo — a single sampled color produces a flat seam.
_LARGE_REMOVAL_THRESHOLD = 0.20

# When the AI path is taken, pre-fill the removal area + this many extra pixels
# around it with natural background color before calling gpt-image-1.
# This prevents dark overlay panel pixels from appearing as OPAQUE context
# in DALL-E's mask and causing it to generate dark/black content.
_AI_CONTEXT_BUFFER_PX = 40

# ── TYPE B flags ──────────────────────────────────────────────────────────────
# TYPE A (original): pre-fill + mask → gpt → restore source pixels
# TYPE B (prototype): full original image + no mask → gpt → no restore
#
# _TYPE_B_NO_MASK: send full original image to gpt without a mask.
#   gpt sees the whole scene and decides what to remove (ad elements) vs. keep (person).
#   Set False to revert to TYPE A mask-based inpainting.
_TYPE_B_NO_MASK = True

# _SKIP_SUBJECT_RESTORE: skip Step 3 pixel restore after gpt cleanup.
#   No restore boundary → no seam. Subject may be slightly altered; acceptable for prototype.
#   Set False to revert to TYPE A pixel-perfect restore.
_SKIP_SUBJECT_RESTORE = True

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.removal.models import RemovalMaskResult
from clean_pipeline.scene import (
    immutable_pixel_restorer,
    openai_cleanup,
    target_transform,
)
from clean_pipeline.scene.cleanup_prompt import CLEANUP_PROMPT, CLEANUP_PROMPT_TYPE_B, OUTPAINT_PROMPT
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
    clean_canonical_path: str = "",
) -> tuple[StageResult, ScenePlateResult | None]:
    """scene_plate 생성.

    clean_canonical_path 지정 시 AI inpainting 입력을 clean_canonical 사용
    (lama로 텍스트 제거된 이미지 → 환각 텍스트 방지).
    미지정 시 원본 canonical 사용.
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    # AI 입력 이미지: clean_canonical 우선, 없으면 canonical
    ai_input_path = clean_canonical_path if clean_canonical_path else canonical_path

    logger.stage_start(
        STAGE.value,
        f"input={ai_input_path} target={target_width}×{target_height} "
        f"clean={'yes' if clean_canonical_path else 'no'}",
        metrics={"targetWidth": target_width, "targetHeight": target_height,
                 "useCleanCanonical": bool(clean_canonical_path)},
    )

    # ── Load canonical and masks ────────────────────────────────────────────
    try:
        canonical = Image.open(ai_input_path).convert("RGB")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open input image: {exc}")

    # 규격 검증 Assert
    assert canonical.width == removal_result.image_width and canonical.height == removal_result.image_height, (
        f"input image size ({canonical.width},{canonical.height}) != "
        f"removal_mask size ({removal_result.image_width},{removal_result.image_height})"
    )

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

    # ── Step 2: Fill removal area (direct color fill or AI) ──────────────────
    # Sample the 30-px dilation expansion band: pixels that the MaxFilter added
    # around the original removal area.  These are the immediate background
    # neighbors of the ad objects — the color that must continue into the
    # removal area after the ads are erased.
    original_removal_bool = np.array(proj_removal_base) > 128
    expansion_band = (proj_rem_arr > 128) & ~original_removal_bool  # dilated only

    source_cleanup_api_calls = 0
    original_arr = np.array(original_projection)

    if expansion_band.any():
        band_pixels = original_arr[expansion_band]
        bg_estimate = np.median(band_pixels, axis=0).astype(np.uint8)
        bg_std = float(band_pixels.std(axis=0).max())
    else:
        bg_estimate = np.zeros(3, dtype=np.uint8)
        bg_std = 100.0  # no band → force AI path

    total_pixels = target_width * target_height
    removal_ratio = float((proj_rem_arr > 128).sum()) / total_pixels
    force_ai = removal_ratio >= _LARGE_REMOVAL_THRESHOLD

    logger.artifact_written(
        STAGE.value, "(memory) bg_sample",
        f"expansion_band_px={int(expansion_band.sum())} "
        f"bg={bg_estimate.tolist()} std={bg_std:.1f} "
        f"threshold={_UNIFORM_BG_STD_THRESHOLD} "
        f"removal_ratio={removal_ratio:.2%} force_ai={force_ai}"
    )

    if _TYPE_B_NO_MASK:
        # ── TYPE B: pre-fill removal area → full image (no mask) → gpt ─────────
        # Pre-fill the removal mask area (product, text, decorative_panel) with
        # estimated natural background BEFORE sending to AI.
        # Without this, dark decorative panels remain in the image and the AI
        # treats them as "natural background" → black area in scene_plate.
        natural_region = (proj_rem_arr <= 128) & (inverted_arr > 128)
        if natural_region.any():
            natural_pixels = original_arr[natural_region]
            r = natural_pixels[:, 0].astype(np.float32)
            g = natural_pixels[:, 1].astype(np.float32)
            b = natural_pixels[:, 2].astype(np.float32)
            pix_max = np.maximum(np.maximum(r, g), b)
            pix_min = np.minimum(np.minimum(r, g), b)
            brightness = pix_max / 255.0
            saturation = np.where(pix_max > 0, (pix_max - pix_min) / pix_max, 0.0)
            bg_only = (brightness > 0.75) & (saturation < 0.20)
            if bg_only.sum() >= 100:
                type_b_bg = np.median(natural_pixels[bg_only], axis=0).astype(np.uint8)
                prefill_source = "bg_only"
            else:
                type_b_bg = np.median(natural_pixels, axis=0).astype(np.uint8)
                prefill_source = "all_natural"
        else:
            type_b_bg = bg_estimate
            prefill_source = "fallback_bg_estimate"

        prefilled_arr = original_arr.copy()
        prefilled_arr[proj_rem_arr > 128] = type_b_bg
        type_b_input = Image.fromarray(prefilled_arr, "RGB")

        prefill_path = stage_dir / "type_b_prefill.png"
        type_b_input.save(str(prefill_path))
        logger.artifact_written(
            STAGE.value, str(prefill_path),
            f"TYPE B pre-filled input (bg={type_b_bg.tolist()} src={prefill_source})",
        )

        removal_ai, fail_code, fail_reason = openai_cleanup.cleanup_no_mask(
            original_image=type_b_input,
            target_width=target_width,
            target_height=target_height,
            api_key=api_key,
            prompt=CLEANUP_PROMPT_TYPE_B,
        )
        if removal_ai is None:
            return _fail(logger, fail_code, fail_reason)
        source_cleanup_api_calls = 1

        removal_ai_path = stage_dir / "source_cleanup_ai.png"
        removal_ai.save(str(removal_ai_path))
        logger.artifact_written(STAGE.value, str(removal_ai_path),
                                "TYPE B AI removal result (pre-filled, no mask)")

    elif bg_std < _UNIFORM_BG_STD_THRESHOLD and not force_ai:
        # ── TYPE A direct fill: uniform background, no API call ──────────────
        removal_arr_rgb = original_arr.copy()
        removal_arr_rgb[proj_rem_arr > 128] = bg_estimate
        removal_ai = Image.fromarray(removal_arr_rgb, "RGB")

        direct_path = stage_dir / "source_cleanup_direct.png"
        removal_ai.save(str(direct_path))
        logger.artifact_written(STAGE.value, str(direct_path),
                                f"direct fill bg={bg_estimate.tolist()} std={bg_std:.1f}")
        removal_ai_path = stage_dir / "source_cleanup_ai.png"
        removal_ai.save(str(removal_ai_path))
        logger.artifact_written(STAGE.value, str(removal_ai_path),
                                "removal result (direct fill — no API call)")
    else:
        # ── TYPE A AI cleanup: complex or dark background ─────────────────────
        natural_region = (proj_rem_arr <= 128) & (inverted_arr > 128)
        if natural_region.any():
            natural_pixels = original_arr[natural_region]

            r = natural_pixels[:, 0].astype(np.float32)
            g = natural_pixels[:, 1].astype(np.float32)
            b = natural_pixels[:, 2].astype(np.float32)
            pix_max = np.maximum(np.maximum(r, g), b)
            pix_min = np.minimum(np.minimum(r, g), b)
            brightness = pix_max / 255.0
            saturation = np.where(pix_max > 0, (pix_max - pix_min) / pix_max, 0.0)
            bg_only_mask = (brightness > 0.75) & (saturation < 0.20)

            if bg_only_mask.sum() >= 100:
                natural_bg = np.median(natural_pixels[bg_only_mask], axis=0).astype(np.uint8)
                prefill_source = "bg_only"
            else:
                natural_bg = np.median(natural_pixels, axis=0).astype(np.uint8)
                prefill_source = "all_natural"
        else:
            natural_bg = bg_estimate
            prefill_source = "fallback_bg_estimate"

        extended_removal = proj_removal.filter(
            ImageFilter.MaxFilter(size=2 * _AI_CONTEXT_BUFFER_PX + 1)
        )
        extended_arr = np.array(extended_removal, dtype=np.uint8) > 128

        prefilled_arr = original_arr.copy()
        prefilled_arr[extended_arr] = natural_bg
        projected_image_for_ai = Image.fromarray(prefilled_arr, "RGB")

        logger.artifact_written(
            STAGE.value, "(memory) ai_context_prefill",
            f"natural_bg={natural_bg.tolist()} source={prefill_source} buffer={_AI_CONTEXT_BUFFER_PX}px",
        )

        prefill_path = stage_dir / "source_cleanup_prefill.png"
        projected_image_for_ai.save(str(prefill_path))
        logger.artifact_written(STAGE.value, str(prefill_path),
                                "pre-filled image sent to gpt-image-1")

        removal_ai, fail_code, fail_reason = openai_cleanup.cleanup(
            projected_image=projected_image_for_ai,
            projected_removal_mask=proj_removal,
            target_width=target_width,
            target_height=target_height,
            api_key=api_key,
            prompt=CLEANUP_PROMPT,
        )
        if removal_ai is None:
            return _fail(logger, fail_code, fail_reason)
        source_cleanup_api_calls = 1

        removal_ai_path = stage_dir / "source_cleanup_ai.png"
        removal_ai.save(str(removal_ai_path))
        logger.artifact_written(STAGE.value, str(removal_ai_path),
                                "AI removal result (target canvas)")

    # ── Step 3: Restore protected-subject pixels from original_projection ────
    # TYPE B (_SKIP_SUBJECT_RESTORE=True): use AI output directly — no pixel
    # boundary, no seam.  Subject may be slightly altered; acceptable for prototype.
    # TYPE A (_SKIP_SUBJECT_RESTORE=False): restore original pixels with soft blend.
    if _SKIP_SUBJECT_RESTORE:
        clean_projection = removal_ai
        logger.artifact_written(STAGE.value, "(memory) step3_restore",
                                "SKIPPED (TYPE B): using AI output directly, no restore boundary")
    else:
        clean_projection = immutable_pixel_restorer.restore(
            removal_ai, original_projection, proj_restore, blend_radius=20
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

    if has_letterbox and not _TYPE_B_NO_MASK:
        # TYPE A only: letterbox was left black after masked inpaint → fill via outpaint.
        # TYPE B skips this: the full-canvas AI call already generated content for
        # the letterbox strips. Running outpaint again on TYPE B creates a seam at
        # the letterbox boundary (x=offset_x) where two different AI outputs are stitched.
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
        if has_letterbox and _TYPE_B_NO_MASK:
            logger.artifact_written(
                STAGE.value, "(memory) outpaint_skip",
                "SKIPPED (TYPE B): full-canvas AI already filled letterbox regions — no seam needed",
            )

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
    total_api_call_count = source_cleanup_api_calls + outpaint_api_call_count

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
        source_cleanup_api_call_count=source_cleanup_api_calls,
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
