"""Validate RemovalMaskResult.

FAIL conditions:
  REMOVAL_MASK_EMPTY   — removal mask is all zero
  SIZE_MISMATCH        — mask dimensions differ from declared image size
  MASK_OVERLAP         — same pixel is foreground in both removal and restore mask
                         (binary threshold > 128; should never happen if built correctly)
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from worker.clean_pipeline.removal.models import RemovalMaskResult


def validate(
    result: RemovalMaskResult,
) -> tuple[bool, str, str]:
    """Return (is_valid, fail_code, reason). fail_code and reason are '' on success."""

    expected_size = (result.image_width, result.image_height)

    removal_img = Image.open(result.removal_mask_path).convert("L")
    restore_img = Image.open(result.restore_mask_path).convert("L")

    # 1. Size consistency
    if removal_img.size != expected_size:
        return (
            False,
            "SIZE_MISMATCH",
            f"removal_mask size {removal_img.size} != declared {expected_size}",
        )
    if restore_img.size != expected_size:
        return (
            False,
            "SIZE_MISMATCH",
            f"restore_mask size {restore_img.size} != declared {expected_size}",
        )

    removal_arr = np.array(removal_img, dtype=np.uint8)
    restore_arr = np.array(restore_img, dtype=np.uint8)

    # 2. Removal mask must not be empty
    if removal_arr.max() == 0:
        return False, "REMOVAL_MASK_EMPTY", "removal_mask is all zero"

    # 3. No pixel should be foreground in both masks (binary at threshold 128)
    removal_fg = removal_arr > 128
    restore_fg = restore_arr > 128
    overlap_count = int(np.count_nonzero(removal_fg & restore_fg))
    if overlap_count > 0:
        return (
            False,
            "MASK_OVERLAP",
            f"removalMask and restoreMask share {overlap_count} foreground pixel(s)",
        )

    return True, "", ""
