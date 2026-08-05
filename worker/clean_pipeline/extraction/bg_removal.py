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

# Roles that use TEXT (brightness-threshold) strategy.
_TEXT_ROLES = frozenset({"title_group", "body_text_group", "cta_group", "badge", "logo"})


# ── Public ────────────────────────────────────────────────────────────────────


def should_remove_background(crop_rgba: Image.Image) -> bool:
    """Return True when the crop border is dominantly dark OR achromatic-bright.

    Detects two kinds of backgrounds that need removal:
      • Dark panels (solid black/dark background behind text)
      • Achromatic-bright panels (white/grey background — low saturation, high brightness)
    """
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


def remove_background(crop_rgba: Image.Image, role: str) -> Image.Image:
    """Return crop_rgba with dark background pixels made transparent.

    Preserves the existing polygon alpha channel: pixels already transparent
    remain transparent regardless of strategy.
    """
    if role in _TEXT_ROLES:
        return _remove_text_bg(crop_rgba)
    return _remove_product_bg(crop_rgba)


# ── Strategies ────────────────────────────────────────────────────────────────


def _remove_text_bg(crop_rgba: Image.Image) -> Image.Image:
    """Brightness + achromatic strategy for text / icon objects.

    Removes two kinds of background:
      • dark pixels (brightness < _KEEP_BRIGHTNESS) — dark panel background
      • achromatic-bright pixels (low saturation AND high brightness)
        — white/grey panel background

    Coloured text (high saturation) and dark-coloured text (low brightness)
    that is NOT achromatic-bright are preserved.
    """
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
