"""Restore pixels in the restore-mask region from source projection.

Rule: only the projected_restore_mask (binary, threshold > 128) determines which pixels
are restored. No bbox, no center region, no full-image overwrite.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def restore(
    ai_cleanup: Image.Image,
    source_projection: Image.Image,
    projected_restore_mask: Image.Image,
    blend_radius: int = 0,
) -> Image.Image:
    """Copy source_projection pixels into ai_cleanup wherever restore_mask > 128.

    If blend_radius > 0, a Gaussian-blurred alpha is used near the mask boundary
    to produce a smooth transition instead of a hard pixel cut.

    Returns a new RGB image (never modifies inputs in-place).
    All three images must be the same size.
    """
    ai_arr = np.array(ai_cleanup.convert("RGB"), dtype=np.float32)
    src_arr = np.array(source_projection.convert("RGB"), dtype=np.float32)
    mask = np.array(projected_restore_mask.convert("L"), dtype=np.uint8)

    if blend_radius > 0:
        soft_mask_img = projected_restore_mask.convert("L").filter(
            ImageFilter.GaussianBlur(radius=blend_radius)
        )
        alpha = np.array(soft_mask_img, dtype=np.float32) / 255.0  # 0=AI, 1=original
        alpha = alpha[..., np.newaxis]
        blended = alpha * src_arr + (1.0 - alpha) * ai_arr
        return Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8), "RGB")

    result = ai_arr.astype(np.uint8).copy()
    keep = mask > 128
    result[keep] = src_arr.astype(np.uint8)[keep]
    return Image.fromarray(result, "RGB")
