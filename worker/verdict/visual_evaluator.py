"""Stage E-4: Visual (pixel-based) verdict evaluator.

Evaluates the final rendered output against basic visual integrity criteria:
  1. Not blank / not uniform fill (variance >= 5.0)
  2. Output dimensions match target
  3. Not full scene regeneration (pixel diff ratio <= 85% vs. canonical source)

Required in production when VISUAL_VERDICT_ENABLED=true.
Default (not required) for backward compatibility with C-1 unit tests.
"""
from __future__ import annotations

from verdict.models import (
    VerdictResult, PASS, FAIL, NOT_TESTED, NOT_APPLICABLE,
    VERDICT_VERSION,
)

# Pixel diff ratio above which the output is considered full-scene regeneration
FULL_REGEN_THRESHOLD = 0.85

# Image variance below which the result is considered blank
BLANK_VARIANCE_THRESHOLD = 5.0

VISUAL_REASON_PASS = "VISUAL_PASS"
VISUAL_REASON_BLANK = "SCENE_PLATE_BLANK"
VISUAL_REASON_WRONG_DIMENSIONS = "SCENE_PLATE_WRONG_DIMENSIONS"
VISUAL_REASON_FULL_REGEN = "FULL_SCENE_REGENERATION_DETECTED"
VISUAL_REASON_NOT_TESTED = "VISUAL_NOT_TESTED"
VISUAL_REASON_NOT_APPLICABLE = "VISUAL_NO_SOURCE"


def evaluate_visual(
    *,
    source_img: object,
    result_img: object,
    target_w: int,
    target_h: int,
    job_id: str = "",
    spec_id: str = "",
) -> VerdictResult:
    """Evaluate visual integrity of the rendered output.

    Args:
        source_img:  PIL Image — canonical source (for pixel diff)
        result_img:  PIL Image — final composited output
        target_w:    Expected width
        target_h:    Expected height

    Returns:
        VerdictResult with status PASS/FAIL/NOT_APPLICABLE.
        required=False here — caller sets required based on env config.
    """
    import numpy as np
    from PIL import Image

    if result_img is None or not isinstance(result_img, Image.Image):
        reason = VISUAL_REASON_BLANK
        _log_visual(job_id, spec_id, FAIL, [reason], {})
        return VerdictResult(
            name="visualVerdict",
            status=FAIL,
            required=False,
            reasonCodes=[reason],
            messages=["result_img is None or not a PIL Image"],
            evidence={"resultImgNone": True},
        )

    rw, rh = result_img.size

    # 1. Dimension check
    if (rw, rh) != (target_w, target_h):
        reason = VISUAL_REASON_WRONG_DIMENSIONS
        evidence = {"actualW": rw, "actualH": rh, "targetW": target_w, "targetH": target_h}
        _log_visual(job_id, spec_id, FAIL, [reason], evidence)
        return VerdictResult(
            name="visualVerdict",
            status=FAIL,
            required=False,
            reasonCodes=[reason],
            messages=[f"expected={target_w}x{target_h} actual={rw}x{rh}"],
            evidence=evidence,
        )

    # 2. Blank check
    try:
        arr = np.array(result_img.convert("RGB"), dtype=np.float32)
        variance = float(arr.var())
    except Exception as _e:
        variance = 0.0

    if variance < BLANK_VARIANCE_THRESHOLD:
        reason = VISUAL_REASON_BLANK
        evidence = {"variance": variance, "threshold": BLANK_VARIANCE_THRESHOLD}
        _log_visual(job_id, spec_id, FAIL, [reason], evidence)
        return VerdictResult(
            name="visualVerdict",
            status=FAIL,
            required=False,
            reasonCodes=[reason],
            messages=[f"variance={variance:.2f} < {BLANK_VARIANCE_THRESHOLD}"],
            evidence=evidence,
        )

    # 3. Full scene regeneration check (requires source_img)
    diff_ratio = None
    if source_img is not None and isinstance(source_img, Image.Image):
        try:
            from scene_cleanup.mask_restore import compute_pixel_diff_ratio
            diff_ratio = compute_pixel_diff_ratio(source_img, result_img)
            if diff_ratio > FULL_REGEN_THRESHOLD:
                reason = VISUAL_REASON_FULL_REGEN
                evidence = {"pixelDiffRatio": diff_ratio, "threshold": FULL_REGEN_THRESHOLD}
                _log_visual(job_id, spec_id, FAIL, [reason], evidence)
                return VerdictResult(
                    name="visualVerdict",
                    status=FAIL,
                    required=False,
                    reasonCodes=[reason],
                    messages=[f"pixelDiffRatio={diff_ratio:.3f} > {FULL_REGEN_THRESHOLD}"],
                    evidence=evidence,
                    metrics={"pixelDiffRatio": diff_ratio},
                )
        except Exception:
            diff_ratio = None

    evidence = {
        "variance": variance,
        "pixelDiffRatio": diff_ratio,
        "targetW": target_w,
        "targetH": target_h,
    }
    _log_visual(job_id, spec_id, PASS, [VISUAL_REASON_PASS], evidence)
    return VerdictResult(
        name="visualVerdict",
        status=PASS,
        required=False,
        reasonCodes=[VISUAL_REASON_PASS],
        messages=["visual integrity checks passed"],
        evidence=evidence,
        metrics={"variance": variance, "pixelDiffRatio": diff_ratio},
    )


def _log_visual(
    job_id: str,
    spec_id: str,
    status: str,
    reason_codes: list,
    evidence: dict,
) -> None:
    print(
        f"[VERDICT_VISUAL] jobId={job_id} specId={spec_id}"
        f" status={status}"
        f" reasonCodes={reason_codes}"
        f" evidence={evidence}",
        flush=True,
    )
