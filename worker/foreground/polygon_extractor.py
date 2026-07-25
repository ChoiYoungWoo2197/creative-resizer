"""Extract RGBA transparent PNG from canonical image using GPT polygon points."""
from __future__ import annotations

import hashlib

from PIL import Image, ImageDraw, ImageFilter


def extract_polygon_mask(
    source_image: Image.Image,
    polygon: list,
    bbox: dict,
    holes: list | None = None,
    edge_feather_px: int = 2,
    crop_padding: int = 4,
) -> tuple:
    """Extract RGBA image from canonical source using polygon mask.

    Args:
        source_image:    Full canonical PIL Image
        polygon:         list of [x,y] pairs or {"x":..,"y":..} dicts
        bbox:            {x, y, width, height} in source coords
        holes:           list of polygons to subtract (cut out)
        edge_feather_px: Gaussian blur radius for edge softening
        crop_padding:    extra pixels around bbox crop

    Returns:
        (rgba_img, metrics) — rgba_img is None on failure.
    """
    if source_image is None or not polygon:
        return None, {"error": "MISSING_POLYGON_OR_SOURCE"}

    src_w, src_h = source_image.size
    pts = _normalize_polygon(polygon)
    if len(pts) < 3:
        return None, {"error": "POLYGON_TOO_FEW_POINTS"}

    # Draw filled polygon mask
    mask = Image.new("L", (src_w, src_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(pts, fill=255)

    # Subtract holes
    if holes:
        for hole in holes:
            hole_pts = _normalize_polygon(hole)
            if len(hole_pts) >= 3:
                draw.polygon(hole_pts, fill=0)

    # Feather edges
    if edge_feather_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=edge_feather_px))

    # Build RGBA from source + mask
    src_rgba = source_image.convert("RGBA")
    r, g, b, _ = src_rgba.split()
    result_full = Image.merge("RGBA", (r, g, b, mask))

    # Crop to bbox + padding
    bx = int(bbox.get("x", 0))
    by = int(bbox.get("y", 0))
    bw = int(bbox.get("width", src_w))
    bh = int(bbox.get("height", src_h))
    x1 = max(0, bx - crop_padding)
    y1 = max(0, by - crop_padding)
    x2 = min(src_w, bx + bw + crop_padding)
    y2 = min(src_h, by + bh + crop_padding)
    if x2 <= x1 or y2 <= y1:
        return None, {"error": "INVALID_CROP_AREA"}

    result_crop = result_full.crop((x1, y1, x2, y2))

    import numpy as np
    mask_arr = np.array(mask, dtype=np.uint8)
    mask_pixel_count = int((mask_arr > 0).sum())

    mask_ref = hashlib.sha256(mask_arr.tobytes()).hexdigest()
    fg_img_ref = hashlib.sha256(result_crop.tobytes()).hexdigest()

    return result_crop, {
        "maskPixelCount": mask_pixel_count,
        "maskRef": mask_ref,
        "foregroundImageRef": fg_img_ref,
        "cropRect": {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1},
    }


def _normalize_polygon(polygon: list) -> list[tuple]:
    pts: list[tuple] = []
    for pt in polygon:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            pts.append((float(pt[0]), float(pt[1])))
        elif isinstance(pt, dict):
            pts.append((float(pt.get("x", 0)), float(pt.get("y", 0))))
    return pts
