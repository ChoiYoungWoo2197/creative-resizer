"""SAM2 세그멘테이션 서비스 HTTP 클라이언트.

services/segmentation-ai (Flask 8090) 와 통신.
서비스가 없거나 오류 발생 시 None을 반환 → 호출자가 polygon fallback 처리.
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Optional

import requests
from PIL import Image


@dataclass
class Sam2Detection:
    role: str
    mask_png_base64: str        # PNG 마스크 base64
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    mask_quality_score: float   # 0~100

    def decode_mask(self) -> Image.Image:
        """base64 → PIL Image (L mode)."""
        data = base64.b64decode(self.mask_png_base64)
        return Image.open(io.BytesIO(data)).convert("L")


def query_sam2(
    service_url: str,
    image: Image.Image,
    prompts: list[dict],
    timeout: int = 30,
) -> list[Sam2Detection] | None:
    """SAM2 서비스에 세그멘테이션 요청.

    Args:
        service_url: 예) "http://creative-segmentation:8090"
        image: PIL RGB 이미지
        prompts: [{"role": "product", "texts": ["skincare jar"]}, ...]
        timeout: 요청 타임아웃(초)

    Returns:
        감지 결과 리스트. 서비스 미기동·오류 시 None.
    """
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)

    try:
        resp = requests.post(
            f"{service_url.rstrip('/')}/v1/segment",
            files={"image": ("input.png", buf, "image/png")},
            data={"prompts": json.dumps(prompts)},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.exceptions.ConnectionError:
        return None  # 서비스 미기동 — polygon fallback
    except requests.exceptions.Timeout:
        return None
    except Exception:
        return None

    detections: list[Sam2Detection] = []
    for det in payload.get("detections", []):
        bbox = det.get("bbox", {})
        detections.append(Sam2Detection(
            role=det.get("role", "unknown"),
            mask_png_base64=det.get("maskPngBase64") or det.get("mask_png_base64", ""),
            bbox_x=int(bbox.get("x", 0)),
            bbox_y=int(bbox.get("y", 0)),
            bbox_w=int(bbox.get("width", 0)),
            bbox_h=int(bbox.get("height", 0)),
            mask_quality_score=float(det.get("maskQualityScore") or det.get("mask_quality_score", 0)),
        ))
    return detections


def compute_iou(
    box_a: tuple[int, int, int, int],
    box_b: tuple[int, int, int, int],
) -> float:
    """두 [x, y, w, h] 박스의 IoU."""
    ax1, ay1, aw, ah = box_a
    bx1, by1, bw, bh = box_b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0

    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def best_match(
    gpt_bbox: tuple[int, int, int, int],
    detections: list[Sam2Detection],
    min_iou: float,
    min_quality: float,
) -> Sam2Detection | None:
    """GPT-4o bbox와 최고 IoU 일치 SAM2 감지 결과 반환.

    min_iou, min_quality 기준 미달 시 None 반환 → polygon fallback.
    """
    best: Optional[Sam2Detection] = None
    best_score = 0.0

    for det in detections:
        if det.mask_quality_score < min_quality:
            continue
        iou = compute_iou(gpt_bbox, (det.bbox_x, det.bbox_y, det.bbox_w, det.bbox_h))
        if iou >= min_iou and iou > best_score:
            best_score = iou
            best = det

    return best
