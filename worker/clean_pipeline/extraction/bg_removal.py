"""Background removal for extracted RGBA crops.

Used in P3 OBJECT_EXTRACTION when an object sits on a uniformly dark background
(e.g. a solid black advertising panel).  Converts the polygon-masked RGBA crop
so that background pixels become transparent.

Two strategies, chosen by role:
  TEXT   (title_group, body_text_group, cta_group, badge, logo)
         Bright-pixel keep: any pixel whose mean RGB brightness >= _KEEP_BRIGHTNESS
         is kept; darker pixels become transparent.  Works well for light-coloured
         text / icons on a black or very dark panel.

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
    Returns True when the crop's corners are uniformly dark.

remove_background(crop_rgba, role) -> Image.Image
    Returns a new RGBA image with background pixels made transparent.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

# ── Tuning constants ──────────────────────────────────────────────────────────

# Border band sampling: border pixels darker than this count as "dark".
_DARK_DETECT_THRESHOLD = 55          # 0-255 mean RGB

# Border band thickness and minimum dark-pixel ratio to trigger bg removal.
# Using the full border band (not just 4 corners) handles tight crops where
# the product fills the corners but leaves dark strips along some edges.
_DARK_BORDER_WIDTH = 3               # px — top/bottom/left/right strip
_DARK_RATIO_THRESHOLD = 0.15         # 15 % of border pixels must be dark

# TEXT strategy: keep pixels brighter than this.
_KEEP_BRIGHTNESS = 50                # 0-255 mean RGB

# PRODUCT strategy: colour distance from sampled bg → transparent.
_PRODUCT_BG_DIST = 40               # Euclidean distance in RGB space

# Feather radius applied after alpha thresholding (reduces hard edges).
_FEATHER_RADIUS = 1.5

# Roles that use TEXT (brightness-threshold) strategy.
_TEXT_ROLES = frozenset({"title_group", "body_text_group", "cta_group", "badge", "logo"})


# ── Public ────────────────────────────────────────────────────────────────────


def should_remove_background(crop_rgba: Image.Image) -> bool:
    """Return True when the crop border contains enough dark background pixels.

    The previous 4-corner approach fails when the product fills the corners
    (e.g. a tight jar crop): all 4 corners sample bright product pixels and
    the function returns False even though dark background strips exist along
    the edges.  Sampling the full border band catches those strips.
    """
    arr = np.array(crop_rgba.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    b = min(_DARK_BORDER_WIDTH, h // 2, w // 2)   # band width, clamped

    brightness = arr.mean(axis=2)   # H×W, 0-255
    border = np.concatenate([
        brightness[:b, :].ravel(),
        brightness[-b:, :].ravel(),
        brightness[:, :b].ravel(),
        brightness[:, -b:].ravel(),
    ])
    dark_ratio = float((border < _DARK_DETECT_THRESHOLD).sum()) / len(border)
    return dark_ratio >= _DARK_RATIO_THRESHOLD


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
    """Brightness-threshold strategy for text / icon objects."""
    arr = np.array(crop_rgba, dtype=np.uint8)          # H×W×4
    rgb = arr[:, :, :3].astype(np.float32)
    existing_alpha = arr[:, :, 3]                      # polygon mask alpha

    brightness = rgb.mean(axis=2)                      # H×W, 0-255
    keep = (brightness >= _KEEP_BRIGHTNESS) & (existing_alpha > 0)

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

    # ── 3. Largest Blob: 잔여 텍스트 조각 제거 ───────────────────────────────
    final_alpha = _keep_largest_blob_from_binary(closed_binary, new_alpha)

    result = arr.copy()
    result[:, :, 3] = final_alpha
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
