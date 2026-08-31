"""TYPE E P5.5 SMART_RESIZE: fal-ai/smart-resize API → PIL center crop fallback.

fal-ai/smart-resize:
  인물/피사체를 인식해 타겟 규격에 맞게 지능형 크롭/리프레이밍.
  FAL_KEY 없거나 API 실패 시 PIL Center Crop으로 graceful fallback.

Outputs under output/{jobId}/clean_v1/05.5_smart_resize/:
  smart_resized.png
"""
from __future__ import annotations

import io
import json
import os
import ssl
import time
import urllib.request
from pathlib import Path

from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.SMART_RESIZE
_OUTPUT_SUBDIR = Path("clean_v1") / "05.5_smart_resize"
_FAL_ENDPOINT = "fal-ai/smart-resize"


def resize(
    canonical_path: str,
    target_width: int,
    target_height: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, dict | None]:
    """fal-ai/smart-resize 호출, 실패 시 PIL center crop.

    Returns (StageResult, result_dict):
      result_dict = {
        "resized_path": str,
        "is_smart_resized": bool,   # True=fal-ai, False=PIL fallback
        "target_width": int,
        "target_height": int,
        "crop_ratio": float,        # PIL fallback 시 사용된 원본 비율 (1.0=전체)
      }
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    fal_key = os.environ.get("FAL_KEY", "")

    logger.stage_start(
        STAGE.value,
        f"target={target_width}×{target_height} fal={'set' if fal_key else 'missing'}",
        metrics={"targetWidth": target_width, "targetHeight": target_height, "hasFalKey": bool(fal_key)},
    )

    try:
        canonical = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open canonical: {exc}")

    src_w, src_h = canonical.size
    resized_img: Image.Image | None = None
    is_smart_resized = False

    # ── fal-ai/smart-resize 시도 ──────────────────────────────────────────────
    fal_meta: dict | None = None
    if fal_key:
        try:
            resized_img, fal_meta = _call_fal_smart_resize(canonical, target_width, target_height, fal_key)
            is_smart_resized = True
            logger.artifact_written(
                STAGE.value, "(fal-ok)",
                f"fal-ai/smart-resize OK {src_w}×{src_h} → {target_width}×{target_height} "
                f"({fal_meta['durationSec']}s requestId={fal_meta.get('requestId', 'n/a')})",
            )
        except Exception as exc:
            logger.artifact_written(
                STAGE.value, "(fal-warn)",
                f"fal-ai/smart-resize 실패 ({exc}) — PIL center crop fallback",
            )
    else:
        logger.artifact_written(STAGE.value, "(fal-skip)", "FAL_KEY 없음 — PIL center crop fallback")

    # ── PIL center crop fallback ───────────────────────────────────────────────
    crop_ratio = 1.0
    if resized_img is None:
        resized_img, crop_ratio = _pil_center_crop(canonical, target_width, target_height)
        logger.artifact_written(
            STAGE.value, "(pil-crop)",
            f"PIL center crop {src_w}×{src_h} → {target_width}×{target_height} "
            f"(원본 사용 비율 {crop_ratio:.1%})",
        )

    resized_path = str(stage_dir / "smart_resized.png")
    resized_img.save(resized_path)
    logger.artifact_written(
        STAGE.value, resized_path,
        f"{'fal-ai smart-resize' if is_smart_resized else 'PIL center crop'} 결과",
    )

    stage_metrics = {
        "isSmartResized": is_smart_resized,
        "cropRatio": round(crop_ratio, 3),
        "srcWidth": src_w,
        "srcHeight": src_h,
        "targetWidth": target_width,
        "targetHeight": target_height,
    }
    if fal_meta:
        stage_metrics["fal"] = fal_meta

    logger.stage_pass(
        STAGE.value,
        f"PASS {'(fal-ai)' if is_smart_resized else f'(PIL fallback crop_ratio={crop_ratio:.1%})'}",
        metrics=stage_metrics,
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics=stage_metrics,
        artifacts={"resized": resized_path},
    ), {
        "resized_path": resized_path,
        "is_smart_resized": is_smart_resized,
        "target_width": target_width,
        "target_height": target_height,
        "crop_ratio": crop_ratio,
        "fal_meta": fal_meta,
    }


# ── fal-ai 호출 ───────────────────────────────────────────────────────────────


def _call_fal_smart_resize(
    img: Image.Image,
    target_width: int,
    target_height: int,
    fal_key: str,
) -> tuple[Image.Image, dict]:
    """fal-ai/smart-resize REST 호출. (PIL Image, fal_meta dict) 반환.

    fal_meta 구조 (프론트 연동용):
      endpoint, requestId, status, durationSec, timings,
      outputUrl, outputWidth, outputHeight, targetSize
    """
    import fal_client
    os.environ.setdefault("FAL_KEY", fal_key)

    # 업로드 전 RGB 강제 변환 (RGBA 알파채널 제거)
    if img.mode != "RGB":
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    buf.seek(0)

    # fal storage에 업로드 → HTTPS URL 획득 (JPEG: PNG 대비 용량 50~70% 절감)
    image_url = fal_client.upload(buf.read(), content_type="image/jpeg")

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
        arguments={
            "prompt": "Soft background bokeh, shallow depth of field on edges, subtle ambient light, minimal negative space, high commercial quality, photorealistic. [DO NOT ADD: no text, no logos, no new objects, no extra people, no harsh seams]",
            "image_url": image_url,
            "resolution": "1K",
            "target_sizes": [f"{target_width}x{target_height}"],
            "output_format": "png",
            "safety_tolerance": "4",
            "num_images_per_size": 1,
        },
        with_logs=True,
        on_queue_update=on_queue_update,
    )
    elapsed = round(time.time() - t0, 2)

    url = _extract_url(result)
    if not url:
        raise RuntimeError("fal-ai/smart-resize: 응답에 URL 없음")

    images_meta = result.get("images") or []
    fal_meta = {
        "endpoint": _FAL_ENDPOINT,
        "requestId": request_id_holder.get("id"),
        "status": 200,
        "durationSec": elapsed,
        "timings": result.get("timings"),
        "outputUrl": url,
        "outputWidth": images_meta[0].get("width") if images_meta else target_width,
        "outputHeight": images_meta[0].get("height") if images_meta else target_height,
        "targetSize": f"{target_width}x{target_height}",
    }

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=60, context=ctx) as resp:
        raw = resp.read()

    result_img = Image.open(io.BytesIO(raw)).convert("RGB")
    if result_img.size != (target_width, target_height):
        result_img = result_img.resize((target_width, target_height), Image.LANCZOS)
    return result_img, fal_meta


def _extract_url(result: dict) -> str:
    """fal-ai/smart-resize 응답에서 이미지 URL 추출.

    응답 구조: { images: [ { url, width, height, content_type } ] }
    images는 target_sizes 개수만큼 존재하므로 첫 번째 사용.
    """
    images = result.get("images") or []
    if images:
        return images[0].get("url", "")
    return result.get("url", "")


def _call_via_urllib(endpoint: str, payload: dict, fal_key: str) -> str:
    """fal_client 없을 때 urllib으로 REST 직접 호출."""
    import urllib.error
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://fal.run/{endpoint}",
        data=body,
        headers={"Authorization": f"Key {fal_key}", "Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}"
        )
    return _extract_url(data)


# ── PIL fallback ──────────────────────────────────────────────────────────────


def _pil_center_crop(
    img: Image.Image,
    target_w: int,
    target_h: int,
) -> tuple[Image.Image, float]:
    """타겟 비율로 center crop 후 리사이즈. (결과 이미지, 원본 사용 비율) 반환."""
    src_w, src_h = img.size
    target_ar = target_w / target_h
    src_ar = src_w / src_h

    if src_ar > target_ar:
        crop_h = src_h
        crop_w = round(src_h * target_ar)
        x = (src_w - crop_w) // 2
        cropped = img.crop((x, 0, x + crop_w, crop_h))
        ratio = crop_w / src_w
    else:
        crop_w = src_w
        crop_h = round(src_w / target_ar)
        y = (src_h - crop_h) // 2
        cropped = img.crop((0, y, crop_w, y + crop_h))
        ratio = crop_h / src_h

    return cropped.resize((target_w, target_h), Image.LANCZOS), ratio


# ── Helper ────────────────────────────────────────────────────────────────────


def _fail(logger: PipelineLogger, code: str, message: str) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
