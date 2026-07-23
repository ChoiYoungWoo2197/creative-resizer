"""Stage 21 Bundle B: Deterministic candidate scoring.

Lower total score = better candidate.
Tie-breaking by candidateId (lexicographic) is done in reflow_engine.
"""
from __future__ import annotations

from layout.models import ObjectPlacement, CandidateScore
from layout.safe_zone_validator import (
    validate_placement,
    compute_clipping_ratio,
    compute_overlap_ratio,
)
from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES, CANVAS_BLEED_ROLES


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
      hardFailureCount      — hard constraint violations
      safeZoneViolationCount
      clippingViolationCount
      overlapViolationCount
      sizePenalty           — objects too small
      balancePenalty        — foreground objects concentrated on one side
      originalRelationPenalty — hero placed on wrong side vs original
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
        # Penalty if average center is more than 40% off from canvas center
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

    total = (
        hard_fails * 1000
        + sz_violations * 50
        + clip_violations * 30
        + overlap_violations * 20
        + size_penalty
        + balance_penalty
        + relation_penalty
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
        total=round(total, 2),
    )
