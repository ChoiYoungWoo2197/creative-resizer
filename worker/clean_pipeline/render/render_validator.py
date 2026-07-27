"""FINAL_VALIDATION stage — step 2: validate result.png before job_pass.

Checks (in order):
  RESULT_NOT_FOUND       — file does not exist
  RESULT_DECODE_FAILED   — cannot open/decode image
  RESULT_SIZE_MISMATCH   — image dimensions != (target_width, target_height)
  SOLID_COLOR_RESULT     — per-channel spatial std < 2.0 (blank/solid output)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def validate(
    result_path: str,
    target_width: int,
    target_height: int,
) -> tuple[bool, str, str]:
    """Returns (ok, fail_code, reason)."""
    if not Path(result_path).exists():
        return False, "RESULT_NOT_FOUND", f"result.png not found: {result_path}"

    try:
        img = Image.open(result_path)
        img.load()
    except Exception as exc:
        return False, "RESULT_DECODE_FAILED", str(exc)

    if img.size != (target_width, target_height):
        return (
            False,
            "RESULT_SIZE_MISMATCH",
            f"result.png is {img.size}, expected ({target_width}, {target_height})",
        )

    rgb = img.convert("RGB")
    r_arr, g_arr, b_arr = [np.array(c, dtype=np.float32) for c in rgb.split()]
    spatial_std = (float(r_arr.std()) + float(g_arr.std()) + float(b_arr.std())) / 3
    if spatial_std < 2.0:
        return (
            False,
            "SOLID_COLOR_RESULT",
            f"result.png appears solid (per-channel std={spatial_std:.3f} < 2.0)",
        )

    return True, "", ""
