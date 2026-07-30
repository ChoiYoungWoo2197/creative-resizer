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

# Corner brightness below this → consider background dark → trigger removal.
_DARK_DETECT_THRESHOLD = 55          # 0-255 mean RGB

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
    """Return True when at least 2 of the 4 crop corners are uniformly dark.

    Mean-of-all-corners fails when one corner samples a bright product pixel
    (e.g. jar lid), pulling the mean above the threshold even though 3 other
    corners are pure black.  Majority-vote (≥2 dark) is more robust.
    """
    arr = np.array(crop_rgba.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    s = max(1, min(12, h // 6, w // 6))   # corner sample size

    corners = [
        arr[:s, :s],
        arr[:s, -s:],
        arr[-s:, :s],
        arr[-s:, -s:],
    ]
    dark_count = sum(
        1 for c in corners if float(c.mean()) < _DARK_DETECT_THRESHOLD
    )
    return dark_count >= 2


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
    """Brightness-threshold strategy for product objects on dark backgrounds.

    Color-distance approach fails when the product fills the entire crop bbox
    (e.g. a jar that spans corner-to-corner): edge sampling returns product
    pixels as bg_color, so the actual dark background is never removed.

    Since should_remove_background() already confirmed the background is dark,
    brightness thresholding is the correct approach — dark bg pixels have
    brightness ≈ 0 while product pixels (white/cream/colored jars) are >> 50.
    """
    arr = np.array(crop_rgba, dtype=np.uint8)          # H×W×4
    rgb = arr[:, :, :3].astype(np.float32)
    existing_alpha = arr[:, :, 3]

    brightness = rgb.mean(axis=2)                      # H×W, 0-255
    keep = (brightness >= _KEEP_BRIGHTNESS) & (existing_alpha > 0)

    new_alpha = np.where(keep, existing_alpha, 0).astype(np.uint8)
    result = arr.copy()
    result[:, :, 3] = new_alpha

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
