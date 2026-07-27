"""Rasterize a polygon to an L-mode mask with anti-aliased boundary.

Strategy: draw at 2× resolution using PIL ImageDraw, then LANCZOS-downsample.
This gives sub-pixel soft edges without any external dependencies.

Rules:
- polygon with < 3 points → returns all-zero mask (empty), never raises
- polygon points are (x, y) pairs; extra values ignored
- returned mask is L-mode, same size as (image_width, image_height)
"""
from __future__ import annotations

from PIL import Image, ImageDraw


def rasterize_polygon(
    polygon: list[list[int]],
    image_width: int,
    image_height: int,
) -> Image.Image:
    """Return an L-mode mask of size (image_width, image_height).

    Pixels inside the polygon are white (255), outside are black (0).
    Boundary is anti-aliased via 2× supersample + LANCZOS downsample.
    If polygon has fewer than 3 points, returns an all-zero mask.
    """
    scale = 2
    hi_w, hi_h = image_width * scale, image_height * scale
    hi_res = Image.new("L", (hi_w, hi_h), 0)

    pts = [
        (int(round(p[0] * scale)), int(round(p[1] * scale)))
        for p in polygon
        if len(p) >= 2
    ]
    if len(pts) >= 3:
        draw = ImageDraw.Draw(hi_res)
        draw.polygon(pts, fill=255)

    return hi_res.resize((image_width, image_height), Image.LANCZOS)


def bounding_rect(mask: Image.Image) -> tuple[int, int, int, int] | None:
    """Return (x1, y1, x2, y2) of the minimal bounding rectangle of non-zero pixels.

    Returns None when the mask is entirely zero (empty).
    """
    import numpy as np

    arr = np.array(mask)  # (H, W)
    nonzero = np.argwhere(arr > 0)
    if len(nonzero) == 0:
        return None

    r_min, c_min = nonzero.min(axis=0)
    r_max, c_max = nonzero.max(axis=0)
    # (x1, y1, x2, y2) — PIL crop convention: right/bottom are exclusive
    return int(c_min), int(r_min), int(c_max + 1), int(r_max + 1)
