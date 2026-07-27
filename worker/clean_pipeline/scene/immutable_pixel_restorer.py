"""Restore pixels in the restore-mask region from source projection.

Rule: only the projected_restore_mask (binary, threshold > 128) determines which pixels
are restored. No bbox, no center region, no full-image overwrite.
"""
from __future__ import annotations

import numpy as np
from PIL import Image


def restore(
    ai_cleanup: Image.Image,
    source_projection: Image.Image,
    projected_restore_mask: Image.Image,
) -> Image.Image:
    """Copy source_projection pixels into ai_cleanup wherever restore_mask > 128.

    Returns a new RGB image (never modifies inputs in-place).
    All three images must be the same size.
    """
    result = np.array(ai_cleanup.convert("RGB"), dtype=np.uint8).copy()
    src = np.array(source_projection.convert("RGB"), dtype=np.uint8)
    mask = np.array(projected_restore_mask.convert("L"), dtype=np.uint8)

    keep = mask > 128
    result[keep] = src[keep]

    return Image.fromarray(result, "RGB")
