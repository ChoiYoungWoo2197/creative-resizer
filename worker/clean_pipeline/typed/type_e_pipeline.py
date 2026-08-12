"""TYPE E — 간소화 파이프라인: P1 → P5.5(smart_resize) → P7(safe_zone) → P8.

기존 TYPE B 파이프라인(P1~P8) 및 관련 모듈은 수정하지 않는다.
TYPE E만 이 파일에서 처리.

Flow:
  P1   SOURCE_PREPARATION  (orchestrator 담당, canonical 전달)
  P5.5 SMART_RESIZE        fal-ai/smart-resize → PIL center crop fallback
  P7   LAYOUT              safe zone 정보 기록 (MongoDB slug 기반, spec에서 읽음)
  P8   FINAL_VALIDATION    08_final/result.png 저장 → render_validator

Fail codes:
  CANONICAL_LOAD_FAILED, FINAL_SAVE_FAILED
"""
from __future__ import annotations

import shutil
from pathlib import Path

from clean_pipeline.contracts import (
    CleanPipelineRequest,
    CleanPipelineResult,
    PipelineStatus,
    StageName,
    StageResult,
    TargetSpec,
)
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.source.models import CanonicalSource


def run(
    request: CleanPipelineRequest,
    spec: TargetSpec,
    canonical: CanonicalSource,
    stage_results: list[StageResult],
    logger: PipelineLogger,
) -> CleanPipelineResult:
    """TYPE E 파이프라인 실행. stage_results에 P1 결과가 이미 포함돼 있어야 한다."""
    job_id = request.job_id

    # ── P5.5: SMART_RESIZE ────────────────────────────────────────────────────
    from clean_pipeline.typed import smart_resizer

    sr, resize_info = smart_resizer.resize(
        canonical_path=canonical.canonical_path,
        target_width=spec.width,
        target_height=spec.height,
        output_dir=request.output_directory,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)

    resized_path: str = resize_info["resized_path"]

    # ── P7: LAYOUT — MongoDB slug 기반 safe zone 정보 기록 ────────────────────
    sr, corrected_path = _safe_zone_check(
        resized_path=resized_path,
        target_width=spec.width,
        target_height=spec.height,
        safe_left=spec.safe_left,
        safe_top=spec.safe_top,
        safe_right=spec.safe_right,
        safe_bottom=spec.safe_bottom,
        output_dir=request.output_directory,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)
    if corrected_path:
        resized_path = corrected_path

    # ── P8: FINAL_VALIDATION — 08_final/result.png 저장 + render_validator ────
    result_dir = Path(request.output_directory) / job_id / "clean_v1" / "08_final"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = str(result_dir / "result.png")

    try:
        shutil.copy2(resized_path, result_path)
    except Exception as exc:
        fail_sr = StageResult(
            stage=StageName.FINAL_VALIDATION,
            status=PipelineStatus.FAIL,
            reasons=[f"[FINAL_SAVE_FAILED] {exc}"],
        )
        stage_results.append(fail_sr)
        return _fail(job_id, fail_sr, stage_results, logger)

    logger.artifact_written(
        StageName.FINAL_VALIDATION.value, result_path, "TYPE E result.png 저장"
    )

    from clean_pipeline.render import render_validator

    is_valid, val_code, val_reason = render_validator.validate(
        result_path, spec.width, spec.height
    )
    final_sr = StageResult(
        stage=StageName.FINAL_VALIDATION,
        status=PipelineStatus.PASS if is_valid else PipelineStatus.FAIL,
        reasons=[] if is_valid else [f"[{val_code}] {val_reason}"],
        artifacts={"result": result_path},
    )
    stage_results.append(final_sr)
    if not is_valid:
        logger.stage_fail(StageName.FINAL_VALIDATION.value, val_code, val_reason)
        return _fail(job_id, final_sr, stage_results, logger)

    logger.stage_pass(
        StageName.FINAL_VALIDATION.value,
        f"TYPE E result.png validated: {result_path}",
        metrics={"resultPath": result_path},
    )

    logger.job_pass(
        f"TYPE E job complete — result={result_path}",
        metrics={"outputCount": 1},
    )
    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.PASS,
        stage_results=stage_results,
        output_paths=[result_path],
    )


# ── P7 safe zone 헬퍼 ────────────────────────────────────────────────────────


def _safe_zone_check(
    resized_path: str,
    target_width: int,
    target_height: int,
    safe_left: int,
    safe_top: int,
    safe_right: int,
    safe_bottom: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, str | None]:
    """MongoDB slug 기반 safe zone 정보를 기록한다.

    spec.safe_left/top/right/bottom은 request_adapter가 banner_spec_lookup을 통해
    MongoDB에서 조회한 값이다. TYPE E는 이미지를 수정하지 않고 메타데이터만 기록한다.
    Returns (StageResult, None) — 이미지 경로 변경 없음.
    """
    has_safe_zone = (safe_left + safe_top + safe_right + safe_bottom) > 0
    safe_metrics = {
        "safeLeft": safe_left,
        "safeTop": safe_top,
        "safeRight": safe_right,
        "safeBottom": safe_bottom,
        "hasSafeZone": has_safe_zone,
    }

    logger.stage_start(
        StageName.LAYOUT.value,
        f"safe_zone left={safe_left} top={safe_top} right={safe_right} bottom={safe_bottom}",
        metrics=safe_metrics,
    )

    logger.stage_pass(
        StageName.LAYOUT.value,
        f"PASS — safe zone {'recorded' if has_safe_zone else 'not configured for this spec'}",
        metrics=safe_metrics,
    )
    return StageResult(
        stage=StageName.LAYOUT,
        status=PipelineStatus.PASS,
        metrics=safe_metrics,
        artifacts={"resized": resized_path},
    ), None


# ── Helper ────────────────────────────────────────────────────────────────────


def _fail(
    job_id: str,
    failed_sr: StageResult,
    stage_results: list[StageResult],
    logger: PipelineLogger,
) -> CleanPipelineResult:
    code = failed_sr.reasons[0] if failed_sr.reasons else "UNKNOWN"
    logger.job_fail(code, f"Stage {failed_sr.stage.value} failed")
    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.FAIL,
        stage_results=stage_results,
        failure_code=code,
        failure_message=f"Stage {failed_sr.stage.value} failed",
    )
