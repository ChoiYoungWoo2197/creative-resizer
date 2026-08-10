"""SAM2 cloud segmentation via fal.ai — product 픽셀 마스크 생성.

전체 이미지를 SAM2에 전달하고 product bbox를 box prompt로 지정.
SAM2가 이미지 전체 맥락에서 bbox 안의 주요 객체(제품)를 픽셀 단위로 세그먼트.

엔드포인트: fal-ai/sam2/image
입력: 전체 이미지 data URI + product bbox box 프롬프트 [x1, y1, x2, y2]
출력: L mode(Grayscale) 이진 마스크 (흰=전경, 검=배경, 원본 캔버스 크기)
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

    # bbox가 캔버스 전체를 덮으면 SAM2 box prompt 의미 없음 → P2 bbox 문제 명확히 노출
    if bbox_w >= img_w * _CANVAS_COVERAGE_THRESHOLD and bbox_h >= img_h * _CANVAS_COVERAGE_THRESHOLD:
        raise SAM2Error(
            f"[SAM2] product bbox ({bbox_w}x{bbox_h}) covers entire canvas "
            f"({img_w}x{img_h}) — P2 GPT bbox quality problem"
        )

    # bbox clamp
    x1 = max(0, bbox_x)
    y1 = max(0, bbox_y)
    x2 = min(img_w, bbox_x + bbox_w)
    y2 = min(img_h, bbox_y + bbox_h)

    # 1. 전체 이미지를 SAM2에 전달 + product bbox를 box prompt로 지정
    #    crop 후 전체 box를 주면 SAM2가 이미지 전체를 마스크로 반환하므로,
    #    전체 이미지에서 bbox 영역을 명시해야 제품만 세그먼트된다.
    try:
        import fal_client
        result = fal_client.subscribe(
            _ENDPOINT,
            arguments={
                "image_url": _to_data_uri(image.convert("RGB")),
                "prompts": [{"type": "box", "box": [x1, y1, x2, y2]}],
            },
        )
    except SAM2Error:
        raise
    except Exception as exc:
        raise SAM2Error(f"[SAM2] fal.ai call failed: {exc}") from exc

    # 2. 마스크 이미지 다운로드 → L mode 변환
    # fal-ai/sam2/image 응답은 RGBA PNG가 아닌 L mode(Grayscale) 또는 RGB 이진 마스크.
    # .convert("RGBA") 후 알파 채널을 꺼내면 A=255 전체 → 전체 흰색 버그 발생.
    # .convert("L")로 직접 변환하여 픽셀 밝기 값(0~255)을 마스크로 사용.
    try:
        img_info = result.get("image") or {}
        url = img_info.get("url", "")
        if not url:
            raise SAM2Error(f"[SAM2] No image URL in fal.ai response: {result}")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        raw = Image.open(io.BytesIO(resp.content))
        # 이진 마스크이므로 NEAREST 리사이즈로 흰/검 경계 유지
        full_mask = raw.convert("L").resize((img_w, img_h), Image.NEAREST)
    except SAM2Error:
        raise
    except Exception as exc:
        raise SAM2Error(f"[SAM2] Mask download failed: {exc}") from exc

    return full_mask


def _to_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"
