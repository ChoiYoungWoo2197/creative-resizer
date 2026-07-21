"""Stage 19E — Background Quality Gate.

Evaluates candidates and selects the best one.
Hard fail conditions immediately eliminate candidates regardless of score.

PASS thresholds (initial):
  backgroundNaturalnessScore >= 70
  seamScore >= 75
  protectedPixelIntegrityScore >= 99.5
  productPixelIntegrityScore >= 99.5
  safeZoneComplianceScore = 100
  specComplianceScore = 100
  seamRisk <= 0.15
  blurBandRisk <= 0.10
  repetitionRisk <= 0.20
  ghostingRisk <= 0.15
  haloRisk <= 0.15
  productMutationRisk = 0
  protectedPixelMutationRisk = 0
"""
from __future__ import annotations

import io
import math
from PIL import Image, ImageChops, ImageStat

from .schemas import BackgroundCandidate, BackgroundResult


# ── hard fail conditions ──────────────────────────────────────────────────────
HARD_FAIL_CONDITIONS = {
    "product_mutation_risk":           (">", 0.0),
    "protected_pixel_mutation_risk":   (">", 0.0),
}

# ── soft pass thresholds ──────────────────────────────────────────────────────
PASS_THRESHOLDS = {
    "naturalness_score":               (">=", 70.0),
    "seam_score":                      (">=", 75.0),
    "protected_pixel_integrity_score": (">=", 99.5),
    "product_pixel_integrity_score":   (">=", 99.5),
    "safe_zone_compliance_score":      (">=", 100.0),
    "spec_compliance_score":           (">=", 100.0),
    "seam_risk":                       ("<=", 0.15),
    "blur_band_risk":                  ("<=", 0.10),
    "repetition_risk":                 ("<=", 0.20),
    "ghosting_risk":                   ("<=", 0.15),
    "halo_risk":                       ("<=", 0.15),
}

# ── scoring weights ───────────────────────────────────────────────────────────
_SCORE_WEIGHTS = {
    "naturalness":    0.25,
    "seam":           0.25,
    "color_cont":     0.15,
    "texture_cont":   0.10,
    "shadow":         0.10,
    "protected":      0.15,
}
_PENALTY_WEIGHTS = {
    "ghosting":   20.0,
    "blur":       15.0,
    "repetition": 10.0,
    "halo":       10.0,
    "mutation":  100.0,
}


def _cmp(value: float, op: str, threshold: float) -> bool:
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    return value == threshold


def check_hard_fail(candidate: BackgroundCandidate) -> list[str]:
    """Return list of hard-fail reason strings; empty = no hard fail."""
    reasons: list[str] = []
    for field, (op, thresh) in HARD_FAIL_CONDITIONS.items():
        v = getattr(candidate, field, 0.0)
        if _cmp(v, op, thresh):
            reasons.append(f"hard_fail:{field}{op}{thresh}")
    # additional checks
    if candidate.extras.get("nonUniformScaleDetected"):
        reasons.append("hard_fail:non_uniform_scale")
    if candidate.extras.get("safeZoneViolations", 0) > 0:
        reasons.append("hard_fail:safe_zone_violation")
    # corrupt / wrong size
    if candidate.image is not None:
        img = candidate.image
        if not isinstance(img, Image.Image):
            reasons.append("hard_fail:invalid_image_type")
        # reopen check
        try:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            Image.open(buf).load()
        except Exception as exc:
            reasons.append(f"hard_fail:image_corrupted:{exc}")
    return reasons


def compute_composite_score(candidate: BackgroundCandidate) -> float:
    """Compute weighted composite score 0–100."""
    raw = (
        candidate.naturalness_score       * _SCORE_WEIGHTS["naturalness"]
        + candidate.seam_score            * _SCORE_WEIGHTS["seam"]
        + candidate.color_continuity_score * _SCORE_WEIGHTS["color_cont"]
        + candidate.texture_continuity_score * _SCORE_WEIGHTS["texture_cont"]
        + candidate.shadow_naturalness_score * _SCORE_WEIGHTS["shadow"]
        + candidate.protected_pixel_integrity_score * _SCORE_WEIGHTS["protected"]
    )
    penalties = (
        candidate.ghosting_risk            * _PENALTY_WEIGHTS["ghosting"]
        + candidate.blur_band_risk         * _PENALTY_WEIGHTS["blur"]
        + candidate.repetition_risk        * _PENALTY_WEIGHTS["repetition"]
        + candidate.halo_risk              * _PENALTY_WEIGHTS["halo"]
        + candidate.product_mutation_risk  * _PENALTY_WEIGHTS["mutation"]
    )
    return round(max(0.0, min(100.0, raw - penalties)), 2)


