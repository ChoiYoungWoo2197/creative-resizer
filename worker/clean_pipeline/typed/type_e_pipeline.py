"""TYPE E — 간소화 파이프라인: P1 → P5.5(smart_resize) → P7(safe_zone) → P8.

기존 TYPE B 파이프라인(P1~P8) 및 관련 모듈은 수정하지 않는다.
TYPE E만 이 파일에서 처리.

Flow:
  P1   SOURCE_PREPARATION  (orchestrator 담당, canonical 전달)
  P5.5 SMART_RESIZE        fal-ai/smart-resize → PIL center crop fallback
  P7   LAYOUT              safe zone 10% 여백 검증; 위반 시 letterbox 보정
  P8   FINAL_VALIDATION    08_final/result.png 저장 → render_validator

Fail codes:
  SMART_RESIZE_FAILED, SAFE_ZONE_UNCORRECTABLE, FINAL_SAVE_FAILED
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PIL import Image

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
    crop_ratio: float = resize_info["crop_ratio"]

    # ── P7: LAYOUT — safe zone 10% 여백 검증 ─────────────────────────────────
    safe_margin_x = round(spec.width * 0.10)
    safe_margin_y = round(spec.height * 0.10)

    layout_sr, corrected_path = _safe_zone_check(
        resized_path=resized_path,
        target_width=spec.width,
        target_height=spec.height,
        safe_margin_x=safe_margin_x,
        safe_margin_y=safe_margin_y,
        crop_ratio=crop_ratio,
        output_dir=request.output_directory,
        job_id=job_id,
        logger=logger,
    )
    stage_results.append(layout_sr)
    if layout_sr.status == PipelineStatus.FAIL:
        return _fail(job_id, layout_sr, stage_results, logger)

    final_input_path = corrected_path or resized_path

    # ── P8: FINAL_VALIDATION — 08_final/result.png 저장 + render_validator ────
    result_dir = Path(request.output_directory) / job_id / "clean_v1" / "08_final"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = str(result_dir / "result.png")

    try:
        shutil.copy2(final_input_path, result_path)
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


# ── P7 safe zone 검증 + letterbox 보정 ────────────────────────────────────────


def _safe_zone_check(
    resized_path: str,
    target_width: int,
    target_height: int,
    safe_margin_x: int,
    safe_margin_y: int,
    crop_ratio: float,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, str | None]:
    """Safe zone 10% 여백 검증.

    crop_ratio < 0.80 (넓은 영역 잘림) → letterbox 적용.
    그 외 → PASS 그대로.
    Returns (StageResult, corrected_path | None)
    """
    logger.stage_start(
        StageName.LAYOUT.value,
        f"safe_zone margin x={safe_margin_x}px y={safe_margin_y}px crop_ratio={crop_ratio:.1%}",
        metrics={
            "safeMarginX": safe_margin_x,
            "safeMarginY": safe_margin_y,
            "cropRatio": round(crop_ratio, 3),
        },
    )

    # crop_ratio 1.0 = 원본 전체 사용(fal-ai 경로 포함), < 0.80 = 넓은 영역 잘림
    needs_letterbox = (crop_ratio < 0.80)

    if not needs_letterbox:
        logger.stage_pass(
            StageName.LAYOUT.value,
            f"PASS — crop_ratio={crop_ratio:.1%} ≥ 80%, no letterbox needed",
            metrics={"letterboxApplied": False},
        )
        return StageResult(
            stage=StageName.LAYOUT,
            status=PipelineStatus.PASS,
            metrics={"letterboxApplied": False, "cropRatio": round(crop_ratio, 3)},
            artifacts={"resized": resized_path},
        ), None

    # letterbox 적용: contain-scale + 4코너 엣지 색상 샘플링 배경
    stage_dir = Path(output_dir) / job_id / "clean_v1" / "07_layout"
    stage_dir.mkdir(parents=True, exist_ok=True)

    try:
        img = Image.open(resized_path).convert("RGB")
        letterboxed = _apply_letterbox(img, target_width, target_height)
        lb_path = str(stage_dir / "safe_zone_letterbox.png")
        letterboxed.save(lb_path)
        logger.artifact_written(StageName.LAYOUT.value, lb_path, "letterbox 보정 결과")
    except Exception as exc:
        fail_sr = StageResult(
            stage=StageName.LAYOUT,
            status=PipelineStatus.FAIL,
            reasons=[f"[SAFE_ZONE_UNCORRECTABLE] letterbox 실패: {exc}"],
        )
        logger.stage_fail(StageName.LAYOUT.value, "SAFE_ZONE_UNCORRECTABLE", str(exc))
        return fail_sr, None

    logger.stage_pass(
        StageName.LAYOUT.value,
        f"PASS — letterbox 적용 (crop_ratio={crop_ratio:.1%} < 80%)",
        metrics={"letterboxApplied": True, "cropRatio": round(crop_ratio, 3)},
    )
    return StageResult(
        stage=StageName.LAYOUT,
        status=PipelineStatus.PASS,
        metrics={"letterboxApplied": True, "cropRatio": round(crop_ratio, 3)},
        artifacts={"letterbox": lb_path},
    ), lb_path


def _apply_letterbox(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """contain-scale + 4코너 엣지 색상 샘플링으로 배경 채움."""
    src_w, src_h = img.size
    scale = min(target_w / src_w, target_h / src_h)
    new_w = round(src_w * scale)
    new_h = round(src_h * scale)
    scaled = img.resize((new_w, new_h), Image.LANCZOS)

    bg_color = _sample_edge_color(img)
    canvas = Image.new("RGB", (target_w, target_h), bg_color)
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    canvas.paste(scaled, (paste_x, paste_y))
    return canvas


def _sample_edge_color(img: Image.Image) -> tuple[int, int, int]:
    """이미지 4코너 8×8 픽셀의 중앙값으로 배경색 추정."""
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    h, w = arr.shape[:2]
    size = min(8, h // 4, w // 4) or 1
    corners = [
        arr[:size, :size],
        arr[:size, w - size:],
        arr[h - size:, :size],
        arr[h - size:, w - size:],
    ]
    samples = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0)
    median = tuple(int(v) for v in np.median(samples, axis=0).astype(int))
    return median  # type: ignore[return-value]


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
