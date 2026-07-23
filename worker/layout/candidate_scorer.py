"""Stage 21 Bundle B: Deterministic candidate scoring.

Lower total score = better candidate.
Tie-breaking by candidateId (lexicographic) is done in reflow_engine.

D-3 additions:
  originalPreservationPenalty — penalizes candidates that keep objects at
      proportionally-scaled source positions when the target ratio has changed
      significantly (i.e. candidate_C excessive source-layout preservation).
  targetAdaptationPenalty     — proportional to how far a candidate deviates
      from source layout in aspect-ratio-adjusted coordinates.
  readabilityPenalty          — title/CTA objects too small relative to canvas.
"""
from __future__ import annotations

import math

from layout.models import ObjectPlacement, CandidateScore
from layout.safe_zone_validator import (
    validate_placement,
    compute_clipping_ratio,
    compute_overlap_ratio,
)
from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES, CANVAS_BLEED_ROLES

# Roles whose readability matters (text/CTA)
_TEXT_ROLES = frozenset({"title", "headline", "body_text", "cta"})
_REQUIRED_ROLES = frozenset({"product", "title", "headline", "body_text", "cta", "logo"})

# Aspect ratio change threshold above which preservation penalty kicks in
_ASPECT_CHANGE_THRESHOLD = 0.25  # 25% relative change

# Min readable relative height for text objects (fraction of target_h)
_MIN_TEXT_HEIGHT_RATIO = 0.05


def _aspect_ratio(w: int, h: int) -> float:
    return w / h if h > 0 else 1.0


def _compute_source_preservation_penalty(
    placements: list,
    canvas_w: int,
    canvas_h: int,
    target_w: int,
    target_h: int,
) -> tuple[float, float]:
    """Compute originalPreservationPenalty and targetAdaptationPenalty.

    When source and target have very different aspect ratios,
    simply scaling source positions to target coords is a poor layout strategy.
    candidate_C (_place_proportional) does exactly this — it deserves a penalty
    proportional to how much the aspect ratio changed AND how little positions moved.

    Returns (preservation_penalty, adaptation_penalty).
    """
    if canvas_w <= 0 or canvas_h <= 0 or target_w <= 0 or target_h <= 0:
        return 0.0, 0.0

    src_ar = _aspect_ratio(canvas_w, canvas_h)
    tgt_ar = _aspect_ratio(target_w, target_h)
    ar_change = abs(tgt_ar - src_ar) / max(src_ar, 0.001)

    if ar_change < _ASPECT_CHANGE_THRESHOLD:
        # Source and target ratios are similar — no penalty
        return 0.0, 0.0

    # Measure how much positions actually changed from proportional-scale reference
    displacement_scores: list[float] = []
    for p in placements:
        role = p.semanticRole or p.layoutRole
        if role in CANVAS_BLEED_ROLES:
            continue
        orig = p.originalTargetBBox
        actual = p.targetBBox
        if not orig or not actual:
            continue

        # Expected position under pure proportional scale (candidate_C baseline)
        # Actual deviation from this: the MORE it deviates, the BETTER adapted
        orig_cx = orig.get("x", 0) + orig.get("width", 0) / 2
        orig_cy = orig.get("y", 0) + orig.get("height", 0) / 2
        act_cx = actual.get("x", 0) + actual.get("width", 0) / 2
        act_cy = actual.get("y", 0) + actual.get("height", 0) / 2

        dx = abs(act_cx - orig_cx) / max(target_w, 1)
        dy = abs(act_cy - orig_cy) / max(target_h, 1)
        displacement = math.sqrt(dx * dx + dy * dy)
        displacement_scores.append(displacement)

    if not displacement_scores:
        return 0.0, 0.0

    avg_displacement = sum(displacement_scores) / len(displacement_scores)

    # Preservation penalty: high when ar changed a lot but positions barely moved
    # Max preservation penalty = ar_change * 15 (bounded)
    preservation_penalty = max(0.0, ar_change * 15 - avg_displacement * 20)
    preservation_penalty = min(preservation_penalty, ar_change * 15)

    # Adaptation penalty (inverse): small when positions actually moved a lot
    # Rewards candidates that adapted to the new target ratio
    adaptation_penalty = max(0.0, ar_change * 8 - avg_displacement * 15)

    return round(preservation_penalty, 2), round(adaptation_penalty, 2)


