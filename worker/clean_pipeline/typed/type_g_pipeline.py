"""TYPE G — PSD 레이어 기반 분석 파이프라인: P1 → P2 → P3 → P4.

기존 TYPE B 파이프라인은 수정하지 않는다.

Flow:
  P1  SOURCE_PREPARATION  orchestrator 담당, PSD passthrough
  P2  ELEMENT_ANALYSIS    psd-tools 레이어 트리 읽기
                          → 02_psd_layers/layers.json
  P3  BG_EXTRACTION       bg 레이어 composite 추출
                          + fal-ai/smart-resize로 타겟 규격 확장
                          → 03_bg_extraction/bg.png + smart_resized.png
  P4  ELEMENT_COMPOSITE   비율 유지 투영 → 배경 위 합성
                          → 04_composite/result.png

산출물:
  02_psd_layers/layers.json
  03_bg_extraction/bg.png
  05.5_smart_resize/smart_resized.png
  04_composite/result.png
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

    # PSD를 한 번만 열어서 P2/P3/P4에 재사용 (4번 open → 1번)
    try:
        from psd_tools import PSDImage
        psd_obj = PSDImage.open(request.source_path)
    except Exception as exc:
        fail_sr = StageResult(
            stage=StageName.ELEMENT_ANALYSIS,
            status=PipelineStatus.FAIL,
            reasons=[f"[PSD_OPEN_FAILED] {exc}"],
        )
        return _fail(job_id, fail_sr, stage_results, logger)

    # ── P2: ELEMENT_ANALYSIS — PSD 레이어 트리 읽기 ──────────────────────────
    from clean_pipeline.psd import psd_layer_reader

    sr, result = psd_layer_reader.read_layers(
        psd_path=request.source_path,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
        psd=psd_obj,
    )
    stage_results.append(sr)
    if sr.status == PipelineStatus.FAIL:
        return _fail(job_id, sr, stage_results, logger)

    # ── P3: BG_EXTRACTION — bg 레이어 추출 + smart-resize ────────────────────
    from clean_pipeline.psd import bg_extractor

    sr_bg, bg_result = bg_extractor.extract_and_resize(
        psd_path=request.source_path,
        layers=result["layers"],
        target_width=spec.width,
        target_height=spec.height,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
        psd=psd_obj,
        safe_top=spec.safe_top,
        safe_right=spec.safe_right,
        safe_bottom=spec.safe_bottom,
        safe_left=spec.safe_left,
    )
    stage_results.append(sr_bg)
    if sr_bg.status == PipelineStatus.FAIL:
        return _fail(job_id, sr_bg, stage_results, logger)

    # ── P4: ELEMENT_COMPOSITE — 비율 유지 투영 → 배경 합성 ──────────────────────
    from clean_pipeline.psd import element_compositor

    sr_comp, comp_result = element_compositor.composite_elements(
        psd_path=request.source_path,
        layers=result["layers"],
        bg_path=bg_result["resized_path"],
        source_w=canonical.width,
        source_h=canonical.height,
        target_w=spec.width,
        target_h=spec.height,
        output_dir=out_dir,
        job_id=job_id,
        logger=logger,
        psd=psd_obj,
        sz_scaled_w=bg_result.get("sz_scaled_w"),
        sz_scaled_h=bg_result.get("sz_scaled_h"),
        sz_pad_left=bg_result.get("sz_pad_left"),
        sz_pad_top=bg_result.get("sz_pad_top"),
        safe_top=spec.safe_top,
        safe_right=spec.safe_right,
        safe_bottom=spec.safe_bottom,
        safe_left=spec.safe_left,
    )
    stage_results.append(sr_comp)
    if sr_comp.status == PipelineStatus.FAIL:
        return _fail(job_id, sr_comp, stage_results, logger)

    logger.job_pass(
        f"TYPE G P1→P4 complete — layers={result['layer_count']} result={comp_result['result_path']}",
        metrics={
            "layerCount": result["layer_count"],
            "resultPath": comp_result["result_path"],
            "isSmartResized": bg_result["is_smart_resized"],
        },
    )

    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.PASS,
        stage_results=stage_results,
        output_paths=[comp_result["result_path"]],
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
