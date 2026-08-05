"""Pixel-level bounding box refinement for GPT-4o coordinate outputs.

GPT-4o의 정규화 좌표(0~1000)는 이미지 전체 레이아웃 파악에 탁월하지만
수식 변환 과정에서 AI 특유의 미세한 오차(~5~20px)가 발생한다.
이 모듈은 객체 유형에 따라 OpenCV 알고리즘으로 좌표를 픽셀 단위로 정밀화한다.

알고리즘:
  PRODUCT   → Canny Edge Detection + Morphological Closing
              에지를 먼저 따낸 뒤 팽창+침식으로 끊긴 외곽선을 메우고
              그 덩어리의 최소 사각형 좌표를 구한다.

  TEXT/LOGO → Otsu's Thresholding + findNonZero + boundingRect
              크롭 영역 내에서 글자/로고 픽셀과 배경 픽셀을 분리한 뒤
              비-영 픽셀의 최소 바운딩 박스를 정확하게 좁힌다.

  fallback  → 원본 픽셀 좌표를 그대로 반환 (cv2 실패 또는 객체 미탐지 시)

Public API
----------
refine(image_rgb, x1, y1, x2, y2, role) -> tuple[int, int, int, int]
    정밀화된 (x1, y1, x2, y2) 픽셀 좌표를 반환한다.

scan_product_bottom(image_pil, x1, y1, x2, current_y2, image_height) -> int
    GPT가 라벨 텍스트 하단에서 끊은 product y2를 실제 용기 바닥까지 확장한다.
    배경이 어두운 이미지 전용 (밝기 임계값 기반).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

_PADDING = 20          # ROI 크롭 시 여유 픽셀 (에지 검출 정확도 향상)
_CANNY_LOW = 30

# ── scan_product_bottom 전용 상수 ─────────────────────────────────────────────
_SCAN_BRIGHTNESS = 30    # 배경(검은색)과 구분하는 밝기 임계값
_SCAN_DARK_ROWS  = 5     # 연속 어두운 행이 이 수 이상이면 용기 끝으로 판단
_SCAN_MAX_EXPAND = 300   # 최대 하향 탐색 픽셀 (무한 확장 방지)
_SCAN_CENTER_PCT = 0.60  # 제품 가로 중심부 비율 (에지 노이즈 제거용)
_CANNY_HIGH = 100
_MORPH_ITER = 2        # Morphological closing 반복 횟수

_TEXT_ROLES = frozenset({"title_group", "body_text_group", "cta_group", "badge", "logo"})


def refine(
    image_pil: Image.Image,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    role: str,
) -> tuple[int, int, int, int]:
    """픽셀 정밀화. 실패 시 원본 좌표 그대로 반환."""
    try:
        import cv2
    except ImportError:
        return x1, y1, x2, y2

    img = np.array(image_pil.convert("RGB"))
    H, W = img.shape[:2]

    # 1단계: 패딩 포함 ROI 크롭
    rx1 = max(0, x1 - _PADDING)
    ry1 = max(0, y1 - _PADDING)
    rx2 = min(W, x2 + _PADDING)
    ry2 = min(H, y2 + _PADDING)

    roi = img[ry1:ry2, rx1:rx2]
    if roi.size == 0:
        return x1, y1, x2, y2

    # 2단계: 역할별 정밀화
    try:
        if role in _TEXT_ROLES:
            ex1, ey1, ex2, ey2 = _refine_text(roi, cv2)
        else:
            ex1, ey1, ex2, ey2 = _refine_product(roi, cv2)
    except Exception:
        return x1, y1, x2, y2

    if ex1 is None:
        return x1, y1, x2, y2

    # 3단계: ROI 내 상대 좌표 → 원본 이미지 절대 좌표
    abs_x1 = max(0, rx1 + ex1)
    abs_y1 = max(0, ry1 + ey1)
    abs_x2 = min(W, rx1 + ex2)
    abs_y2 = min(H, ry1 + ey2)

    # 결과 좌표가 유효한 경우에만 교체 (정밀화 후 면적이 너무 작아지면 원본 유지)
    if abs_x2 - abs_x1 < 4 or abs_y2 - abs_y1 < 4:
        return x1, y1, x2, y2

    # Canny/Otsu가 실제 객체 일부만 잡아 원본보다 >25% 좁힐 경우 원본 유지.
    # e.g. 연한 색상 용기처럼 배경과 유사한 제품은 에지가 일부만 잡혀 bbox가 축소됨.
    orig_w = x2 - x1
    orig_h = y2 - y1
    if orig_w > 0 and (abs_x2 - abs_x1) < orig_w * 0.75:
        return x1, y1, x2, y2
    if orig_h > 0 and (abs_y2 - abs_y1) < orig_h * 0.75:
        return x1, y1, x2, y2

    return abs_x1, abs_y1, abs_x2, abs_y2


def scan_product_bottom(
    image_pil: Image.Image,
    x1: int,
    y1: int,
    x2: int,
    current_y2: int,
    image_height: int,
) -> int:
    """픽셀 스캔으로 실제 용기 바닥 y2를 반환.

    GPT는 용기 표면의 라벨 텍스트(AZULENE, Yadah 등)를 보고
    해당 텍스트 하단을 용기 끝으로 잘못 판단한다.
    current_y2 아래를 하향 스캔하여 연속된 어두운 행이 나타나는 직전을
    실제 용기 바닥으로 확정한다.

    배경이 어두운 이미지 전용: 밝기 > _SCAN_BRIGHTNESS 를 제품 픽셀로 판단.
    """
    img = np.array(image_pil.convert("RGB"))
    H, W = img.shape[:2]

    # 제품 가로 중심부만 스캔 (좌우 에지·그림자 노이즈 제거)
    cx      = (x1 + x2) // 2
    half_w  = max(1, int((x2 - x1) * _SCAN_CENTER_PCT / 2))
    scan_x1 = max(0, cx - half_w)
    scan_x2 = min(W, cx + half_w)

    if scan_x1 >= scan_x2:
        return current_y2

    scan_start  = max(0, current_y2)
    scan_cap    = min(H, current_y2 + _SCAN_MAX_EXPAND)
    dark_count  = 0
    last_bright = current_y2 - 1

    for row_y in range(scan_start, scan_cap):
        row_max = int(img[row_y, scan_x1:scan_x2].max())
        if row_max > _SCAN_BRIGHTNESS:
            last_bright = row_y
            dark_count  = 0
        else:
            dark_count += 1
            if dark_count >= _SCAN_DARK_ROWS:
                break

    if last_bright < current_y2:
        return current_y2  # 스캔 구간에 밝은 픽셀 없음 → 원본 유지

    return min(last_bright + 1, image_height)


# ── 역할별 알고리즘 ────────────────────────────────────────────────────────────


def _refine_text(roi: np.ndarray, cv2) -> tuple[int | None, int | None, int | None, int | None]:
    """Otsu 이진화 + findNonZero + boundingRect."""
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    points = cv2.findNonZero(thresh)
    if points is None:
        return None, None, None, None
    ex, ey, ew, eh = cv2.boundingRect(points)
    return ex, ey, ex + ew, ey + eh


def _refine_product(roi: np.ndarray, cv2) -> tuple[int | None, int | None, int | None, int | None]:
    """Canny Edge Detection + Morphological Closing + boundingRect."""
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, _CANNY_LOW, _CANNY_HIGH)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=_MORPH_ITER)

    points = cv2.findNonZero(closed)
    if points is None:
        return None, None, None, None
    ex, ey, ew, eh = cv2.boundingRect(points)
    return ex, ey, ex + ew, ey + eh
