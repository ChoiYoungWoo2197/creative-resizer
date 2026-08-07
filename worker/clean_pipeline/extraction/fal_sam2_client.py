"""SAM2 cloud segmentation via fal.ai — product bbox crop 후 픽셀 마스크 생성.

전략 B: 전체 이미지가 아닌 product bbox 영역만 crop하여 SAM2에 전달.
crop 이미지 기준 마스크를 원본 캔버스 크기 좌표에 복원하여 L mode PIL Image 반환.

엔드포인트: fal-ai/sam2/image
입력: crop 이미지 data URI + 전체 box 프롬프트 [0, 0, crop_w, crop_h]
출력: RGBA PNG → alpha 채널 → L mode mask → 원본 캔버스에 bbox 위치 복원
"""
from __future__ import annotations

import base64
import io
import os
from typing import Optional

import requests
from PIL import Image

_FAL_KEY = os.environ.get("FAL_KEY", "")
_ENDPOINT = "fal-ai/sam2/image"
# bbox가 캔버스 이 비율 이상이면 SAM2가 의미 있는 분리를 할 수 없음
_CANVAS_COVERAGE_THRESHOLD = 0.95


class SAM2Error(Exception):
    pass


def extract_sam2_mask(
    image: Image.Image,
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
) -> Optional[Image.Image]:
    """product bbox 영역 crop 후 SAM2로 픽셀 마스크 생성.

    Returns:
        L mode PIL Image (원본 캔버스 크기) — 흰색=전경, 검은색=배경
        None — FAL_KEY 미설정 시 (로컬 환경, SAM2 skip)
    Raises:
        SAM2Error — API 실패 또는 bbox가 캔버스 전체인 경우
    """
    if not _FAL_KEY:
        return None

    img_w, img_h = image.size

    # bbox가 캔버스 전체를 덮으면 SAM2가 전경/배경을 구분할 수 없음 → P2 bbox 문제 명확히 알림
    if bbox_w >= img_w * _CANVAS_COVERAGE_THRESHOLD and bbox_h >= img_h * _CANVAS_COVERAGE_THRESHOLD:
        raise SAM2Error(
            f"[SAM2] product bbox ({bbox_w}x{bbox_h}) covers entire canvas "
            f"({img_w}x{img_h}) — P2 GPT bbox quality problem"
        )

    # 1. bbox 영역 crop (경계 clamp 포함)
    crop_box = (
        max(0, bbox_x),
        max(0, bbox_y),
        min(img_w, bbox_x + bbox_w),
        min(img_h, bbox_y + bbox_h),
    )
    cropped = image.crop(crop_box).convert("RGB")
    crop_w, crop_h = cropped.size

    # 2. SAM2 호출 — crop 이미지 전체를 box 프롬프트로 전달
    try:
        import fal_client
        result = fal_client.subscribe(
            _ENDPOINT,
            arguments={
                "image_url": _to_data_uri(cropped),
                "prompts": [{"type": "box", "box": [0, 0, crop_w, crop_h]}],
            },
        )
    except SAM2Error:
        raise
    except Exception as exc:
        raise SAM2Error(f"[SAM2] fal.ai call failed: {exc}") from exc

    # 3. RGBA PNG URL → alpha 채널(L mode) 추출
    try:
        img_info = result.get("image") or {}
        url = img_info.get("url", "")
        if not url:
            raise SAM2Error(f"[SAM2] No image URL in fal.ai response: {result}")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        rgba = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        _, _, _, crop_alpha = rgba.split()
        # SAM2 응답 크기가 crop 크기와 다를 수 있으므로 리사이즈
        crop_mask = crop_alpha.resize((crop_w, crop_h), Image.LANCZOS)
    except SAM2Error:
        raise
    except Exception as exc:
        raise SAM2Error(f"[SAM2] Mask download failed: {exc}") from exc

    # 4. 원본 캔버스 크기 검은 배경에 bbox 위치(crop_box 좌상단)에 crop 마스크 붙이기
    full_mask = Image.new("L", (img_w, img_h), 0)
    full_mask.paste(crop_mask, (crop_box[0], crop_box[1]))
    return full_mask


def _to_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"