def _compute_readability_penalty(
    placements: list,
    target_w: int,
    target_h: int,
) -> float:
    """Penalize when title/CTA objects are too small relative to canvas."""
    penalty = 0.0
    for p in placements:
        role = p.semanticRole or p.layoutRole
        if role not in _TEXT_ROLES:
            continue
        h = p.targetBBox.get("height", 0)
        ratio = h / target_h if target_h > 0 else 0
        if ratio < _MIN_TEXT_HEIGHT_RATIO:
            shortage = _MIN_TEXT_HEIGHT_RATIO - ratio
            penalty += shortage * 30
    return round(penalty, 2)


def score_candidate(
    candidate_id: str,
    placements: list,  # list[ObjectPlacement]
    safe_x1: int,
    safe_y1: int,
    safe_x2: int,
    safe_y2: int,
    target_w: int,
    target_h: int,
    original_visual_side: str = "left",
    canvas_w: int = 0,
    canvas_h: int = 0,
) -> CandidateScore:
    """Compute deterministic score for a list of ObjectPlacements.

    Scoring components (all penalties — lower is better):
      hardFailureCount           — hard constraint violations
      safeZoneViolationCount
      clippingViolationCount
      overlapViolationCount
      sizePenalty                — objects too small (scale < 0.15)
      balancePenalty             — foreground COM far from canvas center
      originalRelationPenalty    — hero on wrong side vs original
      originalPreservationPenalty — D-3: too much source-layout preservation
                                    when target aspect ratio changed
      targetAdaptationPenalty    — D-3: insufficient reflow for new target ratio
      readabilityPenalty         — D-3: text/CTA objects too small
    """
    hard_fails = 0
    sz_violations = 0
    clip_violations = 0
    overlap_violations = 0
    size_penalty = 0.0
    balance_penalty = 0.0
    relation_penalty = 0.0

    for p in placements:
        check = validate_placement(
            p, placements, safe_x1, safe_y1, safe_x2, safe_y2, target_w, target_h
        )
        if check["hardFail"]:
            hard_fails += 1
        if not check["safeZonePassed"] and p.safeZoneRequired:
            sz_violations += 1
        if check["clippingRatio"] > 0.1 and p.layoutRole in SAFE_ZONE_REQUIRED_ROLES:
            clip_violations += 1
        if check["overlapObjectIds"]:
            overlap_violations += 1

        # Size penalty: required object whose scale is very small
        if p.required and p.scale < 0.15:
            size_penalty += (0.15 - p.scale) * 100

    # Balance: center-of-mass of all non-decorative bboxes
    non_deco = [p for p in placements if p.layoutRole not in CANVAS_BLEED_ROLES]
    if non_deco:
        cx_sum = sum(
            p.targetBBox.get("x", 0) + p.targetBBox.get("width", 0) / 2
            for p in non_deco
        )
        cx_avg = cx_sum / len(non_deco)
        canvas_cx = target_w / 2
        imbalance = abs(cx_avg - canvas_cx) / canvas_cx
        if imbalance > 0.4:
            balance_penalty = imbalance * 10

    # Original relation: hero on wrong side
    hero_roles = {"product", "main_image", "human_subject", "person"}
    hero_placements = [p for p in placements if p.layoutRole in hero_roles or p.semanticRole in hero_roles]
    if hero_placements and canvas_w > 0:
        hero = max(hero_placements, key=lambda p: p.targetBBox.get("width", 0) * p.targetBBox.get("height", 0))
        hero_cx = hero.targetBBox.get("x", 0) + hero.targetBBox.get("width", 0) / 2
        hero_on_left = hero_cx < target_w / 2
        if original_visual_side == "left" and not hero_on_left:
            relation_penalty = 5.0
        elif original_visual_side == "right" and hero_on_left:
            relation_penalty = 5.0

    # D-3: source preservation and target adaptation penalties
    preservation_penalty, adaptation_penalty = _compute_source_preservation_penalty(
        placements, canvas_w, canvas_h, target_w, target_h
    )

    # D-3: readability penalty
    readability_penalty = _compute_readability_penalty(placements, target_w, target_h)

    total = (
        hard_fails * 1000
        + sz_violations * 50
        + clip_violations * 30
        + overlap_violations * 20
        + size_penalty
        + balance_penalty
        + relation_penalty
        + preservation_penalty
        + adaptation_penalty
        + readability_penalty
    )

    return CandidateScore(
        candidateId=candidate_id,
        hardFailureCount=hard_fails,
        safeZoneViolationCount=sz_violations,
        clippingViolationCount=clip_violations,
        overlapViolationCount=overlap_violations,
        sizePenalty=round(size_penalty, 2),
        balancePenalty=round(balance_penalty, 2),
        originalRelationPenalty=round(relation_penalty, 2),
        originalPreservationPenalty=preservation_penalty,
        targetAdaptationPenalty=adaptation_penalty,
        readabilityPenalty=readability_penalty,
        total=round(total, 2),
    )
