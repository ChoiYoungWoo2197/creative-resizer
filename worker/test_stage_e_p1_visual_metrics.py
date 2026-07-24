"""Stage E P1-D: Extended visual verdict metrics tests.

Verifies compute_extended_visual_metrics() and evaluate_extended_visual():

compute_extended_visual_metrics:
  1. Returns all 15 metric keys with correct defaults when no inputs provided
  2. blankOutputScore=1.0 when result_img=None
  3. blankOutputScore near 0 for varied-content image
  4. sceneSimilarityScore=1.0 for identical source/result
  5. sceneSimilarityScore<1.0 when images differ
  6. immutableChangedPixelRatio=0 when immutable region is unchanged
  7. immutableChangedPixelRatio>0 when immutable region pixels changed
  8. outsideAllowedChangedPixelRatio=0 when all changes inside allowed mask
  9. outsideAllowedChangedPixelRatio>0 when change outside allowed mask
  10. duplicateObjectCount passed through correctly
  11. groupCompletenessRatio passed through correctly
  12. faceOcclusionRatio>0 when face bbox pixels changed in result
  13. handOcclusionRatio>0 when hand bbox pixels changed in result

evaluate_extended_visual:
  14. Returns PASS when all metrics within thresholds
  15. Returns FAIL with IMMUTABLE_PIXELS_CHANGED when ratio exceeded
  16. Returns FAIL with OUTSIDE_ALLOWED_REGION_CHANGED when exceeded
  17. Returns FAIL with FACE_OCCLUSION_EXCEEDED when face ratio exceeded
  18. Returns FAIL with HAND_OCCLUSION_EXCEEDED when hand ratio exceeded
  19. Returns FAIL with SEMANTIC_GROUP_INCOMPLETE when group completeness low
  20. Returns FAIL with DUPLICATE_OBJECT_COMPOSITION when duplicates>0
  21. Returns FAIL with BLANK_OUTPUT_DETECTED when output blank
  22. Base E-4 FAIL propagates through extended evaluator
  23. Log emitted with [VERDICT_VISUAL] including metrics in evidence
  24. reason_codes constants have correct values
  25. All 15 metric keys present in evaluate_extended_visual evidence

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image


# ── Helpers ───────────────────────────────────────────────────────────────────

def _blank_img(w=200, h=100):
    """Uniform black image — very low variance."""
    return Image.new("RGB", (w, h), color=(0, 0, 0))


def _solid_img(w=200, h=100, color=(128, 64, 32)):
    return Image.new("RGB", (w, h), color=color)


def _near_blank_img(w=200, h=100, seed=42):
    """Very low variance (≈10) but > 5.0 E-4 threshold → passes E-4 blank check.
    P1-D blankOutputScore = 1 - 10/500 = 0.98 > 0.9 → triggers BLANK_OUTPUT_DETECTED."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 3.16, (h, w, 3))
    arr = np.clip(128 + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _locally_changed_img(src_img, x, y, w, h, fill=(0, 200, 100)):
    """Return copy of src_img with a small rectangular region filled with `fill`."""
    import copy
    res = src_img.copy()
    from PIL import ImageDraw
    d = ImageDraw.Draw(res)
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=fill)
    return res


