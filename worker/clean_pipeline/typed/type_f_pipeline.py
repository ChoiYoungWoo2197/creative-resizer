"""TYPE F (TYPE B Light) — fal-ai 경량 파이프라인: P1 → P2 → P3 → P4 → P5.

기존 TYPE B 파이프라인(P1~P8) 및 관련 모듈은 수정하지 않는다.
TYPE F만 이 파일에서 처리.

Flow:
  P1  SOURCE_PREPARATION     orchestrator 담당, canonical 전달
  P2  BACKGROUND_CLEANUP     fal-ai/object-removal → clean_canonical.png
                             (실패 시 canonical pass-through)
  P3  FOREGROUND_EXTRACTION  fal-ai/birefnet(clean_canonical) → product_cutout.png (RGBA)
                             clean_canonical 입력으로 텍스트 아티팩트 0% 보장
  P4  SMART_RESIZE           fal-ai/smart-resize → scene_plate.png
                             (clean_canonical 기준, PIL center crop fallback)
  P5  COMPOSITE_SAFEZONE     PIL alpha_composite → 08_final/result.png
                             (MongoDB slug 기반 safe zone 배치)

Fail codes:
  BIREFNET_FAILED, FAL_KEY_MISSING, SCENE_LOAD_FAILED,
  CUTOUT_LOAD_FAILED, FINAL_SAVE_FAILED
"""
from __future__ import annotations

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
    """TYPE F 파이프라인 실행. stage_results에 P1 결과가 이미 포함돼 있어야 한다."""
    job_id = request.job_id
    out_dir = request.output_directory

    # ── P2: BACKGROUND_CLEANUP — fal-ai/object-removal ───────────────────────
    # birefnet 전에 텍스트·오버레이 제거 → 전경 마스크 아티팩트 방지
    from clean_pipeline.typed import object_remover

    sr, clean_canonical_path = object_remover.clean(
        canonical_path=canonical.canonical_path,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)

    # ── P3: FOREGROUND_EXTRACTION — fal-ai/birefnet(clean_canonical) ─────────
    # clean_canonical 입력으로 텍스트 아티팩트 없는 순수 전경 추출
    from clean_pipeline.typed import birefnet_extractor

    sr, fg_info = birefnet_extractor.extract(
        canonical_path=clean_canonical_path,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)

    cutout_path: str = fg_info["cutout_path"]

    # ── P4: SMART_RESIZE — fal-ai/smart-resize (clean_canonical → scene_plate)
    from clean_pipeline.typed import smart_resizer

    sr, resize_info = smart_resizer.resize(
        canonical_path=clean_canonical_path,
        target_width=spec.width,
        target_height=spec.height,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)

    scene_plate_path: str = resize_info["resized_path"]

    # ── P5: COMPOSITE_SAFEZONE — PIL alpha_composite ──────────────────────────
    from clean_pipeline.typed import safezone_compositor
    from clean_pipeline.render import render_validator

    sr, result_path = safezone_compositor.composite(
        scene_plate_path=scene_plate_path,
        cutout_path=cutout_path,
        target_width=spec.width,
        target_height=spec.height,
        safe_left=spec.safe_left,
        safe_top=spec.safe_top,
        safe_right=spec.safe_right,
        safe_bottom=spec.safe_bottom,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)

    # ── render_validator ──────────────────────────────────────────────────────
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
        f"TYPE F result.png validated: {result_path}",
        metrics={"resultPath": result_path},
    )
    logger.job_pass(
        f"TYPE F job complete — result={result_path}",
        metrics={"outputCount": 1},
    )
    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.PASS,
        stage_results=stage_results,
        output_paths=[result_path],
    )


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
