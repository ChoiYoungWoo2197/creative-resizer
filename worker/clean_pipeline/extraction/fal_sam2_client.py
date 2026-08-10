"""SAM2 cloud segmentation via fal.ai — product 픽셀 마스크 생성.

product bbox 영역을 padding 포함해 crop한 뒤 SAM2에 전달.
crop 좌표계 내 box prompt를 지정하여 제품만 픽셀 단위로 세그먼트.

엔드포인트: fal-ai/sam2/image
입력: product bbox crop 이미지 + 크롭 내 box 프롬프트 [x1, y1, x2, y2]
출력: L mode(Grayscale) 이진 마스크 (흰=전경, 검=배경, 원본 캔버스 크기)

NOTE: 전체 이미지 + 절대 픽셀 box 방식은 SAM2가 box를 무시하고
      dominant foreground(인물 등)를 세그먼트하는 문제가 있음.
      crop 방식으로 좌표 ambiguity를 원천 제거.
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
# crop 시 bbox 주변 여백 (픽셀) — 컨텍스트 제공용
_CROP_PADDING = 50


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

    # 1. product bbox 영역을 padding 포함해 crop
    #    전체 이미지 + 절대 픽셀 box 방식은 SAM2가 box를 무시하고
    #    dominant foreground(인물)를 세그먼트하는 문제가 있으므로
    #    crop으로 좌표 ambiguity를 원천 제거.
    cx1 = max(0, x1 - _CROP_PADDING)
    cy1 = max(0, y1 - _CROP_PADDING)
    cx2 = min(img_w, x2 + _CROP_PADDING)
    cy2 = min(img_h, y2 + _CROP_PADDING)
    crop_img = image.crop((cx1, cy1, cx2, cy2))
    crop_w, crop_h = crop_img.size

    # crop 좌표계 내 box (crop origin 기준 상대 좌표)
    bx1 = x1 - cx1
    by1 = y1 - cy1
    bx2 = x2 - cx1
    by2 = y2 - cy1

    try:
        import fal_client
        result = fal_client.subscribe(
            _ENDPOINT,
            arguments={
                "image_url": _to_data_uri(crop_img.convert("RGB")),
                "prompts": [{"type": "box", "box": [bx1, by1, bx2, by2]}],
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
        # crop 크기로 리사이즈 (이진 마스크이므로 NEAREST)
        crop_mask = raw.convert("L").resize((crop_w, crop_h), Image.NEAREST)
    except SAM2Error:
        raise
    except Exception as exc:
        raise SAM2Error(f"[SAM2] Mask download failed: {exc}") from exc

    # 3. crop 마스크를 원본 캔버스 크기로 복원 (crop 위치에 paste)
    full_mask = Image.new("L", (img_w, img_h), 0)
    full_mask.paste(crop_mask, (cx1, cy1))
    return full_mask


def _to_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"
