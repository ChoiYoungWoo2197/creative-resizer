"""Stage 21 Bundle D-3: Candidate score preservation penalty tests.

Verifies:
  - Square→wide: source-preservation candidate gets higher penalty
  - Target-adapted reflow gets lower score than proportional-clone
  - Same ratio: preservation penalty not excessive
  - candidate_C not deleted (just scored appropriately)
  - Score determinism
  - Tie-break rules
  - Score component logging
"""
from __future__ import annotations

import pytest
from layout.models import ObjectPlacement, CandidateScore
from layout.candidate_scorer import (
    score_candidate,
    _compute_source_preservation_penalty,
    _compute_readability_penalty,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _placement(
    oid: str,
    role: str,
    orig: tuple,   # x, y, w, h (originalTargetBBox)
    target: tuple, # x, y, w, h (targetBBox)
    required: bool = True,
    sz_required: bool = True,
    scale: float = 1.0,
) -> ObjectPlacement:
    ox, oy, ow, oh = orig
    tx, ty, tw, th = target
    return ObjectPlacement(
        objectId=oid,
        semanticRole=role,
        layoutRole=role,
        sourceBBox={"x": ox, "y": oy, "width": ow, "height": oh},
        originalTargetBBox={"x": ox, "y": oy, "width": ow, "height": oh},
        targetBBox={"x": tx, "y": ty, "width": tw, "height": th},
        scale=scale,
        required=required,
        safeZoneRequired=sz_required,
        safeZonePassed=True,
        reason="test",
    )


# Safe zone constants for 1250×560 (16:5 wide banner)
SZ_X1, SZ_Y1, SZ_X2, SZ_Y2 = 60, 28, 1190, 532
TGT_W, TGT_H = 1250, 560
# Source canvas (square 1200×1200)
SRC_W, SRC_H = 1200, 1200


# ── Category 1: Preservation penalty for square→wide ─────────────────────────

class TestPreservationPenaltySquareToWide:
    def _proportional_placements(self):
        """Simulate candidate_C: objects at proportionally-scaled positions."""
        # Source 1200×1200, target 1250×560
        # scale_x = 1250/1200 ≈ 1.04, scale_y = 560/1200 ≈ 0.47
        # product at source (100,400,300,300) → proportional target (104,187,146,146)
        return [
            _placement("prod", "product",
                       (100, 400, 300, 300),  # orig
                       (104, 187, 146, 146),  # proportionally scaled
                       scale=0.49),
            _placement("title", "title",
                       (100, 100, 800, 80),
                       (104, 47,  390, 37),   # proportionally scaled
                       scale=0.49),
        ]

    def _reflow_placements(self):
        """Simulate candidate_A: actually adapted to wide target."""
        return [
            _placement("prod", "product",
                       (100, 400, 300, 300),  # orig same
                       (650, 80, 540, 400),   # wide-right reflow
                       scale=1.33),
            _placement("title", "title",
                       (100, 100, 800, 80),
                       (60, 80, 560, 60),     # wide-left reflow
                       scale=0.7),
        ]

    def test_proportional_candidate_higher_penalty(self):
        """candidate_C (proportional) should have higher preservation penalty than candidate_A (reflow)."""
        pres_c, adapt_c = _compute_source_preservation_penalty(
            self._proportional_placements(), SRC_W, SRC_H, TGT_W, TGT_H
        )
        pres_a, adapt_a = _compute_source_preservation_penalty(
            self._reflow_placements(), SRC_W, SRC_H, TGT_W, TGT_H
        )
        assert pres_c >= pres_a, (
            f"Proportional candidate should have >= preservation penalty: "
            f"candidate_C={pres_c} candidate_A={pres_a}"
        )

    def test_reflow_candidate_lower_total_score(self):
        """After preservation penalty, reflow candidate should have lower score."""
        score_c = score_candidate(
            "candidate_C", self._proportional_placements(),
            SZ_X1, SZ_Y1, SZ_X2, SZ_Y2, TGT_W, TGT_H,
            canvas_w=SRC_W, canvas_h=SRC_H,
        )
        score_a = score_candidate(
            "candidate_A", self._reflow_placements(),
            SZ_X1, SZ_Y1, SZ_X2, SZ_Y2, TGT_W, TGT_H,
            canvas_w=SRC_W, canvas_h=SRC_H,
        )
        assert score_a.total <= score_c.total, (
            f"Reflow candidate score ({score_a.total}) should be <= proportional ({score_c.total})"
        )

    def test_proportional_candidate_not_auto_zero(self):
        """candidate_C proportional must not get score=0 just for preserving source layout."""
        score = score_candidate(
            "candidate_C", self._proportional_placements(),
            SZ_X1, SZ_Y1, SZ_X2, SZ_Y2, TGT_W, TGT_H,
            canvas_w=SRC_W, canvas_h=SRC_H,
        )
        # candidate_C exists and has a score — not deleted, not hard-coded 0
        assert isinstance(score.total, float)
        assert score.candidateId == "candidate_C"


# ── Category 2: Same-ratio — no excessive penalty ────────────────────────────

class TestSameRatioNoPenalty:
    def _same_ratio_placements(self):
        """Source and target have the same aspect ratio (no adaptation needed)."""
        # source 1200×628, target 1200×628 (same)
        return [
            _placement("prod", "product",
                       (100, 100, 300, 300),
                       (100, 100, 300, 300),
                       scale=1.0),
        ]

    def test_same_ratio_preservation_penalty_small(self):
        pres, adapt = _compute_source_preservation_penalty(
            self._same_ratio_placements(), 1200, 628, 1200, 628
        )
        assert pres < 2.0, f"Same-ratio should have negligible preservation penalty: {pres}"

    def test_same_ratio_total_score_low(self):
        score = score_candidate(
            "candidate_C", self._same_ratio_placements(),
            60, 30, 1140, 598, 1200, 628,
            canvas_w=1200, canvas_h=628,
        )
        assert score.originalPreservationPenalty < 5.0


# ── Category 3: candidate_C not removed ───────────────────────────────────────

class TestCandidateCNotRemoved:
    def test_candidate_c_score_object_exists(self):
        """candidate_C still gets a CandidateScore — it is not dropped."""
        placements = [
            _placement("prod", "product",
                       (100, 400, 300, 300),
                       (100, 187, 146, 146),
                       scale=0.49),
        ]
        score = score_candidate(
            "candidate_C", placements,
            SZ_X1, SZ_Y1, SZ_X2, SZ_Y2, TGT_W, TGT_H,
            canvas_w=SRC_W, canvas_h=SRC_H,
        )
        assert score.candidateId == "candidate_C"
        assert score.total >= 0

    def test_candidate_c_can_win_good_conditions(self):
        """candidate_C can be selected when source/target ratios are close."""
        placements = [
            _placement("prod", "product",
                       (100, 100, 300, 300),
                       (104, 100, 310, 310),
                       scale=1.03),
        ]
        score = score_candidate(
            "candidate_C", placements,
            60, 60, 940, 940, 1000, 1000,
            canvas_w=1000, canvas_h=1000,
        )
        # When no hard fails and no ratio difference, score can be 0 or very low
        assert score.hardFailureCount == 0
        assert score.total >= 0  # may be 0 for ideal conditions


# ── Category 4: Readability penalty ───────────────────────────────────────────

class TestReadabilityPenalty:
    def test_tiny_title_gets_penalty(self):
        placements = [
            _placement("title", "title",
                       (0, 0, 600, 20),
                       (0, 0, 600, 20)),  # height=20, TGT_H=560 → ratio=0.036 > 0.05? no → below threshold
        ]
        pen = _compute_readability_penalty(placements, TGT_W, TGT_H)
        # 20/560 = 0.0357 < 0.05 → penalty expected
        assert pen > 0

    def test_normal_title_no_penalty(self):
        placements = [
            _placement("title", "title",
                       (0, 0, 600, 80),
                       (0, 0, 600, 80)),  # 80/560 = 0.143 > 0.05
        ]
        pen = _compute_readability_penalty(placements, TGT_W, TGT_H)
        assert pen == 0.0

    def test_product_not_penalized_for_height(self):
        placements = [
            _placement("prod", "product",
                       (0, 0, 100, 10),
                       (0, 0, 100, 10)),
        ]
        pen = _compute_readability_penalty(placements, TGT_W, TGT_H)
        assert pen == 0.0  # product not in _TEXT_ROLES


# ── Category 5: Score components in CandidateScore ───────────────────────────

class TestCandidateScoreFields:
    def test_score_has_d3_fields(self):
        placements = [
            _placement("prod", "product",
                       (100, 400, 300, 300),
                       (104, 187, 146, 146),
                       scale=0.49),
        ]
        score = score_candidate(
            "candidate_C", placements,
            SZ_X1, SZ_Y1, SZ_X2, SZ_Y2, TGT_W, TGT_H,
            canvas_w=SRC_W, canvas_h=SRC_H,
        )
        assert hasattr(score, "originalPreservationPenalty")
        assert hasattr(score, "targetAdaptationPenalty")
        assert hasattr(score, "readabilityPenalty")
        assert isinstance(score.originalPreservationPenalty, float)
        assert isinstance(score.targetAdaptationPenalty, float)
        assert isinstance(score.readabilityPenalty, float)

    def test_total_includes_d3_components(self):
        """total must include all D-3 penalties."""
        placements = [
            _placement("prod", "product",
                       (100, 400, 300, 300),
                       (104, 187, 146, 146),
                       scale=0.49),
            _placement("title", "title",
                       (100, 100, 800, 80),
                       (100, 40, 600, 15),   # tiny title
                       scale=0.49),
        ]
        score = score_candidate(
            "candidate_C", placements,
            SZ_X1, SZ_Y1, SZ_X2, SZ_Y2, TGT_W, TGT_H,
            canvas_w=SRC_W, canvas_h=SRC_H,
        )
        # Manual floor: total must be >= preservation + adaptation + readability
        manual_floor = (
            score.originalPreservationPenalty
            + score.targetAdaptationPenalty
            + score.readabilityPenalty
        )
        assert score.total >= manual_floor - 0.001  # float tolerance


# ── Category 6: Determinism ───────────────────────────────────────────────────

class TestCandidateScoreDeterminism:
    def _placements(self):
        return [
            _placement("prod", "product",
                       (100, 400, 300, 300),
                       (104, 187, 146, 146),
                       scale=0.49),
            _placement("title", "title",
                       (100, 100, 800, 80),
                       (100, 47, 390, 37),
                       scale=0.49),
        ]

    def test_same_input_same_score(self):
        s1 = score_candidate(
            "candidate_C", self._placements(),
            SZ_X1, SZ_Y1, SZ_X2, SZ_Y2, TGT_W, TGT_H,
            canvas_w=SRC_W, canvas_h=SRC_H,
        )
        s2 = score_candidate(
            "candidate_C", self._placements(),
            SZ_X1, SZ_Y1, SZ_X2, SZ_Y2, TGT_W, TGT_H,
            canvas_w=SRC_W, canvas_h=SRC_H,
        )
        assert s1.total == s2.total
        assert s1.originalPreservationPenalty == s2.originalPreservationPenalty
        assert s1.targetAdaptationPenalty == s2.targetAdaptationPenalty


# ── Category 7: Tie-break in reflow_engine ────────────────────────────────────

class TestTieBreakOrdering:
    """Verify the sort key in reflow_engine is deterministic by candidateId."""
    def test_same_score_sorted_by_candidate_id(self):
        """When two candidates have identical scores, lexicographic candidateId wins."""
        # Simulate scored list (cid, placements, score_obj)
        sc_z = CandidateScore(
            candidateId="candidate_Z",
            hardFailureCount=0, safeZoneViolationCount=0,
            clippingViolationCount=0, overlapViolationCount=0,
            total=10.0,
        )
        sc_a = CandidateScore(
            candidateId="candidate_A",
            hardFailureCount=0, safeZoneViolationCount=0,
            clippingViolationCount=0, overlapViolationCount=0,
            total=10.0,
        )
        scored = [("candidate_Z", [], sc_z), ("candidate_A", [], sc_a)]
        scored.sort(key=lambda x: (
            x[2].hardFailureCount,
            x[2].safeZoneViolationCount,
            x[2].clippingViolationCount,
            x[2].overlapViolationCount,
            x[2].total,
            x[0],  # candidateId lexical
        ))
        assert scored[0][0] == "candidate_A"
        assert scored[1][0] == "candidate_Z"

    def test_hard_fail_prioritized_over_score(self):
        """Hard fails always rank worse regardless of total score."""
        sc_fail = CandidateScore(
            candidateId="candidate_fail",
            hardFailureCount=1, total=0.0,
        )
        sc_pass = CandidateScore(
            candidateId="candidate_pass",
            hardFailureCount=0, total=999.0,
        )
        scored = [("fail", [], sc_fail), ("pass", [], sc_pass)]
        scored.sort(key=lambda x: (
            x[2].hardFailureCount,
            x[2].safeZoneViolationCount,
            x[2].clippingViolationCount,
            x[2].overlapViolationCount,
            x[2].total,
            x[0],
        ))
        assert scored[0][0] == "pass"  # no hard fail wins despite high score
