"""TYPE G — PSD 레이어 기반 분석 파이프라인: P1 → P2.

기존 TYPE B 파이프라인은 수정하지 않는다.
TYPE G P1→P2까지만 구현 (P3 이후는 별도 구현 예정).

Flow:
  P1  SOURCE_PREPARATION  orchestrator 담당, canonical 전달
  P2  ELEMENT_ANALYSIS    psd-tools 레이어 트리 읽기
                          → 02_psd_layers/layers.json

산출물:
  02_psd_layers/layers.json
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
    api_key: str = "",
) -> CleanPipelineResult:
    """TYPE G 파이프라인 실행 (P1→P2). stage_results에 P1 결과가 포함돼 있어야 한다."""
    job_id = request.job_id
    out_dir = request.output_directory

    # ── P2: ELEMENT_ANALYSIS — PSD 레이어 트리 읽기 ──────────────────────────
    from clean_pipeline.psd import psd_layer_reader

    sr, result = psd_layer_reader.read_layers(
        psd_path=request.source_path,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)

    logger.job_pass(
        f"TYPE G P1→P2 complete — layers={result['layer_count']} layers_json={result['layers_json_path']}",
        metrics={
            "layerCount": result["layer_count"],
            "layersJsonPath": result["layers_json_path"],
        },
    )

    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.PASS,
        stage_results=stage_results,
        output_paths=[result["layers_json_path"]],
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
