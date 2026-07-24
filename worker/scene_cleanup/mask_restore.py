"""Stage E-2: Pixel integrity validation between source and SSC output.

Computes the fraction of pixels that changed from canonical source to scene plate.
Used in E-4 to detect full-scene regeneration (>85% pixel change).
"""
from __future__ import annotations

import numpy as np


def compute_pixel_diff_ratio(
    src_img: object,
    result_img: object,
    *,
    change_threshold: int = 10,
) -> float:
    """Return fraction of pixels that changed between src and result.

    Args:
        src_img:          PIL Image — canonical source
        result_img:       PIL Image — SSC scene plate
        change_threshold: per-channel delta to count as changed (0-255)

    Returns:
        float in [0.0, 1.0]; 1.0 = all pixels changed; 0.0 = identical.
        Returns 1.0 when dimensions differ (total regeneration assumed).
    """
    from PIL import Image

    if src_img is None or result_img is None:
        return 1.0
    if not isinstance(src_img, Image.Image) or not isinstance(result_img, Image.Image):
        return 1.0

    sw, sh = src_img.size
    rw, rh = result_img.size
    if (sw, sh) != (rw, rh):
        return 1.0

    src_arr = np.array(src_img.convert("RGB"), dtype=np.int32)
    res_arr = np.array(result_img.convert("RGB"), dtype=np.int32)

    delta = np.abs(src_arr - res_arr)  # shape (H, W, 3)
    changed_pixels = np.any(delta > change_threshold, axis=2)  # shape (H, W)
    total = changed_pixels.size
    if total == 0:
        return 0.0
    return float(changed_pixels.sum()) / total


def validate_pixel_integrity(
    src_img: object,
    result_img: object,
    *,
    full_regen_threshold: float = 0.85,
    change_threshold: int = 10,
) -> tuple[bool, float, str]:
    """Check whether the SSC output is a plausible background plate.

    Returns:
        (passed, ratio, reason_code)
        - passed: True when ratio <= full_regen_threshold
        - ratio:  pixel diff ratio
        - reason_code: "" on pass; "FULL_SCENE_REGENERATION_DETECTED" on fail
    """
    ratio = compute_pixel_diff_ratio(
        src_img, result_img, change_threshold=change_threshold
    )
    if ratio > full_regen_threshold:
        return False, ratio, "FULL_SCENE_REGENERATION_DETECTED"
    return True, ratio, ""
