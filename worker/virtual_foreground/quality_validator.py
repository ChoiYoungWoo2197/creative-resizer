"""D-2: Validate extracted RGBA virtual object quality.

Thresholds from spec Section 16.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

# Quality thresholds
_MIN_ALPHA_COVERAGE = 0.02
_MAX_ALPHA_COVERAGE = 0.97   # above this → suspected opaque bbox crop
_MAX_BORDER_RATIO = 0.70
_MAX_CONTAMINATION_SCORE = 0.80
_MAX_COMPONENT_COUNT = 25


def validate_extraction_quality(
    rgba_image: Image.Image | None,
    source_bbox: dict,
    *,
    detection_id: str = "",
    job_id: str = "",
) -> dict:
    """Validate RGBA extraction quality.

    Returns:
        {"passed": bool, "failure_reasons": list[str], "metrics": dict}
    """
    if rgba_image is None:
        return {"passed": False, "failure_reasons": ["NO_RGBA_IMAGE"], "metrics": {}}

    if rgba_image.mode != "RGBA":
        rgba_image = rgba_image.convert("RGBA")

    arr = np.array(rgba_image)
    alpha = arr[:, :, 3]
    h, w = alpha.shape
    total_pixels = h * w

    if total_pixels == 0:
        return {"passed": False, "failure_reasons": ["ZERO_SIZE"], "metrics": {}}

    alpha_pixels = int(np.sum(alpha > 0))
    alpha_coverage = alpha_pixels / total_pixels
    opaque_pixels = int(np.sum(alpha >= 240))
    opaque_coverage = opaque_pixels / total_pixels

    # Border ratio
    if h >= 4 and w >= 4:
        border_total = (
            int(np.sum(alpha[0, :] > 0))
            + int(np.sum(alpha[-1, :] > 0))
            + int(np.sum(alpha[:, 0] > 0))
            + int(np.sum(alpha[:, -1] > 0))
        )
        border_ratio = border_total / max(1, alpha_pixels)
    else:
        border_ratio = 0.0

    contamination_score = min(1.0, border_ratio * 1.5)

    component_count = 1
    try:
        from scipy import ndimage as ndi
        binary = (alpha > 127).astype(np.uint8)
        _, component_count = ndi.label(binary)
    except ImportError:
        pass

    metrics = {
        "alphaCoverageRatio": round(alpha_coverage, 4),
        "opaqueCoverageRatio": round(opaque_coverage, 4),
        "borderAlphaRatio": round(border_ratio, 4),
        "backgroundContaminationScore": round(contamination_score, 4),
        "componentCount": component_count,
        "totalPixels": total_pixels,
        "alphaPixels": alpha_pixels,
    }

    failure_reasons: list[str] = []

    if alpha_coverage < _MIN_ALPHA_COVERAGE:
        failure_reasons.append("ALPHA_COVERAGE_TOO_LOW")
    if alpha_coverage >= _MAX_ALPHA_COVERAGE:
        failure_reasons.append("OPAQUE_BBOX_CROP_DETECTED")
    if border_ratio >= _MAX_BORDER_RATIO:
        failure_reasons.append("BORDER_ALPHA_RATIO_TOO_HIGH")
    if contamination_score >= _MAX_CONTAMINATION_SCORE:
        failure_reasons.append("BACKGROUND_CONTAMINATION_DETECTED")
    if component_count > _MAX_COMPONENT_COUNT:
        failure_reasons.append("MASK_COMPONENT_COUNT_EXCESSIVE")

    passed = len(failure_reasons) == 0

    print(
        f"[D2_QUALITY]"
        f" detId={detection_id} jobId={job_id}"
        f" passed={passed}"
        f" coverage={alpha_coverage:.3f}"
        f" border={border_ratio:.3f}"
        f" contamination={contamination_score:.3f}"
        f" components={component_count}"
        f" reasons={failure_reasons}",
        flush=True,
    )

    return {"passed": passed, "failure_reasons": failure_reasons, "metrics": metrics}