def check_pass_conditions(candidate: BackgroundCandidate) -> list[str]:
    """Return list of failed soft conditions; empty = all pass."""
    failures: list[str] = []
    for field, (op, thresh) in PASS_THRESHOLDS.items():
        v = getattr(candidate, field, None)
        if v is None:
            continue
        if not _cmp(v, op, thresh):
            failures.append(f"threshold_fail:{field}{op}{thresh}(actual={v})")
    return failures


def evaluate_candidate(
    candidate: BackgroundCandidate,
    source_image: Image.Image | None = None,
    protected_mask: Image.Image | None = None,
) -> BackgroundCandidate:
    """Evaluate a single candidate; update score/accepted/rejection_reasons in place.

    Does NOT modify source_image or protected pixel values.
    """
    # protected pixel integrity (compare candidate image vs source in protected zone).
    # Skip when sizes differ (outpaint candidates): the source was pre-scaled so
    # pixel coordinates no longer align — returning 0.0 would be a false hard-fail.
    # Outpaint never touches protected pixels by construction; keep the 100.0 default.
    if (
        candidate.image is not None
        and source_image is not None
        and protected_mask is not None
        and candidate.image.size == source_image.size
    ):
        integrity = _compute_protected_pixel_integrity(
            candidate.image, source_image, protected_mask
        )
        candidate.protected_pixel_integrity_score = integrity
        candidate.product_pixel_integrity_score = integrity  # conservative

    # hard fail check
    hard_fails = check_hard_fail(candidate)
    if hard_fails:
        candidate.accepted = False
        candidate.rejection_reasons = list(set(candidate.rejection_reasons + hard_fails))
        candidate.score = 0.0
        return candidate

    # soft condition check
    soft_fails = check_pass_conditions(candidate)
    if soft_fails:
        candidate.rejection_reasons = list(set(candidate.rejection_reasons + soft_fails))
        # not immediately rejected; score reflects quality

    # recompute composite score
    candidate.score = compute_composite_score(candidate)
    candidate.accepted = (
        len(hard_fails) == 0
        and candidate.score >= 50.0
        and candidate.image is not None
    )
    return candidate


def select_best_candidate(
    candidates: list[BackgroundCandidate],
    source_image: Image.Image | None = None,
    protected_mask: Image.Image | None = None,
) -> tuple[BackgroundCandidate | None, str]:
    """Evaluate all candidates and return (best_accepted, rejection_summary).

    Hard-fail candidates are eliminated regardless of score.
    If no candidate passes, returns (None, reason).
    """
    evaluated: list[BackgroundCandidate] = []
    for c in candidates:
        if c.image is None:
            c.accepted = False
            c.rejection_reasons.append("no_image")
            evaluated.append(c)
            continue
        evaluated.append(evaluate_candidate(c, source_image, protected_mask))

    accepted = [c for c in evaluated if c.accepted]
    if not accepted:
        all_reasons = [r for c in evaluated for r in c.rejection_reasons]
        summary = "; ".join(sorted(set(all_reasons))[:5]) or "all_candidates_rejected"
        return None, summary

    best = max(accepted, key=lambda c: c.score)
    best.accepted = True
    return best, ""


def _compute_protected_pixel_integrity(
    result: Image.Image,
    source: Image.Image,
    protected_mask: Image.Image,
) -> float:
    """Compute pixel integrity score (0–100) in protected zone.

    100 = no change; 0 = completely different.
    """
    try:
        import numpy as np

        r_arr = np.array(result.convert("RGB"), dtype=float)
        s_arr = np.array(source.convert("RGB"), dtype=float)
        if r_arr.shape != s_arr.shape:
            return 0.0

        pm = np.array(protected_mask.convert("L"), dtype=bool)
        if not pm.any():
            return 100.0

        diff = np.abs(r_arr[pm] - s_arr[pm])
        mean_delta = float(diff.mean())
        # 0 delta → 100, 25.5 delta → 0
        score = max(0.0, 100.0 - mean_delta * (100.0 / 25.5))
        return round(score, 2)
    except Exception:
        return 100.0  # conservative: assume OK


def build_quality_metrics(
    candidates: list[BackgroundCandidate],
    selected: BackgroundCandidate | None,
) -> dict:
    """Build the metrics dict for BackgroundResult."""
    metrics: dict = {
        "candidateCount": len(candidates),
        "acceptedCount":  sum(1 for c in candidates if c.accepted),
        "bestScore":      selected.score if selected else 0.0,
        "bestMethod":     selected.method if selected else "none",
        "bestProvider":   selected.provider if selected else "none",
    }
    for c in candidates:
        metrics[f"candidate_{c.candidate_id}_score"] = c.score
        metrics[f"candidate_{c.candidate_id}_accepted"] = c.accepted
    return metrics