def _noisy_img(w=200, h=100, seed=42):
    """Random-noise image — high variance."""
    rng = np.random.default_rng(seed)
    arr = (rng.random((h, w, 3)) * 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _half_changed_img(w=200, h=100, seed=99):
    """Left half black, right half noise — half differs from a solid source."""
    rng = np.random.default_rng(seed)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, w//2:, :] = (rng.random((h, w//2, 3)) * 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _full_mask(w, h):
    """All-white (allowed) mask."""
    return Image.new("L", (w, h), 255)


def _partial_mask(w, h, left_allowed_ratio=0.5):
    """Left portion allowed, right portion immutable."""
    arr = np.zeros((h, w), dtype=np.uint8)
    arr[:, :int(w * left_allowed_ratio)] = 255
    return Image.fromarray(arr, "L")


def _bbox(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def _compute(**kwargs):
    from verdict.visual_evaluator import compute_extended_visual_metrics
    return compute_extended_visual_metrics(**kwargs)


def _evaluate(**kwargs):
    from verdict.visual_evaluator import evaluate_extended_visual
    return evaluate_extended_visual(**kwargs)


# ── T1: Default keys ──────────────────────────────────────────────────────────

class TestDefaultKeys:
    EXPECTED_KEYS = {
        "immutableChangedPixelRatio",
        "outsideAllowedChangedPixelRatio",
        "sceneSimilarityScore",
        "backgroundSemanticDriftScore",
        "fullSceneRegenerationScore",
        "productVisibilityRatio",
        "titleVisibilityRatio",
        "ctaVisibilityRatio",
        "titleContrastRatio",
        "ctaContrastRatio",
        "faceOcclusionRatio",
        "handOcclusionRatio",
        "groupCompletenessRatio",
        "duplicateObjectCount",
        "blankOutputScore",
    }

    def test_all_15_keys_present_no_inputs(self):
        result_img = _solid_img()
        m = _compute(result_img=result_img)
        assert set(m.keys()) >= self.EXPECTED_KEYS, f"Missing: {self.EXPECTED_KEYS - set(m.keys())}"

    def test_all_15_keys_present_full_inputs(self):
        src = _noisy_img()
        res = _noisy_img(seed=1)
        m = _compute(
            canonical_img=src,
            result_img=res,
            allowed_generation_mask=_full_mask(200, 100),
            immutable_mask=_partial_mask(200, 100, 0.5),
            product_bboxes=[_bbox(0, 0, 50, 50)],
            title_bboxes=[_bbox(50, 0, 50, 50)],
            cta_bboxes=[_bbox(100, 0, 50, 50)],
            face_bboxes=[_bbox(0, 0, 20, 20)],
            hand_bboxes=[_bbox(20, 0, 20, 20)],
            group_completeness_ratio=0.8,
            duplicate_object_count=1,
        )
        assert set(m.keys()) >= self.EXPECTED_KEYS


# ── T2: Blank detection ────────────────────────────────────────────────────────

class TestBlankDetection:
    def test_blank_score_1_when_no_result(self):
        m = _compute(result_img=None)
        assert m["blankOutputScore"] == 1.0

    def test_blank_score_near_1_for_uniform_image(self):
        """Uniform solid color has variance=0 → blankOutputScore=1.0."""
        m = _compute(result_img=_blank_img())
        assert m["blankOutputScore"] > 0.9

    def test_blank_score_near_0_for_varied_image(self):
        """Random noise has high variance → blankOutputScore near 0."""
        m = _compute(result_img=_noisy_img())
        assert m["blankOutputScore"] < 0.1


# ── T3: Scene similarity ───────────────────────────────────────────────────────

class TestSceneSimilarity:
    def test_identical_images_similarity_1(self):
        img = _noisy_img(seed=7)
        m = _compute(canonical_img=img, result_img=img)
        assert m["sceneSimilarityScore"] == pytest.approx(1.0, abs=0.01)

    def test_different_images_similarity_less_than_1(self):
        src = _solid_img(color=(0, 0, 0))
        res = _noisy_img(seed=5)
        m = _compute(canonical_img=src, result_img=res)
        assert m["sceneSimilarityScore"] < 0.9

    def test_full_regen_score_is_complement_of_similarity(self):
        src = _solid_img(color=(10, 10, 10))
        res = _noisy_img(seed=3)
        m = _compute(canonical_img=src, result_img=res)
        assert m["fullSceneRegenerationScore"] == pytest.approx(
            1.0 - m["sceneSimilarityScore"], abs=0.001
        )


# ── T4: Immutable pixel ratio ──────────────────────────────────────────────────

class TestImmutablePixelRatio:
    def test_zero_when_immutable_region_unchanged(self):
        """Result identical to canonical → immutable ratio = 0."""
        img = _noisy_img(seed=11)
        immutable = _partial_mask(200, 100, 0.5)  # left half immutable
        m = _compute(
            canonical_img=img,
            result_img=img,
            immutable_mask=immutable,
        )
        assert m["immutableChangedPixelRatio"] == pytest.approx(0.0, abs=0.001)

    def test_nonzero_when_immutable_region_changed(self):
        """Left half changed while left half is marked immutable → nonzero ratio."""
        src = _solid_img(color=(0, 0, 0))
        # Result has noise everywhere → immutable left half changed
        res = _noisy_img(seed=15)
        immutable = _partial_mask(200, 100, 0.5)
        m = _compute(
            canonical_img=src,
            result_img=res,
            immutable_mask=immutable,
        )
        assert m["immutableChangedPixelRatio"] > 0.0


# ── T5: Outside-allowed ratio ─────────────────────────────────────────────────

class TestOutsideAllowedRatio:
    def test_zero_when_all_changes_inside_allowed(self):
        """Identical source and result → no changes anywhere."""
        img = _noisy_img(seed=20)
        m = _compute(
            canonical_img=img,
            result_img=img,
            allowed_generation_mask=_partial_mask(200, 100, 0.5),
        )
        assert m["outsideAllowedChangedPixelRatio"] == pytest.approx(0.0, abs=0.001)

    def test_nonzero_when_outside_allowed_changed(self):
        """Source solid black, result is noise. Allowed=left half, right changes → ratio>0."""
        src = _solid_img(color=(0, 0, 0))
        res = _noisy_img(seed=22)
        # Only left half is allowed generation, but right half also changed
        allowed = _partial_mask(200, 100, 0.5)
        m = _compute(
            canonical_img=src,
            result_img=res,
            allowed_generation_mask=allowed,
        )
        assert m["outsideAllowedChangedPixelRatio"] > 0.0


# ── T6: Passthrough metrics ───────────────────────────────────────────────────

class TestPassthroughMetrics:
    def test_duplicate_object_count_passthrough(self):
        m = _compute(result_img=_noisy_img(), duplicate_object_count=3)
        assert m["duplicateObjectCount"] == 3

    def test_group_completeness_passthrough(self):
        m = _compute(result_img=_noisy_img(), group_completeness_ratio=0.75)
        assert m["groupCompletenessRatio"] == pytest.approx(0.75, abs=0.001)

    def test_default_group_completeness_is_1(self):
        m = _compute(result_img=_noisy_img())
        assert m["groupCompletenessRatio"] == pytest.approx(1.0, abs=0.001)


# ── T7: Occlusion ratios ──────────────────────────────────────────────────────

class TestOcclusionRatios:
    def test_face_occlusion_nonzero_when_face_region_changed(self):
        src = _solid_img(color=(0, 0, 0))
        res = _noisy_img(seed=30)
        face_bboxes = [_bbox(0, 0, 50, 50)]
        m = _compute(
            canonical_img=src,
            result_img=res,
            face_bboxes=face_bboxes,
        )
        assert m["faceOcclusionRatio"] > 0.0

    def test_hand_occlusion_nonzero_when_hand_region_changed(self):
        src = _solid_img(color=(0, 0, 0))
        res = _noisy_img(seed=31)
        hand_bboxes = [_bbox(100, 50, 50, 30)]
        m = _compute(
            canonical_img=src,
            result_img=res,
            hand_bboxes=hand_bboxes,
        )
        assert m["handOcclusionRatio"] > 0.0

    def test_face_occlusion_zero_when_face_unchanged(self):
        img = _noisy_img(seed=40)
        face_bboxes = [_bbox(0, 0, 50, 50)]
        m = _compute(
            canonical_img=img,
            result_img=img,
            face_bboxes=face_bboxes,
        )
        assert m["faceOcclusionRatio"] == pytest.approx(0.0, abs=0.001)


# ── T8: evaluate_extended_visual ─────────────────────────────────────────────

class TestEvaluateExtendedVisual:
    def _pass_result(self, **kwargs):
        src = _noisy_img(seed=50)
        return _evaluate(
            source_img=src,
            result_img=src,
            target_w=200,
            target_h=100,
            **kwargs,
        )

    def test_pass_when_all_within_thresholds(self):
        r = self._pass_result()
        from verdict.models import PASS
        assert r.status == PASS

    def test_fail_immutable_changed(self):
        """Small change (10%) inside the immutable region → IMMUTABLE_PIXELS_CHANGED.
        Uses locally changed image so E-4 diff check (< 85%) passes first."""
        from verdict.models import FAIL
        from verdict.visual_evaluator import REASON_IMMUTABLE_CHANGED
        src = _noisy_img(seed=55)
        # Change only top-left 50x50 region (12.5% of 200x100 canvas — below 85% E-4 threshold)
        res = _locally_changed_img(src, x=0, y=0, w=50, h=50, fill=(0, 0, 200))
        # Mark that region as immutable
        imm_arr = np.zeros((100, 200), dtype=np.uint8)
        imm_arr[:50, :50] = 255
        immutable = Image.fromarray(imm_arr, "L")
        r = _evaluate(
            source_img=src,
            result_img=res,
            target_w=200,
            target_h=100,
            immutable_mask=immutable,
            immutable_threshold=0.0,
        )
        assert r.status == FAIL
        assert REASON_IMMUTABLE_CHANGED in r.reasonCodes

    def test_fail_outside_allowed(self):
        """Change in right half (not allowed) → OUTSIDE_ALLOWED_REGION_CHANGED."""
        from verdict.models import FAIL
        from verdict.visual_evaluator import REASON_OUTSIDE_ALLOWED
        src = _noisy_img(seed=60)
        # Change right 50x50 region; allowed mask covers only left half
        res = _locally_changed_img(src, x=150, y=0, w=50, h=50, fill=(200, 0, 0))
        allowed = _partial_mask(200, 100, 0.5)  # left half allowed, right half not
        r = _evaluate(
            source_img=src,
            result_img=res,
            target_w=200,
            target_h=100,
            allowed_generation_mask=allowed,
            outside_allowed_threshold=0.0,
        )
        assert r.status == FAIL
        assert REASON_OUTSIDE_ALLOWED in r.reasonCodes

    def test_fail_face_occlusion(self):
        """Localized change overlaps face bbox → FACE_OCCLUSION_EXCEEDED (threshold=0.0)."""
        from verdict.models import FAIL
        from verdict.visual_evaluator import REASON_FACE_OCCLUSION
        src = _noisy_img(seed=65)
        res = _locally_changed_img(src, x=0, y=0, w=50, h=50, fill=(100, 200, 50))
        face_bboxes = [_bbox(0, 0, 50, 50)]
        r = _evaluate(
            source_img=src,
            result_img=res,
            target_w=200,
            target_h=100,
            face_bboxes=face_bboxes,
            face_occlusion_threshold=0.0,
        )
        assert r.status == FAIL
        assert REASON_FACE_OCCLUSION in r.reasonCodes

    def test_fail_hand_occlusion(self):
        """Localized change overlaps hand bbox → HAND_OCCLUSION_EXCEEDED (threshold=0.0)."""
        from verdict.models import FAIL
        from verdict.visual_evaluator import REASON_HAND_OCCLUSION
        src = _noisy_img(seed=70)
        res = _locally_changed_img(src, x=10, y=10, w=40, h=40, fill=(50, 150, 200))
        hand_bboxes = [_bbox(10, 10, 40, 40)]
        r = _evaluate(
            source_img=src,
            result_img=res,
            target_w=200,
            target_h=100,
            hand_bboxes=hand_bboxes,
            hand_occlusion_threshold=0.0,
        )
        assert r.status == FAIL
        assert REASON_HAND_OCCLUSION in r.reasonCodes

    def test_fail_group_incomplete(self):
        src = _noisy_img(seed=75)
        r = _evaluate(
            source_img=src,
            result_img=src,
            target_w=200,
            target_h=100,
            group_completeness_ratio=0.5,
            group_completeness_min=1.0,
        )
        from verdict.models import FAIL
        from verdict.visual_evaluator import REASON_GROUP_INCOMPLETE
        assert r.status == FAIL
        assert REASON_GROUP_INCOMPLETE in r.reasonCodes

    def test_fail_duplicate_object(self):
        src = _noisy_img(seed=80)
        r = _evaluate(
            source_img=src,
            result_img=src,
            target_w=200,
            target_h=100,
            duplicate_object_count=2,
        )
        from verdict.models import FAIL
        from verdict.visual_evaluator import REASON_DUPLICATE_OBJECT
        assert r.status == FAIL
        assert REASON_DUPLICATE_OBJECT in r.reasonCodes

    def test_fail_blank_output(self):
        """Near-blank result (variance ≈ 10, above E-4 threshold of 5) triggers
        P1-D BLANK_OUTPUT_DETECTED (blankOutputScore > 0.9 when variance < 50)."""
        from verdict.models import FAIL
        from verdict.visual_evaluator import REASON_BLANK_OUTPUT
        # Both source and result are near-blank (low variance, passes E-4) so
        # pixelDiffRatio is also low — only the P1-D blank check fires.
        src = _near_blank_img(seed=85)
        res = _near_blank_img(seed=86)
        r = _evaluate(
            source_img=src,
            result_img=res,
            target_w=200,
            target_h=100,
        )
        assert r.status == FAIL
        assert REASON_BLANK_OUTPUT in r.reasonCodes

    def test_base_e4_fail_propagates(self):
        """Wrong dimensions → base evaluate_visual returns FAIL → extended propagates it."""
        src = _noisy_img(seed=90)
        res = Image.new("RGB", (100, 50))  # wrong size
        r = _evaluate(
            source_img=src,
            result_img=res,
            target_w=200,
            target_h=100,
        )
        from verdict.models import FAIL
        assert r.status == FAIL

    def test_log_emitted_with_metrics(self, capsys):
        src = _noisy_img(seed=95)
        _evaluate(
            source_img=src,
            result_img=src,
            target_w=200,
            target_h=100,
            job_id="p1d-log",
            spec_id="200x100",
        )
        out = capsys.readouterr().out
        assert "[VERDICT_VISUAL]" in out
        assert "jobId=p1d-log" in out

    def test_evidence_contains_all_15_metric_keys(self):
        src = _noisy_img(seed=100)
        r = _evaluate(
            source_img=src,
            result_img=src,
            target_w=200,
            target_h=100,
        )
        expected_keys = {
            "immutableChangedPixelRatio", "outsideAllowedChangedPixelRatio",
            "sceneSimilarityScore", "backgroundSemanticDriftScore",
            "fullSceneRegenerationScore", "productVisibilityRatio",
            "titleVisibilityRatio", "ctaVisibilityRatio",
            "titleContrastRatio", "ctaContrastRatio",
            "faceOcclusionRatio", "handOcclusionRatio",
            "groupCompletenessRatio", "duplicateObjectCount",
            "blankOutputScore",
        }
        assert expected_keys <= set(r.evidence.keys()), (
            f"Missing from evidence: {expected_keys - set(r.evidence.keys())}"
        )


# ── T9: Reason code constants ─────────────────────────────────────────────────

class TestReasonCodeConstants:
    def test_reason_codes_have_correct_values(self):
        from verdict.visual_evaluator import (
            REASON_IMMUTABLE_CHANGED,
            REASON_OUTSIDE_ALLOWED,
            REASON_SCENE_IDENTITY,
            REASON_PRODUCT_VISIBILITY,
            REASON_TITLE_READABILITY,
            REASON_CTA_READABILITY,
            REASON_FACE_OCCLUSION,
            REASON_HAND_OCCLUSION,
            REASON_GROUP_INCOMPLETE,
            REASON_DUPLICATE_OBJECT,
            REASON_BLANK_OUTPUT,
        )
        assert REASON_IMMUTABLE_CHANGED == "IMMUTABLE_PIXELS_CHANGED"
        assert REASON_OUTSIDE_ALLOWED == "OUTSIDE_ALLOWED_REGION_CHANGED"
        assert REASON_SCENE_IDENTITY == "SCENE_IDENTITY_CHANGED"
        assert REASON_PRODUCT_VISIBILITY == "PRODUCT_VISIBILITY_FAILED"
        assert REASON_TITLE_READABILITY == "TITLE_READABILITY_FAILED"
        assert REASON_CTA_READABILITY == "CTA_READABILITY_FAILED"
        assert REASON_FACE_OCCLUSION == "FACE_OCCLUSION_EXCEEDED"
        assert REASON_HAND_OCCLUSION == "HAND_OCCLUSION_EXCEEDED"
        assert REASON_GROUP_INCOMPLETE == "SEMANTIC_GROUP_INCOMPLETE"
        assert REASON_DUPLICATE_OBJECT == "DUPLICATE_OBJECT_COMPOSITION"
        assert REASON_BLANK_OUTPUT == "BLANK_OUTPUT_DETECTED"
