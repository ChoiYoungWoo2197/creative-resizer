"""TYPE G — 트랙별 독립 분석 파이프라인: P1 → P2.

기존 TYPE B 파이프라인은 수정하지 않는다.
TYPE G P1→P2까지만 구현 (P3 이후는 별도 구현 예정).

Flow:
  P1  SOURCE_PREPARATION  orchestrator 담당, canonical 전달
  P2  ELEMENT_ANALYSIS    GPT-4o 트랙별(product/text/person) 독립 분석
                          + PIL crop + fal-ai/birefnet RGBA 추출
                          → manifest.json + track별 cutout PNG

산출물:
  02_element_analysis/manifest.json
  02_element_analysis/product_cutout.png  (RGBA, detected 시)
  02_element_analysis/person_cutout.png   (RGBA, detected 시)
  02_element_analysis/product_raw.json
  02_element_analysis/text_raw.json
  02_element_analysis/person_raw.json
"""
from __future__ import annotations

import os

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
    api_key: str = "",
) -> CleanPipelineResult:
    """TYPE G 파이프라인 실행 (P1→P2). stage_results에 P1 결과가 포함돼 있어야 한다."""
    job_id = request.job_id
    out_dir = request.output_directory

    resolved_key = (
        api_key
        or os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("BACKGROUND_AI_API_KEY", "")
    )

    # ── P2: ELEMENT_ANALYSIS — 트랙별 독립 GPT-4o + birefnet ─────────────────
    from clean_pipeline.analysis import element_analyzer

    sr, result = element_analyzer.analyze(
        canonical_path=canonical.canonical_path,
        image_width=canonical.width,
        image_height=canonical.height,
        source_sha256=canonical.sha256,
        api_key=resolved_key,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)

    logger.job_pass(
        f"TYPE G P1→P2 complete — manifest={result['manifest_path']}",
        metrics={
            "manifestPath": result["manifest_path"],
            "productCutout": result["product_cutout"],
            "personCutout": result["person_cutout"],
        },
    )

    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.PASS,
        stage_results=stage_results,
        output_paths=[result["manifest_path"]],
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
