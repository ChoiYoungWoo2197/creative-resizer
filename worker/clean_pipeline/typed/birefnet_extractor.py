"""TYPE F P2: fal-ai/birefnet 전경 추출 → product_cutout.png (RGBA).

fal-ai/birefnet:
  배경을 제거하고 피사체만 투명 RGBA PNG으로 반환.
  FAL_KEY 없으면 FAIL (pass-through 없음 — 전경 추출은 필수 단계).

Outputs under output/{jobId}/clean_v1/02_fg_extraction/:
  product_cutout.png  (RGBA, 투명 배경)
"""
from __future__ import annotations

import io
import os
import ssl
import time
import urllib.request
from pathlib import Path

from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.FOREGROUND_EXTRACTION
_OUTPUT_SUBDIR = Path("clean_v1") / "02_fg_extraction"
_FAL_ENDPOINT = "fal-ai/birefnet"


def extract(
    canonical_path: str,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, dict | None]:
    """fal-ai/birefnet 전경 추출.

    Returns (StageResult, result_dict):
      result_dict = {
        "cutout_path": str,     # RGBA PNG 경로
        "width": int,
        "height": int,
        "fal_meta": dict,
      }
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    fal_key = os.environ.get("FAL_KEY", "")

    logger.stage_start(
        STAGE.value,
        f"birefnet foreground extraction — fal={'set' if fal_key else 'missing'}",
        metrics={"hasFalKey": bool(fal_key), "endpoint": _FAL_ENDPOINT},
    )

    if not fal_key:
        return _fail(logger, "FAL_KEY_MISSING", "FAL_KEY 환경변수 없음 — birefnet 실행 불가")

    try:
        canonical = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open canonical: {exc}")

    src_w, src_h = canonical.size

    try:
        cutout_img, fal_meta = _call_birefnet(canonical, fal_key)
    except Exception as exc:
        return _fail(logger, "BIREFNET_FAILED", f"fal-ai/birefnet 호출 실패: {exc}")

    cutout_path = str(stage_dir / "product_cutout.png")
    cutout_img.save(cutout_path)

    logger.artifact_written(
        STAGE.value, cutout_path,
        f"birefnet RGBA cutout {src_w}×{src_h} ({fal_meta['durationSec']}s "
        f"requestId={fal_meta.get('requestId', 'n/a')})",
    )
    logger.stage_pass(
        STAGE.value,
        f"PASS — product_cutout.png 생성 ({cutout_img.size[0]}×{cutout_img.size[1]})",
        metrics={"srcWidth": src_w, "srcHeight": src_h, "fal": fal_meta},
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={"srcWidth": src_w, "srcHeight": src_h, "fal": fal_meta},
        artifacts={"cutout": cutout_path},
    ), {
        "cutout_path": cutout_path,
        "width": cutout_img.size[0],
        "height": cutout_img.size[1],
        "fal_meta": fal_meta,
    }


# ── fal-ai 호출 ───────────────────────────────────────────────────────────────


def _call_birefnet(img: Image.Image, fal_key: str) -> tuple[Image.Image, dict]:
    """fal-ai/birefnet 호출. (RGBA PIL Image, fal_meta dict) 반환."""
    import fal_client
    os.environ.setdefault("FAL_KEY", fal_key)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    image_url = fal_client.upload(buf.read(), content_type="image/png")

    request_id_holder: dict = {}

    def on_queue_update(update):
        if isinstance(update, fal_client.InProgress):
            if hasattr(update, "request_id") and update.request_id:
                request_id_holder["id"] = update.request_id
            for log in update.logs:
                print(log["message"])

    t0 = time.time()
    result = fal_client.subscribe(
        _FAL_ENDPOINT,
        arguments={"image_url": image_url, "model": "General Use (Light)"},
        with_logs=True,
        on_queue_update=on_queue_update,
    )
    elapsed = round(time.time() - t0, 2)

    url = _extract_url(result)
    if not url:
        raise RuntimeError("fal-ai/birefnet: 응답에 URL 없음")

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=60, context=ctx) as resp:
        raw = resp.read()

    result_img = Image.open(io.BytesIO(raw)).convert("RGBA")

    img_meta = result.get("image") or {}
    fal_meta = {
        "endpoint": _FAL_ENDPOINT,
        "requestId": request_id_holder.get("id"),
        "durationSec": elapsed,
        "outputUrl": url,
        "outputWidth": img_meta.get("width", result_img.size[0]),
        "outputHeight": img_meta.get("height", result_img.size[1]),
    }
    return result_img, fal_meta


def _extract_url(result: dict) -> str:
    """birefnet 응답에서 이미지 URL 추출.

    응답 구조: { "image": { "url": "...", "width": ..., "height": ... } }
    또는 images 배열 형태.
    """
    img = result.get("image") or {}
    if img.get("url"):
        return img["url"]
    images = result.get("images") or []
    if images:
        return images[0].get("url", "")
    return result.get("url", "")


# ── Helper ────────────────────────────────────────────────────────────────────


def _fail(logger: PipelineLogger, code: str, message: str) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
