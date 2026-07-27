"""SCENE_VALIDATION stage: deterministic checks → AI validation → verdict.

Fail codes (any one → FAIL):
  Deterministic:
    SCENE_PLATE_DECODE_FAILED, TARGET_SIZE_MISMATCH, SOLID_COLOR_IMAGE,
    RESTORE_PIXELS_MISMATCH, PROJECTION_LOAD_FAILED
  AI:
    AD_OBJECTS_REMAINING, PROTECTED_SUBJECT_DAMAGED, VISIBLE_SEAM_DETECTED,
    DUPLICATED_FRAGMENTS_DETECTED, NEW_TEXT_GENERATED, LOW_NATURALNESS_SCORE,
    AI_RESPONSE_PARSE_FAILED, API_ERROR, NO_API_KEY, EMPTY_RESPONSE

AI is called exactly once. Deterministic failure short-circuits before AI call.
"""
from __future__ import annotations

from pathlib import Path

from worker.clean_pipeline.analysis.models import SceneManifest
from worker.clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.scene.models import ScenePlateResult
from worker.clean_pipeline.validation import (
    deterministic_scene_validator,
    openai_scene_validator,
)
from worker.clean_pipeline.validation.scene_models import SceneValidationResult

STAGE = StageName.SCENE_VALIDATION
_NATURALNESS_THRESHOLD = 0.70
_OUTPUT_SUBDIR = Path("clean_v1") / "06_validation"


def validate(
    scene_plate_result: ScenePlateResult,
    canonical_path: str,
    manifest: SceneManifest,
    api_key: str,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, SceneValidationResult | None]:

    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"scene_plate={scene_plate_result.scene_plate_path} "
        f"target={scene_plate_result.target_width}×{scene_plate_result.target_height}",
        metrics={
            "targetWidth": scene_plate_result.target_width,
            "targetHeight": scene_plate_result.target_height,
        },
    )

    # ── Deterministic checks ─────────────────────────────────────────────────
    det = deterministic_scene_validator.validate(
        scene_plate_path=scene_plate_result.scene_plate_path,
        source_projection_path=scene_plate_result.source_projection_path,
        projected_restore_mask_path=scene_plate_result.projected_restore_mask_path,
        target_width=scene_plate_result.target_width,
        target_height=scene_plate_result.target_height,
    )

    if not det.passed:
        return _fail(
            logger,
            det.fail_code,
            det.reason,
            SceneValidationResult(
                job_id=job_id,
                passed=False,
                fail_codes=[det.fail_code],
                deterministic=det,
                ai=None,
            ),
            stage_dir,
        )

    # ── AI validation — exactly 1 call ───────────────────────────────────────
    ai, fail_code, fail_reason = openai_scene_validator.validate(
        canonical_path=canonical_path,
        source_projection_path=scene_plate_result.source_projection_path,
        scene_plate_path=scene_plate_result.scene_plate_path,
        manifest=manifest,
        api_key=api_key,
        logger=logger,
        stage=STAGE.value,
    )

    if ai is None:
        return _fail(
            logger,
            fail_code,
            fail_reason,
            SceneValidationResult(
                job_id=job_id,
                passed=False,
                fail_codes=[fail_code],
                deterministic=det,
                ai=None,
            ),
            stage_dir,
        )

    # ── Evaluate AI result fields ────────────────────────────────────────────
    fail_codes = _eval_ai(ai)
    overall_pass = len(fail_codes) == 0

    result_obj = SceneValidationResult(
        job_id=job_id,
        passed=overall_pass,
        fail_codes=fail_codes,
        deterministic=det,
        ai=ai,
    )

    validation_json_path = stage_dir / "validation.json"
    validation_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.validation_json_path = str(validation_json_path)
    logger.artifact_written(STAGE.value, str(validation_json_path), "validation summary")

    if not overall_pass:
        reasons = [f"[{c}]" for c in fail_codes]
        logger.stage_fail(STAGE.value, "|".join(fail_codes), "; ".join(reasons))
        return StageResult(
            stage=STAGE,
            status=PipelineStatus.FAIL,
            reasons=reasons,
            metrics={
                "naturalness": ai.scene_naturalness_score,
                "apiCallCount": ai.api_call_count,
            },
            artifacts={"validation_json": str(validation_json_path)},
        ), result_obj

    logger.stage_pass(
        STAGE.value,
        f"scene plate validated: naturalness={ai.scene_naturalness_score:.2f}",
        metrics={
            "naturalness": ai.scene_naturalness_score,
            "apiCallCount": ai.api_call_count,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "naturalness": ai.scene_naturalness_score,
            "apiCallCount": ai.api_call_count,
        },
        artifacts={"validation_json": str(validation_json_path)},
    ), result_obj


# ── Helpers ───────────────────────────────────────────────────────────────────


def _eval_ai(ai) -> list[str]:
    """Return list of fail codes derived from AI result fields."""
    codes: list[str] = []
    if ai.ad_objects_remaining:
        codes.append("AD_OBJECTS_REMAINING")
    if not ai.protected_subject_preserved:
        codes.append("PROTECTED_SUBJECT_DAMAGED")
    if ai.visible_seam_detected:
        codes.append("VISIBLE_SEAM_DETECTED")
    if ai.duplicated_fragments_detected:
        codes.append("DUPLICATED_FRAGMENTS_DETECTED")
    if ai.newly_generated_text_detected:
        codes.append("NEW_TEXT_GENERATED")
    if ai.scene_naturalness_score < _NATURALNESS_THRESHOLD:
        codes.append("LOW_NATURALNESS_SCORE")
    return codes


def _fail(
    logger: PipelineLogger,
    code: str,
    message: str,
    result_obj: SceneValidationResult,
    stage_dir: Path,
) -> tuple[StageResult, SceneValidationResult]:
    validation_json_path = stage_dir / "validation.json"
    validation_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.validation_json_path = str(validation_json_path)

    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
        artifacts={"validation_json": str(validation_json_path)},
    ), result_obj
