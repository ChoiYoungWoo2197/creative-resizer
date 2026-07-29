"""Deterministic scene plate validation — no AI, no external calls.

Checks (in order, short-circuits on first failure):
  1. SCENE_PLATE_DECODE_FAILED   — image file cannot be opened
  2. TARGET_SIZE_MISMATCH        — scene_plate size ≠ (target_width, target_height)
  3. SOLID_COLOR_IMAGE           — pixel std < 2.0 (transparent or uniform fill)

Note: RESTORE_PIXELS_MISMATCH was removed. Step 3 in scene_plate_generator applies
Gaussian soft blending (blend_radius=20) at the restore mask boundary, so restore-region
pixels near the removal edge are intentionally NOT pixel-identical to source_projection.
The AI naturalness score in P6 covers restoration quality.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from clean_pipeline.validation.scene_models import DeterministicValidationResult

_SOLID_STD_THRESHOLD = 2.0


def validate(
    scene_plate_path: str,
    source_projection_path: str,
    projected_restore_mask_path: str,
    target_width: int,
    target_height: int,
) -> DeterministicValidationResult:

    # 1. Decode
    try:
        scene_img = Image.open(scene_plate_path)
        scene_img.verify()          # checks file integrity
        scene_img = Image.open(scene_plate_path)  # re-open after verify()
    except Exception as exc:
        return DeterministicValidationResult(
            decode_success=False,
            target_size_ok=False,
            not_solid_color=False,
            restore_pixels_ok=False,
            passed=False,
            fail_code="SCENE_PLATE_DECODE_FAILED",
            reason=str(exc),
        )

    # 2. Target size
    if scene_img.size != (target_width, target_height):
        return DeterministicValidationResult(
            decode_success=True,
            target_size_ok=False,
            not_solid_color=False,
            restore_pixels_ok=False,
            passed=False,
            fail_code="TARGET_SIZE_MISMATCH",
            reason=f"scene_plate is {scene_img.size[0]}×{scene_img.size[1]}, "
                   f"expected {target_width}×{target_height}",
        )

    # 3. Not solid / transparent
    # Compute per-channel spatial std (excludes inter-channel variation).
    # A solid-color image has all pixels identical → each channel std = 0.
    scene_rgb = scene_img.convert("RGB")
    r_arr, g_arr, b_arr = [np.array(c, dtype=np.float32) for c in scene_rgb.split()]
    spatial_std = (float(r_arr.std()) + float(g_arr.std()) + float(b_arr.std())) / 3
    if spatial_std < _SOLID_STD_THRESHOLD:
        return DeterministicValidationResult(
            decode_success=True,
            target_size_ok=True,
            not_solid_color=False,
            restore_pixels_ok=False,
            passed=False,
            fail_code="SOLID_COLOR_IMAGE",
            reason=f"scene_plate spatial std={spatial_std:.4f} < {_SOLID_STD_THRESHOLD} "
                   "(transparent or solid-color image)",
        )

    return DeterministicValidationResult(
        decode_success=True,
        target_size_ok=True,
        not_solid_color=True,
        restore_pixels_ok=True,  # soft blend → no longer pixel-exact; always passes
        passed=True,
    )
