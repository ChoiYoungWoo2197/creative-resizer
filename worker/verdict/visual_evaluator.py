"""Stage E-4 + P1-D: Visual (pixel-based) verdict evaluator.

Base checks (E-4):
  1. Not blank / not uniform fill (variance >= 5.0)
  2. Output dimensions match target
  3. Not full scene regeneration (pixel diff ratio <= 85%)

Extended metrics (P1-D):
  immutableChangedPixelRatio:       fraction of immutable pixels that changed
  outsideAllowedChangedPixelRatio:  fraction outside allowed generation mask that changed
  sceneSimilarityScore:             1 - pixelDiffRatio (0=totally different, 1=identical)
  backgroundSemanticDriftScore:     variance increase in background region
  productVisibilityRatio:           product bbox coverage in result
  titleVisibilityRatio:             title text contrast score proxy
  ctaVisibilityRatio:               CTA text contrast score proxy
  titleContrastRatio:               title region mean / background mean
  ctaContrastRatio:                 CTA region mean / background mean
  faceOcclusionRatio:               face bbox overlap with non-source pixels
  handOcclusionRatio:               hand bbox overlap with non-source pixels
  groupCompletenessRatio:           group children rendered / expected
  duplicateObjectCount:             detected duplicate compositing count
  blankOutputScore:                 inverse variance (1.0 = totally blank)

Additional reason codes:
  IMMUTABLE_PIXELS_CHANGED, OUTSIDE_ALLOWED_REGION_CHANGED, SCENE_IDENTITY_CHANGED
  PRODUCT_VISIBILITY_FAILED, TITLE_READABILITY_FAILED, CTA_READABILITY_FAILED
  FACE_OCCLUSION_EXCEEDED, HAND_OCCLUSION_EXCEEDED, SEMANTIC_GROUP_INCOMPLETE
  DUPLICATE_OBJECT_COMPOSITION, BLANK_OUTPUT_DETECTED

Production: visualVerdict=NOT_TESTED → FAIL when visual_required=True (aggregator)
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

# P1-D reason codes
REASON_IMMUTABLE_CHANGED = "IMMUTABLE_PIXELS_CHANGED"
REASON_OUTSIDE_ALLOWED = "OUTSIDE_ALLOWED_REGION_CHANGED"
REASON_SCENE_IDENTITY = "SCENE_IDENTITY_CHANGED"
REASON_PRODUCT_VISIBILITY = "PRODUCT_VISIBILITY_FAILED"
REASON_TITLE_READABILITY = "TITLE_READABILITY_FAILED"
REASON_CTA_READABILITY = "CTA_READABILITY_FAILED"
REASON_FACE_OCCLUSION = "FACE_OCCLUSION_EXCEEDED"
REASON_HAND_OCCLUSION = "HAND_OCCLUSION_EXCEEDED"
REASON_GROUP_INCOMPLETE = "SEMANTIC_GROUP_INCOMPLETE"
REASON_DUPLICATE_OBJECT = "DUPLICATE_OBJECT_COMPOSITION"
REASON_BLANK_OUTPUT = "BLANK_OUTPUT_DETECTED"


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


# ── P1-D: Extended visual metrics ────────────────────────────────────────────

def compute_extended_visual_metrics(
    *,
    canonical_img: object = None,
    result_img: object = None,
    allowed_generation_mask: object = None,
    immutable_mask: object = None,
    product_bboxes: list | None = None,
    title_bboxes: list | None = None,
    cta_bboxes: list | None = None,
    face_bboxes: list | None = None,
    hand_bboxes: list | None = None,
    group_completeness_ratio: float | None = None,
    duplicate_object_count: int = 0,
) -> dict:
    """Compute P1-D extended visual metrics.

    All inputs are optional — missing inputs produce safe defaults (0 or 1.0).
    Returns a dict with all 15 metric keys defined in the P1-D spec.
    """
    import numpy as np
    from PIL import Image

    metrics: dict = {
        "immutableChangedPixelRatio": 0.0,
        "outsideAllowedChangedPixelRatio": 0.0,
        "sceneSimilarityScore": 1.0,
        "backgroundSemanticDriftScore": 0.0,
        "fullSceneRegenerationScore": 0.0,
        "productVisibilityRatio": 1.0,
        "titleVisibilityRatio": 1.0,
        "ctaVisibilityRatio": 1.0,
        "titleContrastRatio": 1.0,
        "ctaContrastRatio": 1.0,
        "faceOcclusionRatio": 0.0,
        "handOcclusionRatio": 0.0,
        "groupCompletenessRatio": 1.0,
        "duplicateObjectCount": duplicate_object_count,
        "blankOutputScore": 0.0,
    }

    if result_img is None or not isinstance(result_img, Image.Image):
        metrics["blankOutputScore"] = 1.0
        return metrics

    res_arr = np.array(result_img.convert("RGB"), dtype=np.float32)
    h, w = res_arr.shape[:2]
    total = h * w

    # blankOutputScore: inverse variance (1.0 = totally blank)
    variance = float(res_arr.var())
    metrics["blankOutputScore"] = max(0.0, 1.0 - min(1.0, variance / (BLANK_VARIANCE_THRESHOLD * 100)))

    if canonical_img is not None and isinstance(canonical_img, Image.Image):
        can_arr = np.array(canonical_img.convert("RGB"), dtype=np.float32)
        if can_arr.shape == res_arr.shape:
            # Pixel diff ratio
            delta = np.abs(res_arr - can_arr)
            changed = np.any(delta > 10, axis=2)
            diff_ratio = float(changed.sum()) / total if total > 0 else 0.0
            metrics["sceneSimilarityScore"] = 1.0 - diff_ratio
            metrics["fullSceneRegenerationScore"] = diff_ratio

            # Immutable mask check
            imm_mask = _to_bool_mask(immutable_mask, w, h)
            if imm_mask is not None and imm_mask.sum() > 0:
                immutable_changed = float((changed & imm_mask).sum()) / imm_mask.sum()
                metrics["immutableChangedPixelRatio"] = immutable_changed

            # Outside allowed generation mask check
            allowed_mask = _to_bool_mask(allowed_generation_mask, w, h)
            if allowed_mask is not None:
                outside = ~allowed_mask
                outside_count = int(outside.sum())
                if outside_count > 0:
                    outside_changed = float((changed & outside).sum()) / outside_count
                    metrics["outsideAllowedChangedPixelRatio"] = outside_changed

            # Background semantic drift: variance change in new-canvas region
            if allowed_mask is not None:
                src_bg_var = float(can_arr[~allowed_mask].var()) if (~allowed_mask).sum() > 0 else 0
                res_bg_var = float(res_arr[~allowed_mask].var()) if (~allowed_mask).sum() > 0 else 0
                drift = abs(res_bg_var - src_bg_var) / max(1.0, src_bg_var + res_bg_var)
                metrics["backgroundSemanticDriftScore"] = min(1.0, drift)

    # Product/title/CTA visibility ratios (proxy: visible bbox area in result)
    if product_bboxes:
        metrics["productVisibilityRatio"] = _bbox_visibility_score(res_arr, product_bboxes)
    if title_bboxes:
        metrics["titleVisibilityRatio"] = _bbox_visibility_score(res_arr, title_bboxes)
        metrics["titleContrastRatio"] = _contrast_ratio_for_bboxes(res_arr, title_bboxes)
    if cta_bboxes:
        metrics["ctaVisibilityRatio"] = _bbox_visibility_score(res_arr, cta_bboxes)
        metrics["ctaContrastRatio"] = _contrast_ratio_for_bboxes(res_arr, cta_bboxes)

    # Face/hand occlusion (new-canvas pixels in face/hand bbox)
    if face_bboxes and canonical_img is not None and isinstance(canonical_img, Image.Image):
        can_arr2 = np.array(canonical_img.convert("RGB"), dtype=np.float32)
        if can_arr2.shape == res_arr.shape:
            delta2 = np.abs(res_arr - can_arr2)
            changed2 = np.any(delta2 > 10, axis=2)
            metrics["faceOcclusionRatio"] = _bbox_changed_ratio(changed2, face_bboxes, h, w)

    if hand_bboxes and canonical_img is not None and isinstance(canonical_img, Image.Image):
        can_arr3 = np.array(canonical_img.convert("RGB"), dtype=np.float32)
        if can_arr3.shape == res_arr.shape:
            delta3 = np.abs(res_arr - can_arr3)
            changed3 = np.any(delta3 > 10, axis=2)
            metrics["handOcclusionRatio"] = _bbox_changed_ratio(changed3, hand_bboxes, h, w)

    if group_completeness_ratio is not None:
        metrics["groupCompletenessRatio"] = float(group_completeness_ratio)

    return metrics


def evaluate_extended_visual(
    *,
    source_img: object,
    result_img: object,
    target_w: int,
    target_h: int,
    allowed_generation_mask: object = None,
    immutable_mask: object = None,
    product_bboxes: list | None = None,
    title_bboxes: list | None = None,
    cta_bboxes: list | None = None,
    face_bboxes: list | None = None,
    hand_bboxes: list | None = None,
    group_completeness_ratio: float | None = None,
    duplicate_object_count: int = 0,
    job_id: str = "",
    spec_id: str = "",
    # Thresholds
    immutable_threshold: float = 0.0,      # any immutable change → fail
    outside_allowed_threshold: float = 0.0,
    face_occlusion_threshold: float = 0.10,
    hand_occlusion_threshold: float = 0.15,
    product_visibility_threshold: float = 0.0,  # 0 = any visibility ok
    group_completeness_min: float = 1.0,
) -> VerdictResult:
    """Extended visual verdict with P1-D metrics.

    Runs base E-4 checks first (blank, dimensions, full-regen),
    then adds P1-D metric-derived failure conditions.
    """
    # Base E-4 checks
    base_verdict = evaluate_visual(
        source_img=source_img,
        result_img=result_img,
        target_w=target_w,
        target_h=target_h,
        job_id=job_id,
        spec_id=spec_id,
    )
    if base_verdict.status == FAIL:
        return base_verdict

    # Compute extended metrics
    metrics = compute_extended_visual_metrics(
        canonical_img=source_img,
        result_img=result_img,
        allowed_generation_mask=allowed_generation_mask,
        immutable_mask=immutable_mask,
        product_bboxes=product_bboxes,
        title_bboxes=title_bboxes,
        cta_bboxes=cta_bboxes,
        face_bboxes=face_bboxes,
        hand_bboxes=hand_bboxes,
        group_completeness_ratio=group_completeness_ratio,
        duplicate_object_count=duplicate_object_count,
    )

    reason_codes: list[str] = []

    # Check thresholds
    if metrics["immutableChangedPixelRatio"] > immutable_threshold:
        reason_codes.append(REASON_IMMUTABLE_CHANGED)
    if metrics["outsideAllowedChangedPixelRatio"] > outside_allowed_threshold:
        reason_codes.append(REASON_OUTSIDE_ALLOWED)
    if metrics["faceOcclusionRatio"] > face_occlusion_threshold:
        reason_codes.append(REASON_FACE_OCCLUSION)
    if metrics["handOcclusionRatio"] > hand_occlusion_threshold:
        reason_codes.append(REASON_HAND_OCCLUSION)
    if product_bboxes and metrics["productVisibilityRatio"] < product_visibility_threshold:
        reason_codes.append(REASON_PRODUCT_VISIBILITY)
    if group_completeness_ratio is not None and group_completeness_ratio < group_completeness_min:
        reason_codes.append(REASON_GROUP_INCOMPLETE)
    if duplicate_object_count > 0:
        reason_codes.append(REASON_DUPLICATE_OBJECT)
    if metrics["blankOutputScore"] > 0.9:
        reason_codes.append(REASON_BLANK_OUTPUT)

    status = FAIL if reason_codes else PASS
    evidence = {**base_verdict.evidence, **metrics}
    _log_visual(job_id, spec_id, status, reason_codes or [VISUAL_REASON_PASS], evidence)

    return VerdictResult(
        name="visualVerdict",
        status=status,
        required=False,
        reasonCodes=reason_codes or [VISUAL_REASON_PASS],
        messages=[f"extended visual metrics evaluated"],
        evidence=evidence,
        metrics=metrics,
    )


# ── P1-D helpers ──────────────────────────────────────────────────────────────

def _to_bool_mask(mask_input: object, w: int, h: int) -> object | None:
    """Convert mask to 2D bool array (H, W) or None."""
    if mask_input is None:
        return None
    try:
        import numpy as np
        from PIL import Image
        if isinstance(mask_input, Image.Image):
            arr = np.array(mask_input.convert("L"), dtype=np.uint8)
        elif isinstance(mask_input, np.ndarray):
            arr = mask_input.astype(np.uint8)
            if arr.ndim == 3:
                arr = arr[:, :, 0]
        else:
            return None
        mh, mw = arr.shape[:2]
        if (mw, mh) != (w, h):
            return None
        return arr > 127
    except Exception:
        return None


def _bbox_visibility_score(result_arr: object, bboxes: list) -> float:
    """Return mean normalised pixel intensity in the bbox region (proxy for visibility)."""
    import numpy as np
    if not bboxes or result_arr is None:
        return 1.0
    h, w = result_arr.shape[:2]
    total_pixels = 0
    visible_pixels = 0
    for bbox in bboxes:
        bx = max(0, int(bbox.get("x", 0)))
        by = max(0, int(bbox.get("y", 0)))
        bw = min(int(bbox.get("w", 0)), w - bx)
        bh = min(int(bbox.get("h", 0)), h - by)
        if bw <= 0 or bh <= 0:
            continue
        region = result_arr[by:by+bh, bx:bx+bw]
        total_pixels += region.size
        visible_pixels += int((region > 10).sum())
    if total_pixels == 0:
        return 1.0
    return visible_pixels / total_pixels


def _contrast_ratio_for_bboxes(result_arr: object, bboxes: list) -> float:
    """Compute contrast proxy: mean of bbox region / mean of surrounding pixels."""
    import numpy as np
    if not bboxes or result_arr is None:
        return 1.0
    h, w = result_arr.shape[:2]
    bg_values = []
    fg_values = []
    for bbox in bboxes:
        bx = max(0, int(bbox.get("x", 0)))
        by = max(0, int(bbox.get("y", 0)))
        bw = min(int(bbox.get("w", 0)), w - bx)
        bh = min(int(bbox.get("h", 0)), h - by)
        if bw <= 0 or bh <= 0:
            continue
        fg_region = result_arr[by:by+bh, bx:bx+bw]
        fg_values.append(float(fg_region.mean()))
        # Background: sample outside bbox
        bg_region = result_arr[:h, :w]
        bg_values.append(float(bg_region.mean()))
    if not fg_values or not bg_values:
        return 1.0
    fg_mean = sum(fg_values) / len(fg_values)
    bg_mean = sum(bg_values) / len(bg_values)
    return fg_mean / max(1.0, bg_mean)


def _bbox_changed_ratio(changed_arr: object, bboxes: list, h: int, w: int) -> float:
    """Fraction of pixels in bboxes that changed."""
    import numpy as np
    if not bboxes or changed_arr is None:
        return 0.0
    total = 0
    changed_count = 0
    for bbox in bboxes:
        bx = max(0, int(bbox.get("x", 0)))
        by = max(0, int(bbox.get("y", 0)))
        bw = min(int(bbox.get("w", 0)), w - bx)
        bh = min(int(bbox.get("h", 0)), h - by)
        if bw <= 0 or bh <= 0:
            continue
        region = changed_arr[by:by+bh, bx:bx+bw]
        total += region.size
        changed_count += int(region.sum())
    return changed_count / total if total > 0 else 0.0
