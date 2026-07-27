"""Validate ExtractionResult against the SceneManifest.

FAIL conditions:
  REQUIRED_OBJECT_NOT_EXTRACTED  — required manifest object missing from extraction
  REQUIRED_RGBA_MISSING          — required object RGBA file does not exist
  REQUIRED_MASK_EMPTY            — required object mask is all zero
  POLYGON_ALPHA_LEAK             — polygon outside alpha is not 0 (RGBA alpha ≠ mask)
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from clean_pipeline.analysis.models import SceneManifest
from clean_pipeline.extraction.models import ExtractionResult


def validate(
    manifest: SceneManifest,
    result: ExtractionResult,
) -> tuple[bool, str, str]:
    """Return (is_valid, fail_code, reason). fail_code and reason are '' on success."""

    extracted_ids = {o.id for o in result.objects}

    for obj in manifest.objects:
        # Skip objects that are not in the extraction scope
        if obj.role == "protected_subject":
            continue
        if not (obj.movable and obj.removable_from_scene):
            continue

        # 1. Required object must appear in extraction result
        if obj.required and obj.id not in extracted_ids:
            return (
                False,
                "REQUIRED_OBJECT_NOT_EXTRACTED",
                f"Required object '{obj.id}' (role={obj.role}) was not extracted",
            )

    for ext in result.objects:
        if not ext.required:
            continue

        from pathlib import Path

        # 2. Required RGBA file must exist
        if not Path(ext.rgba_path).exists():
            return (
                False,
                "REQUIRED_RGBA_MISSING",
                f"Required object '{ext.id}' RGBA file missing: {ext.rgba_path}",
            )

        # 3. Required mask must not be empty
        mask_arr = np.array(Image.open(ext.mask_path).convert("L"))
        if int(mask_arr.max()) == 0:
            return (
                False,
                "REQUIRED_MASK_EMPTY",
                f"Required object '{ext.id}' mask is all zero (empty polygon rasterization)",
            )

        # 4. RGBA alpha channel must equal the mask exactly
        rgba_arr = np.array(Image.open(ext.rgba_path).convert("RGBA"))
        alpha = rgba_arr[:, :, 3]
        if not np.array_equal(alpha, mask_arr):
            return (
                False,
                "POLYGON_ALPHA_LEAK",
                f"Object '{ext.id}' RGBA alpha channel does not match mask — "
                "pixels outside polygon have non-zero alpha",
            )

    return True, "", ""
