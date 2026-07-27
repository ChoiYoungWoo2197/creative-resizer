"""Unified Pipeline V2: polygon/mask-based RGBA extraction from canonical source pixels.

Extracts each detected ad object as an RGBA PIL Image at bbox size.
If polygon points are available, they are used as the alpha mask.
Otherwise the full bbox is used with fully opaque alpha.

No fake/mock — extraction failure returns None (caller decides whether to skip or FAIL).
"""
from __future__ import annotations

import hashlib

import numpy as np
from PIL import Image, ImageDraw

from unified_v2.contracts import V2DetectedObject, V2FgLayer


def extract_fg_layer(
    canonical: Image.Image,
    obj: V2DetectedObject,
    job_id: str = "",
) -> V2FgLayer | None:
    """Extract RGBA cutout for one detected object from the canonical source image.

    Args:
        canonical: Full-size canonical source image (any PIL mode).
        obj:       Detected object with bbox and optional polygon (source coords).
        job_id:    For structured log output.

    Returns:
        V2FgLayer on success, None if bbox is invalid or out of bounds.
    """
    src_w, src_h = canonical.size
    x = int(obj.bbox.get("x", 0))
    y = int(obj.bbox.get("y", 0))
    w = int(obj.bbox.get("width", 0))
    h = int(obj.bbox.get("height", 0))

    if w <= 0 or h <= 0:
        print(
            f"[V2_IMAGE_EXTRACT] jobId={job_id} objectId={obj.object_id}"
            f" role={obj.role} status=SKIP reason=INVALID_BBOX bbox={obj.bbox}",
            flush=True,
        )
        return None

    # Clamp to image bounds
    x = max(0, x)
    y = max(0, y)
    x2 = min(src_w, x + w)
    y2 = min(src_h, y + h)
    actual_w = x2 - x
    actual_h = y2 - y

    if actual_w <= 0 or actual_h <= 0:
        print(
            f"[V2_IMAGE_EXTRACT] jobId={job_id} objectId={obj.object_id}"
            f" role={obj.role} status=SKIP reason=OUT_OF_BOUNDS",
            flush=True,
        )
        return None

    # Crop bbox region from canonical (RGBA)
    crop = canonical.crop((x, y, x2, y2)).convert("RGBA")

    # Apply polygon mask if polygon has enough points
    polygon = obj.polygon
    if polygon and len(polygon) >= 3:
        # Translate from source coords to crop-local coords
        pts = [(int(px) - x, int(py) - y) for px, py in polygon]
        alpha_mask = Image.new("L", (actual_w, actual_h), 0)
        draw = ImageDraw.Draw(alpha_mask)
        draw.polygon(pts, fill=255)
        crop.putalpha(alpha_mask)
        has_polygon = True
    else:
        # Full opaque bbox
        alpha_mask = Image.new("L", (actual_w, actual_h), 255)
        crop.putalpha(alpha_mask)
        has_polygon = False

    # Compute provenance hashes
    arr = np.array(crop, dtype=np.uint8)
    alpha_arr = arr[:, :, 3]
    try:
        mask_sha = hashlib.sha256(alpha_arr.tobytes()).hexdigest()[:16]
        pixel_sha = hashlib.sha256(arr.tobytes()).hexdigest()[:16]
    except Exception:
        mask_sha = ""
        pixel_sha = ""

    alpha_coverage = float(np.mean(alpha_arr > 0))

    print(
        f"[V2_IMAGE_EXTRACT] jobId={job_id} objectId={obj.object_id}"
        f" role={obj.role}"
        f" bbox={x},{y} {actual_w}x{actual_h}"
        f" hasPolygon={has_polygon}"
        f" alphaCoverage={alpha_coverage:.3f}"
        f" pixelSha={pixel_sha}"
        f" status=OK",
        flush=True,
    )

    source_bbox = {"x": x, "y": y, "width": actual_w, "height": actual_h}
    return V2FgLayer(
        object_id=obj.object_id,
        role=obj.role,
        image=crop,
        source_bbox=source_bbox,
        bbox=dict(source_bbox),  # will be overwritten by layout engine
        mask_sha256=mask_sha,
        pixel_sha256=pixel_sha,
        text_content=obj.text_content,
    )
