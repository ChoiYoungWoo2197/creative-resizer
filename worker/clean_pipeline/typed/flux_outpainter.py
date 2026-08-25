"""TYPE G P3 보조 — safe zone 기반 Cover Scale + fal-ai/flux-2-pro/outpaint.

bg 레이어를 세이프존 크기 대비 BG_SAFE_ZONE_SCALE(기본 0.90) 비율로 Cover 축소한 뒤
fal-ai/flux-2-pro/outpaint로 나머지 영역을 AI 생성한다.
FAL_KEY 없거나 API 실패 시 PIL edge-fill(코너 평균색) fallback.

환경변수:
  BG_SAFE_ZONE_SCALE  (기본 0.90) — 세이프존 대비 축소 비율 (예: 0.85, 0.90, 1.0)
  FAL_KEY             — fal-ai API 키
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

STAGE = StageName.BG_EXTRACTION
_FAL_ENDPOINT = "fal-ai/flux-2-pro/outpaint"
_SAFE_ZONE_SCALE = float(os.environ.get("BG_SAFE_ZONE_SCALE", "0.90"))


def outpaint(
    bg_path: str,
    target_width: int,
    target_height: int,
    safe_top: int,
    safe_right: int,
    safe_bottom: int,
    safe_left: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, dict | None]:
    """bg를 세이프존 기준 Cover 축소 후 flux-2-pro/outpaint로 배경 확장.

    Returns (StageResult, {"resized_path": str, "is_smart_resized": bool} | None)
    """
    fal_key = os.environ.get("FAL_KEY", "")

    # ── 1. Cover Scale 계산 ────────────────────────────────────────────────────
    safe_w = target_width  - safe_left - safe_right
    safe_h = target_height - safe_top  - safe_bottom

    if safe_w <= 0 or safe_h <= 0:
        return _fail(logger, "SAFE_ZONE_INVALID",
                     f"safe_zone 크기 계산 오류: safe_w={safe_w} safe_h={safe_h}")

    scale = _SAFE_ZONE_SCALE
    scaled_w = max(1, round(safe_w * scale))
    scaled_h = max(1, round(safe_h * scale))

    # ── 2. bg를 Cover 방식으로 scaled 크기에 맞게 리사이즈 ──────────────────────
    try:
        bg_img = Image.open(bg_path).convert("RGB")
    except Exception as exc:
        return _fail(logger, "BG_LOAD_FAILED", f"bg 이미지 로드 실패: {exc}")

    cover_factor = max(scaled_w / bg_img.width, scaled_h / bg_img.height)
    cover_w = max(1, round(bg_img.width  * cover_factor))
    cover_h = max(1, round(bg_img.height * cover_factor))
    cover_img = bg_img.resize((cover_w, cover_h), Image.LANCZOS)

    # center crop to (scaled_w, scaled_h)
    crop_x = (cover_w - scaled_w) // 2
    crop_y = (cover_h - scaled_h) // 2
    scaled_img = cover_img.crop((crop_x, crop_y, crop_x + scaled_w, crop_y + scaled_h))

    # ── 3. Outpaint padding 계산 (safe zone 중심 정렬) ─────────────────────────
    # safe zone 중심점에 스케일된 이미지를 맞춤 → 비대칭 safe zone 자동 반영
    center_safe_x = safe_left + safe_w / 2
    center_safe_y = safe_top  + safe_h / 2

    offset_x = round(center_safe_x - scaled_w / 2)
    offset_y = round(center_safe_y - scaled_h / 2)

    pad_left   = offset_x
    pad_top    = offset_y
    pad_right  = target_width  - scaled_w - offset_x
    pad_bottom = target_height - scaled_h - offset_y

    logger.artifact_written(
        STAGE.value, "(outpaint-calc)",
        f"scale={scale} scaled={scaled_w}x{scaled_h} "
        f"pad L={pad_left} T={pad_top} R={pad_right} B={pad_bottom}",
    )

    # ── 4. fal-ai/flux-2-pro/outpaint 또는 PIL fallback ─────────────────────
    result_img: Image.Image | None = None
    is_smart_resized = False
    needs_outpaint = pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0

    if fal_key and needs_outpaint:
        try:
            result_img = _call_fal_outpaint(
                scaled_img, pad_top, pad_right, pad_bottom, pad_left,
                target_width, target_height, fal_key, logger,
            )
            is_smart_resized = True
        except Exception as exc:
            logger.artifact_written(
                STAGE.value, "(fal-warn)",
                f"flux-2-pro/outpaint 실패 ({exc}) — PIL edge-fill fallback",
            )
    else:
        reason = "FAL_KEY 없음" if not fal_key else "패딩 불필요"
        logger.artifact_written(STAGE.value, "(fal-skip)", f"{reason} — PIL edge-fill fallback")

    if result_img is None:
        result_img = _pil_edge_fill(
            scaled_img, pad_top, pad_right, pad_bottom, pad_left,
            target_width, target_height,
        )

    # ── 5. 저장 ───────────────────────────────────────────────────────────────
    stage_dir = Path(output_dir) / job_id / "clean_v1" / "03_bg_extraction"
    stage_dir.mkdir(parents=True, exist_ok=True)
    resized_path = str(stage_dir / "smart_resized.png")
    result_img.save(resized_path)

    logger.artifact_written(
        STAGE.value, resized_path,
        f"{'flux-2-pro/outpaint' if is_smart_resized else 'PIL edge-fill'} 결과 "
        f"{target_width}x{target_height}",
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "safeZoneScale": scale,
            "scaledW": scaled_w,
            "scaledH": scaled_h,
            "padLeft": pad_left,
            "padTop": pad_top,
            "padRight": pad_right,
            "padBottom": pad_bottom,
            "isSmartResized": is_smart_resized,
        },
        artifacts={"resized": resized_path},
    ), {
        "resized_path": resized_path,
        "is_smart_resized": is_smart_resized,
    }


# ── fal-ai 호출 ───────────────────────────────────────────────────────────────


def _call_fal_outpaint(
    img: Image.Image,
    pad_top: int,
    pad_right: int,
    pad_bottom: int,
    pad_left: int,
    target_width: int,
    target_height: int,
    fal_key: str,
    logger: PipelineLogger,
) -> Image.Image:
    import fal_client
    os.environ.setdefault("FAL_KEY", fal_key)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    image_url = fal_client.upload(buf.read(), content_type="image/png")

    t0 = time.time()
    result = fal_client.subscribe(
        _FAL_ENDPOINT,
        arguments={
            "image_url": image_url,
            "top":    pad_top,
            "right":  pad_right,
            "bottom": pad_bottom,
            "left":   pad_left,
            "output_format": "png",
        },
    )
    elapsed = round(time.time() - t0, 2)

    url = _extract_url(result)
    if not url:
        raise RuntimeError("flux-2-pro/outpaint: 응답에 URL 없음")

    logger.artifact_written(
        STAGE.value, "(fal-ok)",
        f"flux-2-pro/outpaint OK elapsed={elapsed}s target={target_width}x{target_height}",
    )

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=60, context=ctx) as resp:
        raw = resp.read()

    result_img = Image.open(io.BytesIO(raw)).convert("RGB")
    if result_img.size != (target_width, target_height):
        result_img = result_img.resize((target_width, target_height), Image.LANCZOS)
    return result_img


def _extract_url(result: dict) -> str:
    images = result.get("images") or []
    if images:
        return images[0].get("url", "")
    return result.get("url", "") or (result.get("image") or {}).get("url", "")


# ── PIL fallback ──────────────────────────────────────────────────────────────


def _pil_edge_fill(
    img: Image.Image,
    pad_top: int,
    pad_right: int,
    pad_bottom: int,
    pad_left: int,
    target_width: int,
    target_height: int,
) -> Image.Image:
    """스케일된 이미지를 패딩 위치에 붙이고 나머지를 코너 평균색으로 채움."""
    edge_color = _sample_corner_color(img)
    canvas = Image.new("RGB", (target_width, target_height), edge_color)
    canvas.paste(img, (pad_left, pad_top))
    return canvas


def _sample_corner_color(img: Image.Image) -> tuple[int, int, int]:
    """이미지 4개 코너 픽셀의 평균색 반환."""
    w, h = img.size
    corners = [
        img.getpixel((0,     0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0,     h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    r = sum(c[0] for c in corners) // 4
    g = sum(c[1] for c in corners) // 4
    b = sum(c[2] for c in corners) // 4
    return (r, g, b)


# ── Helper ────────────────────────────────────────────────────────────────────


def _fail(logger: PipelineLogger, code: str, message: str) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
