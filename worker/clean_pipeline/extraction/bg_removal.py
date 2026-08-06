"""Background removal for extracted RGBA crops.

Used in P3 OBJECT_EXTRACTION when an object sits on a dark or achromatic
(white/grey) background.  Converts the polygon-masked RGBA crop so that
background pixels become transparent.

Two strategies, chosen by role:
  TEXT   (title_group, body_text_group, cta_group, badge, logo)
         Removes:
           • dark pixels (brightness < _KEEP_BRIGHTNESS) — solid dark panel bg
           • achromatic-bright pixels (saturation range < _ACHROMATIC_RANGE
             AND brightness > _BRIGHT_BG) — white/grey panel bg
         Keeps everything else (coloured text, dark-on-light body text).

  PRODUCT (product)
         Background-colour removal: sample the crop edges to estimate the
         background colour, then make pixels within _PRODUCT_BG_DIST colour
         distance of that colour transparent.  Handles products that may contain
         dark areas (jars, lids) which a pure brightness cut would erase.

Both strategies apply a Gaussian-blur soft edge (feather) so the composited
result blends naturally onto any new background.

Public API
----------
should_remove_background(crop_rgba) -> bool
    Returns True when the crop border contains dark OR achromatic-bright pixels.

remove_background(crop_rgba, role) -> Image.Image
    Returns a new RGBA image with background pixels made transparent.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

# ── Tuning constants ──────────────────────────────────────────────────────────

# Border band sampling: border pixels darker than this count as "dark".
_DARK_DETECT_THRESHOLD = 55          # 0-255 mean RGB

# Border band thickness and minimum dark/achromatic-pixel ratio to trigger bg removal.
_DARK_BORDER_WIDTH = 3               # px — top/bottom/left/right strip
_DARK_RATIO_THRESHOLD = 0.15         # 15 % of border pixels must qualify

# TEXT strategy: remove dark pixels below this brightness.
_KEEP_BRIGHTNESS = 50                # 0-255 mean RGB

# TEXT strategy: achromatic (white/grey) background detection.
# Pixels where max(R,G,B)-min(R,G,B) < _ACHROMATIC_RANGE  AND  brightness > _BRIGHT_BG
# are treated as achromatic background and made transparent.
_ACHROMATIC_RANGE = 30               # colour range; 0 = pure grey/white
_BRIGHT_BG = 120                     # minimum brightness to count as "bright background"

# PRODUCT strategy: colour distance from sampled bg → transparent.
_PRODUCT_BG_DIST = 40               # Euclidean distance in RGB space

# Feather radius applied after alpha thresholding (reduces hard edges).
_FEATHER_RADIUS = 1.5

# Vertical line filter: connected component width threshold.
# Components narrower than this (px) near the left border are removed.
_VERTICAL_LINE_MAX_WIDTH = 20

# Roles that use TEXT (brightness-threshold) strategy.
_TEXT_ROLES = frozenset({"title_group", "body_text_group", "cta_group", "badge", "logo"})


# tight_crop padding (px) — alpha 경계에서 약간의 여유 확보
_TIGHT_CROP_PADDING = 2


# ── Public ────────────────────────────────────────────────────────────────────


def should_remove_background(crop_rgba: Image.Image, role: str = "") -> bool:
    """Return True when the crop needs background removal.

    TEXT roles always need removal (글자 외 배경 제거가 목적이므로 배경 유형과 무관).
    Other roles: trigger on dark or achromatic-bright border pixels.
    """
    if role in _TEXT_ROLES:
        return True

    arr = np.array(crop_rgba.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    b = min(_DARK_BORDER_WIDTH, h // 2, w // 2)

    border_px = np.concatenate([
        arr[:b, :].reshape(-1, 3),
        arr[-b:, :].reshape(-1, 3),
        arr[:, :b].reshape(-1, 3),
        arr[:, -b:].reshape(-1, 3),
    ])
    brightness = border_px.mean(axis=1)
    sat_range = border_px.max(axis=1) - border_px.min(axis=1)

    dark_ratio = float((brightness < _DARK_DETECT_THRESHOLD).sum()) / len(border_px)
    achromatic_bright_ratio = float(
        ((sat_range < _ACHROMATIC_RANGE) & (brightness > _BRIGHT_BG)).sum()
    ) / len(border_px)

    return dark_ratio >= _DARK_RATIO_THRESHOLD or achromatic_bright_ratio >= _DARK_RATIO_THRESHOLD


def tight_crop(rgba: Image.Image) -> Image.Image:
    """alpha > 0인 픽셀의 tight bbox로 크롭. 상하좌우 빈 여백 제거."""
    arr = np.array(rgba)
    alpha = arr[:, :, 3]
    rows = np.any(alpha > 0, axis=1)
    cols = np.any(alpha > 0, axis=0)
    if not rows.any():
        return rgba
    h, w = arr.shape[:2]
    r_min, r_max = np.where(rows)[0][[0, -1]]
    c_min, c_max = np.where(cols)[0][[0, -1]]
    r_min = max(0, r_min - _TIGHT_CROP_PADDING)
    r_max = min(h - 1, r_max + _TIGHT_CROP_PADDING)
    c_min = max(0, c_min - _TIGHT_CROP_PADDING)
    c_max = min(w - 1, c_max + _TIGHT_CROP_PADDING)
    return rgba.crop((c_min, r_min, c_max + 1, r_max + 1))


def remove_background(crop_rgba: Image.Image, role: str) -> Image.Image:
    """Return crop_rgba with dark background pixels made transparent.

    Preserves the existing polygon alpha channel: pixels already transparent
    remain transparent regardless of strategy.
    """
    if role in _TEXT_ROLES:
        return _remove_text_bg(crop_rgba)
    return _remove_product_bg(crop_rgba)


# ── Strategies ────────────────────────────────────────────────────────────────


def _trim_bbox_by_projection(
    crop_img: Image.Image,
    edge_threshold: int = 50,
    min_density_ratio: float = 0.05,
) -> Image.Image:
    """Canny Edge + Projection Profile로 텍스트 밀집 영역만 inner-trim.

    P2 BBox가 마스카라 같은 인접 오브젝트를 포함해 과하게 잡혔을 때,
    Otsu 이진화 전에 먼저 호출해 실제 글자 영역으로 crop을 줄인다.
    cv2 없으면 원본 반환.
    """
    try:
        import cv2 as _cv2
    except ImportError:
        return crop_img

    img_np = np.array(crop_img)
    if img_np.ndim == 3:
        gray = _cv2.cvtColor(img_np, _cv2.COLOR_RGBA2GRAY if img_np.shape[2] == 4 else _cv2.COLOR_RGB2GRAY)
    else:
        gray = img_np.copy()

    h, w = gray.shape
    if h <= 10 or w <= 10:
        return crop_img

    edges = _cv2.Canny(gray, edge_threshold, edge_threshold * 2)

    y_profile = np.sum(edges > 0, axis=1)
    y_max = np.max(y_profile)
    if y_max == 0:
        return crop_img

    valid_y = np.where(y_profile > y_max * min_density_ratio)[0]
    if len(valid_y) == 0:
        return crop_img
    trim_y1, trim_y2 = int(valid_y[0]), int(valid_y[-1])

    x_profile = np.sum(edges[trim_y1:trim_y2 + 1, :] > 0, axis=0)
    x_max = np.max(x_profile)
    if x_max == 0:
        return crop_img

    valid_x = np.where(x_profile > x_max * min_density_ratio)[0]
    if len(valid_x) == 0:
        return crop_img
    trim_x1, trim_x2 = int(valid_x[0]), int(valid_x[-1])

    pad = 3
    fx1 = max(0, trim_x1 - pad)
    fy1 = max(0, trim_y1 - pad)
    fx2 = min(w, trim_x2 + 1 + pad)
    fy2 = min(h, trim_y2 + 1 + pad)

    if (fx2 - fx1) < 10 or (fy2 - fy1) < 10:
        return crop_img

    return crop_img.crop((fx1, fy1, fx2, fy2))


def _remove_text_bg(crop_rgba: Image.Image) -> Image.Image:
    """Otsu 이진화 기반 TEXT 배경 제거.

    Otsu가 히스토그램 분석으로 전경(글자)/배경 분리 최적 threshold를 자동 계산한다.
    어두운 배경 위 밝은 글자, 밝은 배경 위 어두운 글자 모두 자동 처리.
    cv2 없으면 brightness + saturation 방식으로 fallback.
    """
    try:
        import cv2 as _cv2
    except ImportError:
        return _remove_text_bg_fallback(crop_rgba)

    # Otsu 전에 Projection Profile로 텍스트 영역만 inner-trim (마스카라 등 인접 오브젝트 제거)
    crop_rgba = _trim_bbox_by_projection(crop_rgba)

    arr = np.array(crop_rgba, dtype=np.uint8)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]

    gray = _cv2.cvtColor(rgb, _cv2.COLOR_RGB2GRAY)

    # Otsu: 이미지마다 최적 threshold 자동 계산
    _, thresh = _cv2.threshold(gray, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)

    # 테두리 평균으로 배경이 밝은지/어두운지 판단
    # 이미지가 4px 이하일 때 2:-2 슬라이싱이 역전되는 문제 방지
    h, w = gray.shape
    b_pad = min(2, max(1, h // 4), max(1, w // 4))
    border_mask = np.zeros((h, w), dtype=bool)
    border_mask[:b_pad, :] = True
    border_mask[-b_pad:, :] = True
    border_mask[:, :b_pad] = True
    border_mask[:, -b_pad:] = True

    if gray[border_mask].mean() > 128:
        text_mask = (thresh == 0)    # 밝은 배경 → 어두운 글자 보존
    else:
        text_mask = (thresh == 255)  # 어두운 배경 → 밝은 글자 보존

    # 세로선 필터: crop 좌측 경계에 붙은 가늘고 긴 디자인 요소 제거
    text_mask_arr = np.where(text_mask, np.uint8(255), np.uint8(0))
    text_mask_arr = _filter_vertical_lines(text_mask_arr, h, w, _cv2)
    text_mask = text_mask_arr > 0

    new_alpha = np.where(text_mask & (alpha > 0), alpha, 0).astype(np.uint8)
    result = arr.copy()
    result[:, :, 3] = new_alpha
    return _feather(Image.fromarray(result, "RGBA"))


def _filter_vertical_lines(binary_mask: np.ndarray, img_h: int, img_w: int, cv2) -> np.ndarray:
    """Otsu 마스크에서 crop 좌측 경계에 붙은 가늘고 긴 세로선 제거.

    세로선 판정 조건 (3가지 모두 해당 시 제거):
      1) x 좌표가 crop 좌측 끝 근처 (x <= 15px)
      2) 높이가 crop 높이의 50% 이상
      3) 너비가 매우 가늚 (comp_w <= 10px)
    """
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary_mask)
    cleaned = binary_mask.copy()
    for i in range(1, num_labels):  # 0은 배경
        x      = stats[i, cv2.CC_STAT_LEFT]
        comp_w = stats[i, cv2.CC_STAT_WIDTH]
        comp_h = stats[i, cv2.CC_STAT_HEIGHT]
        is_near_left_border = (x <= 15)
        is_tall = (comp_h > img_h * 0.5)
        is_thin = (comp_w <= _VERTICAL_LINE_MAX_WIDTH)
        if is_near_left_border and is_tall and is_thin:
            cleaned[labels == i] = 0
    return cleaned


def _remove_text_bg_fallback(crop_rgba: Image.Image) -> Image.Image:
    """cv2 없을 때: brightness + saturation 방식 fallback."""
    arr = np.array(crop_rgba, dtype=np.uint8)
    rgb = arr[:, :, :3].astype(np.float32)
    existing_alpha = arr[:, :, 3]

    brightness = rgb.mean(axis=2)
    sat_range = rgb.max(axis=2) - rgb.min(axis=2)

    is_dark_bg = brightness < _KEEP_BRIGHTNESS
    is_achromatic_bright_bg = (sat_range < _ACHROMATIC_RANGE) & (brightness > _BRIGHT_BG)

    keep = ~(is_dark_bg | is_achromatic_bright_bg) & (existing_alpha > 0)
    new_alpha = np.where(keep, existing_alpha, 0).astype(np.uint8)
    result = arr.copy()
    result[:, :, 3] = new_alpha
    return _feather(Image.fromarray(result, "RGBA"))


def _remove_product_bg(crop_rgba: Image.Image) -> Image.Image:
    """Flood Fill + MORPH_CLOSE + Largest Blob strategy for product objects.

    1. Flood Fill from 4 corners: only pixels reachable from the dark outer
       boundary are removed → jar lid/shadow (not connected to corners) survive.
    2. MORPH_CLOSE: bridges thin gaps between jar body and lid so they form
       one connected blob instead of two separate ones.
    3. Largest Blob: removes stray text characters that survived step 1.
    """
    try:
        import cv2
    except ImportError:
        return _remove_product_bg_fallback(crop_rgba)

    arr = np.array(crop_rgba, dtype=np.uint8)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape

    # ── 1. Flood Fill: 모서리에서 연결된 어두운 외곽 배경만 제거 ─────────────
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    bg_filled = gray.copy()
    for seed_x, seed_y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if gray[seed_y, seed_x] < 60:
            cv2.floodFill(
                bg_filled, ff_mask, (seed_x, seed_y), 255,
                loDiff=30, upDiff=30,
                flags=4 | cv2.FLOODFILL_FIXED_RANGE,
            )
    is_outer_bg = ff_mask[1:-1, 1:-1] == 1

    new_alpha = alpha.copy()
    new_alpha[is_outer_bg] = 0

    # ── 2. MORPH_CLOSE: 뚜껑-본체 사이 미세 끊김 메우기 ─────────────────────
    binary = (new_alpha > 0).astype(np.uint8) * 255
    kernel_size = max(3, int(min(h, w) * 0.025))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    closed_binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    # Largest Blob을 적용하지 않음:
    # logo bbox가 product bbox와 겹칠 때 logo 픽셀이 "작은 덩어리"로 오인되어
    # 투명 처리되는 문제 방지. Flood Fill + MORPH_CLOSE로 외곽 배경 제거 충분.
    result = arr.copy()
    result[:, :, 3] = np.where(closed_binary > 0, new_alpha, 0).astype(np.uint8)
    return _feather(Image.fromarray(result, "RGBA"))


def _keep_largest_blob_from_binary(closed_binary: np.ndarray, original_alpha: np.ndarray) -> np.ndarray:
    """MORPH_CLOSE된 바이너리 기준으로 가장 큰 blob 영역만 original_alpha에서 복원."""
    try:
        import cv2
    except ImportError:
        return original_alpha
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(closed_binary, connectivity=8)
    if num_labels <= 1:
        return original_alpha
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    valid_mask = labels == largest
    return np.where(valid_mask, original_alpha, 0).astype(np.uint8)


def _remove_product_bg_fallback(crop_rgba: Image.Image) -> Image.Image:
    """cv2 없을 때 밝기 임계값 폴백."""
    arr = np.array(crop_rgba, dtype=np.uint8)
    rgb = arr[:, :, :3].astype(np.float32)
    existing_alpha = arr[:, :, 3]
    keep = (rgb.mean(axis=2) >= _KEEP_BRIGHTNESS) & (existing_alpha > 0)
    result = arr.copy()
    result[:, :, 3] = np.where(keep, existing_alpha, 0).astype(np.uint8)
    return _feather(Image.fromarray(result, "RGBA"))


def _sample_bg_color(rgb: np.ndarray) -> np.ndarray:
    """Estimate background colour from the crop edges."""
    h, w = rgb.shape[:2]
    s = max(1, min(12, h // 6, w // 6))

    edge_pixels = np.concatenate([
        rgb[:s, :].reshape(-1, 3),
        rgb[-s:, :].reshape(-1, 3),
        rgb[:, :s].reshape(-1, 3),
        rgb[:, -s:].reshape(-1, 3),
    ], axis=0)
    return edge_pixels.mean(axis=0)    # (3,)


def _feather(img: Image.Image) -> Image.Image:
    """Soften hard alpha edges with a small Gaussian blur on the alpha channel."""
    r, g, b, a = img.split()
    a_soft = a.filter(ImageFilter.GaussianBlur(radius=_FEATHER_RADIUS))
    return Image.merge("RGBA", (r, g, b, a_soft))
