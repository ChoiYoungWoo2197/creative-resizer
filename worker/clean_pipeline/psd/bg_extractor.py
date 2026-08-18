"""TYPE G P3 — bg 레이어 추출 + fal-ai/smart-resize 배경 확장.

PSD에서 role=bg 레이어를 composite으로 추출하고
fal-ai/smart-resize(실패 시 PIL center crop fallback)로 타겟 규격에 맞게 확장한다.

타겟 규격은 요청의 spec.width/spec.height에서 가져오며 하드코딩하지 않는다.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.psd.psd_layer_reader import LayerInfo

STAGE = StageName.BG_EXTRACTION
_OUTPUT_SUBDIR = Path("clean_v1") / "03_bg_extraction"


def extract_and_resize(
    psd_path: str,
    layers: list[LayerInfo],
    target_width: int,
    target_height: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, dict | None]:
    """bg 레이어 추출 후 타겟 규격으로 확장.

    Returns (StageResult, result_dict):
      result_dict = {"bg_path": str, "resized_path": str, "is_smart_resized": bool}
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"bg extract + resize -> {target_width}x{target_height} src={os.path.basename(psd_path)}",
    )

    # ── 1. layers에서 role=bg 레이어 이름 찾기 ────────────────────────────────
    bg_layer_name = next((l.name for l in layers if l.role == "bg"), None)
    if not bg_layer_name:
        return _fail(logger, "BG_LAYER_NOT_FOUND", "role=bg 레이어 없음 — bg 그룹 또는 레이어명 확인 필요")

    # ── 2. psd-tools로 bg 레이어 composite ──────────────────────────────────
    bg_img = _composite_bg_layer(psd_path, bg_layer_name)
    if bg_img is None:
        return _fail(logger, "BG_COMPOSITE_FAILED", f"bg 레이어({bg_layer_name!r}) composite 실패")

    bg_path = str(stage_dir / "bg.png")
    bg_img.save(bg_path)
    logger.artifact_written(
        STAGE.value, bg_path,
        f"bg 추출 완료 {bg_img.width}x{bg_img.height} layer={bg_layer_name!r}",
    )

    # ── 3. fal-ai/smart-resize (PIL center crop fallback) ────────────────────
    from clean_pipeline.typed import smart_resizer

    _, sr_result = smart_resizer.resize(
        canonical_path=bg_path,
        target_width=target_width,
        target_height=target_height,
        output_dir=output_dir,
        job_id=job_id,
        logger=logger,
    )

    if sr_result is None:
        return _fail(logger, "BG_RESIZE_FAILED", "smart-resize 실패")

    resized_path = sr_result["resized_path"]
    is_smart = sr_result["is_smart_resized"]

    logger.stage_pass(
        STAGE.value,
        f"bg extract+resize PASS {'(fal-ai)' if is_smart else '(PIL fallback)'} -> {resized_path}",
        metrics={
            "bgLayerName": bg_layer_name,
            "targetWidth": target_width,
            "targetHeight": target_height,
            "isSmartResized": is_smart,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "bgLayerName": bg_layer_name,
            "targetWidth": target_width,
            "targetHeight": target_height,
            "isSmartResized": is_smart,
        },
        artifacts={"bg": bg_path, "resized": resized_path},
    ), {
        "bg_path": bg_path,
        "resized_path": resized_path,
        "is_smart_resized": is_smart,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _composite_bg_layer(psd_path: str, bg_layer_name: str) -> Image.Image | None:
    """psd-tools로 bg_layer_name 레이어를 composite 추출 후 RGB Image 반환."""
    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(psd_path)
    except Exception:
        return None

    psd_w, psd_h = psd.width, psd.height

    def find_layer(parent, name: str):
        for layer in parent:
            if layer.name == name:
                return layer
            if layer.is_group():
                found = find_layer(layer, name)
                if found:
                    return found
        return None

    target = find_layer(psd, bg_layer_name)
    if target is None:
        return None

    try:
        img = target.composite()
    except Exception:
        return None

    if img is None:
        return None

    img = img.convert("RGB")
    if img.size == (psd_w, psd_h):
        return img

    # 레이어가 캔버스보다 작으면 올바른 위치에 붙임
    bbox = target.bbox  # (left, top, right, bottom)
    canvas = Image.new("RGB", (psd_w, psd_h), (0, 0, 0))
    canvas.paste(img, (bbox[0], bbox[1]))
    return canvas


def _fail(logger: PipelineLogger, code: str, message: str) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
