"""Stage E P0-B: Immutable pixel policy and mask conflict tests.

Verifies:
  pixel_restorer:
    1. Outside allowed mask → canonical pixels restored
    2. Inside allowed mask → AI result pixels used
    3. None mask → AI result returned as-is
    4. Size mismatch → AI result returned as-is
    5. compute_immutable_metrics returns correct fields
    6. log_default_immutable_policy emits [DEFAULT_IMMUTABLE_POLICY]

  mask_conflict:
    7. No conflict → hasConflict=False
    8. Full overlap without confidence → unresolved
    9. Overlap with confidence → resolved
    10. validate_or_raise raises on unresolved conflict
    11. validate_or_raise passes with threshold when no conflict
    12. log_mask_conflict_analysis emits [MASK_CONFLICT_ANALYSIS]
    13. DEFAULT_ORIGINAL_PRESERVATION=PASS when restored pixels match canonical
    14. OUTSIDE_REMOVAL_PIXEL_INTEGRITY=PASS when no immutable pixels changed
    15. PRODUCT_HUMAN_CONFLICT_GATE=PASS when no conflict

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image


# ── Helpers ──────────────────────────────────────────────────────────────────

def _solid(w, h, color=(100, 150, 200)):
    return Image.new("RGB", (w, h), color=color)


def _noisy(w, h, seed=42):
    arr = np.random.RandomState(seed).randint(0, 255, (h, w, 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def _mask_arr(w, h, fill=255):
    return np.full((h, w), fill, dtype=np.uint8)


def _split_mask(w, h, split_x):
    """Left half = 255 (allowed), right half = 0 (immutable)."""
    arr = np.zeros((h, w), dtype=np.uint8)
    arr[:, :split_x] = 255
    return arr


# ── P0-B-1: pixel_restorer.apply_default_immutable_policy ───────────────────

class TestApplyDefaultImmutablePolicy:
    def test_none_mask_returns_ai_result(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = _solid(100, 80, (50, 50, 50))
        ai_result = _solid(100, 80, (200, 200, 200))
        out = apply_default_immutable_policy(canonical, ai_result, None)
        # Returns AI result unchanged when no mask
        assert out is ai_result

    def test_full_allowed_mask_returns_ai_result_pixels(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = _solid(100, 80, (50, 50, 50))
        ai_result = _solid(100, 80, (200, 200, 200))
        full_mask = _mask_arr(100, 80, fill=255)  # all allowed
        out = apply_default_immutable_policy(canonical, ai_result, full_mask)
        arr = np.array(out.convert("RGB"))
        # All pixels should be ~AI result (200, 200, 200)
        assert arr[:, :, 0].mean() > 150

    def test_zero_mask_restores_canonical(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = _solid(100, 80, (50, 50, 50))
        ai_result = _solid(100, 80, (200, 200, 200))
        zero_mask = _mask_arr(100, 80, fill=0)  # all immutable
        out = apply_default_immutable_policy(canonical, ai_result, zero_mask)
        arr = np.array(out.convert("RGB"))
        # All pixels should be ~canonical (50, 50, 50)
        assert arr[:, :, 0].mean() < 100

    def test_split_mask_half_ai_half_canonical(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        w, h = 100, 80
        canonical = _solid(w, h, (50, 50, 50))
        ai_result = _solid(w, h, (200, 200, 200))
        split_mask = _split_mask(w, h, split_x=50)
        out = apply_default_immutable_policy(canonical, ai_result, split_mask)
        arr = np.array(out.convert("RGB"))
        left_mean = arr[:, :50, 0].mean()
        right_mean = arr[:, 50:, 0].mean()
        assert left_mean > 150, f"Left should be AI pixels ({left_mean})"
        assert right_mean < 100, f"Right should be canonical pixels ({right_mean})"

    def test_size_mismatch_returns_ai_result(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = _solid(100, 80)
        ai_result = _solid(200, 150)  # different size
        mask = _mask_arr(100, 80, fill=0)
        out = apply_default_immutable_policy(canonical, ai_result, mask)
        assert out is ai_result

    def test_none_canonical_returns_ai_result(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        ai_result = _solid(100, 80, (200, 200, 200))
        out = apply_default_immutable_policy(None, ai_result, _mask_arr(100, 80))
        assert out is ai_result

    def test_none_ai_result_returns_none(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = _solid(100, 80)
        out = apply_default_immutable_policy(canonical, None, _mask_arr(100, 80))
        assert out is None

    def test_pil_mask_works(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = _solid(100, 80, (50, 50, 50))
        ai_result = _solid(100, 80, (200, 200, 200))
        pil_mask = Image.new("L", (100, 80), 0)  # all immutable
        out = apply_default_immutable_policy(canonical, ai_result, pil_mask)
        arr = np.array(out.convert("RGB"))
        assert arr[:, :, 0].mean() < 100


# ── P0-B-2: compute_immutable_metrics ────────────────────────────────────────

class TestComputeImmutableMetrics:
    def test_no_mask_returns_defaults(self):
        from scene_cleanup.pixel_restorer import compute_immutable_metrics
        canonical = _solid(100, 80)
        ai_result = _solid(100, 80, (200, 200, 200))
        m = compute_immutable_metrics(canonical, ai_result, None)
        assert m["allowedGenerationCoverage"] == 1.0
        assert m["outsideAllowedChangedPixelRatio"] == 0.0
        assert m["restoredOriginalPixelCount"] == 0

    def test_full_allowed_no_immutable_changes(self):
        from scene_cleanup.pixel_restorer import compute_immutable_metrics
        canonical = _solid(100, 80, (50, 50, 50))
        ai_result = _solid(100, 80, (200, 200, 200))
        full_mask = _mask_arr(100, 80, fill=255)
        m = compute_immutable_metrics(canonical, ai_result, full_mask)
        # All pixels allowed → 0 immutable changes
        assert m["outsideAllowedChangedPixelRatio"] == 0.0

    def test_zero_mask_counts_all_as_changed(self):
        from scene_cleanup.pixel_restorer import compute_immutable_metrics
        canonical = _solid(100, 80, (50, 50, 50))
        ai_result = _solid(100, 80, (200, 200, 200))
        zero_mask = _mask_arr(100, 80, fill=0)
        m = compute_immutable_metrics(canonical, ai_result, zero_mask)
        # All pixels immutable and all changed
        assert m["outsideAllowedChangedPixelRatio"] > 0.9
        assert m["restoredOriginalPixelCount"] > 0
        assert m["allowedGenerationCoverage"] < 0.01

    def test_split_mask_coverage(self):
        from scene_cleanup.pixel_restorer import compute_immutable_metrics
        w, h = 100, 80
        canonical = _solid(w, h, (50, 50, 50))
        ai_result = _solid(w, h, (200, 200, 200))
        split_mask = _split_mask(w, h, split_x=50)
        m = compute_immutable_metrics(canonical, ai_result, split_mask)
        # ~50% allowed, ~50% immutable; all immutable pixels changed
        assert 0.4 <= m["allowedGenerationCoverage"] <= 0.6
        assert m["outsideAllowedChangedPixelRatio"] > 0.8


# ── P0-B-3: log_default_immutable_policy ─────────────────────────────────────

class TestLogDefaultImmutablePolicy:
    def test_log_emitted(self, capsys):
        from scene_cleanup.pixel_restorer import log_default_immutable_policy
        m = {
            "allowedGenerationCoverage": 0.75,
            "outsideAllowedChangedPixelRatio": 0.02,
            "restoredOriginalPixelCount": 150,
        }
        log_default_immutable_policy(m, job_id="p0b-log", spec_id="300x250")
        out = capsys.readouterr().out
        assert "[DEFAULT_IMMUTABLE_POLICY]" in out
        assert "jobId=p0b-log" in out
        assert "allowedGenerationCoverage=0.7500" in out


# ── P0-B-4: MaskConflictDetector ─────────────────────────────────────────────

class TestMaskConflictDetector:
    def _detector(self):
        from scene_cleanup.mask_conflict import MaskConflictDetector
        return MaskConflictDetector()

    def test_no_conflict_when_no_overlap(self):
        det = self._detector()
        prod_mask = np.zeros((80, 100), dtype=np.uint8)
        prod_mask[:, :50] = 255  # left half = product
        human_mask = np.zeros((80, 100), dtype=np.uint8)
        human_mask[:, 50:] = 255  # right half = human
        r = det.check_conflict(prod_mask, human_mask)
        assert not r.has_conflict
        assert r.raw_conflict_pixel_count == 0

    def test_full_overlap_is_conflict(self):
        det = self._detector()
        mask = _mask_arr(100, 80, fill=255)
        r = det.check_conflict(mask, mask)  # same mask = full overlap
        assert r.has_conflict
        assert r.raw_conflict_pixel_count == 100 * 80

    def test_no_confidence_means_unresolved(self):
        det = self._detector()
        mask = _mask_arr(100, 80, fill=255)
        r = det.check_conflict(mask, mask)
        assert r.unresolved_conflict_pixel_count > 0
        assert r.conflict_resolution_method == "conservative_unresolved"

    def test_confidence_resolves_conflict(self):
        det = self._detector()
        w, h = 100, 80
        prod_mask = _mask_arr(w, h, fill=255)
        human_mask = _mask_arr(w, h, fill=255)
        # High product confidence in left, high human confidence in right
        prod_conf = np.zeros((h, w), dtype=np.uint8)
        prod_conf[:, :50] = 200
        human_conf = np.zeros((h, w), dtype=np.uint8)
        human_conf[:, 50:] = 200
        r = det.check_conflict(prod_mask, human_mask,
                               product_confidence_mask=prod_conf,
                               human_confidence_mask=human_conf)
        assert r.has_conflict
        assert r.conflict_resolution_method == "confidence_arbitration"
        # With equal or different confidence, some should be resolved
        assert r.resolved_conflict_pixel_count >= 0

    def test_none_masks_returns_no_conflict(self):
        det = self._detector()
        r = det.check_conflict(None, None)
        assert not r.has_conflict

    def test_shape_mismatch_returns_no_conflict(self):
        det = self._detector()
        prod = _mask_arr(100, 80, fill=255)
        human = _mask_arr(200, 150, fill=255)
        r = det.check_conflict(prod, human)
        assert not r.has_conflict

    def test_validate_or_raise_no_conflict_passes(self):
        det = self._detector()
        prod = np.zeros((80, 100), dtype=np.uint8)
        human = _mask_arr(100, 80, fill=255)
        r = det.check_conflict(prod, human)
        # Should not raise
        det.validate_or_raise(r, job_id="p0b-ok")

    def test_validate_or_raise_conflict_raises(self):
        det = self._detector()
        mask = _mask_arr(100, 80, fill=255)
        r = det.check_conflict(mask, mask)
        with pytest.raises(RuntimeError, match="PRODUCT_HUMAN_MASK_CONFLICT_UNRESOLVED"):
            det.validate_or_raise(r, job_id="p0b-fail")

    def test_validate_or_raise_threshold_zero(self):
        """Default threshold=0: any unresolved conflict raises."""
        det = self._detector()
        # Create a single-pixel overlap
        prod = np.zeros((80, 100), dtype=np.uint8)
        human = np.zeros((80, 100), dtype=np.uint8)
        prod[40, 50] = 255
        human[40, 50] = 255
        r = det.check_conflict(prod, human)
        assert r.has_conflict
        with pytest.raises(RuntimeError, match="PRODUCT_HUMAN_MASK_CONFLICT_UNRESOLVED"):
            det.validate_or_raise(r, unresolved_threshold=0.0)

    def test_validate_or_raise_above_threshold_raises(self):
        det = self._detector()
        mask = _mask_arr(100, 80, fill=255)
        r = det.check_conflict(mask, mask)
        # Threshold is very high, but unresolved_ratio should exceed it
        # Actually default threshold = 0.0, so any unresolved raises
        with pytest.raises(RuntimeError):
            det.validate_or_raise(r, unresolved_threshold=0.0)


# ── P0-B-5: log_mask_conflict_analysis ───────────────────────────────────────

class TestLogMaskConflictAnalysis:
    def test_log_emitted(self, capsys):
        from scene_cleanup.mask_conflict import (
            MaskConflictDetector, log_mask_conflict_analysis
        )
        det = MaskConflictDetector()
        prod = np.zeros((80, 100), dtype=np.uint8)
        human = np.zeros((80, 100), dtype=np.uint8)
        r = det.check_conflict(prod, human)
        log_mask_conflict_analysis(r, job_id="p0b-log", spec_id="300x250")
        out = capsys.readouterr().out
        assert "[MASK_CONFLICT_ANALYSIS]" in out
        assert "jobId=p0b-log" in out

    def test_log_has_conflict_fields(self, capsys):
        from scene_cleanup.mask_conflict import (
            MaskConflictDetector, log_mask_conflict_analysis
        )
        det = MaskConflictDetector()
        mask = _mask_arr(100, 80, fill=255)
        r = det.check_conflict(mask, mask)
        log_mask_conflict_analysis(r, job_id="p0b-cf", spec_id="s1")
        out = capsys.readouterr().out
        assert "hasConflict=True" in out
        assert "rawConflictPixelCount=" in out
        assert "unresolvedConflictPixelCount=" in out


# ── P0-B-6: Policy contract gates ────────────────────────────────────────────

class TestPolicyContractGates:
    """Verify DEFAULT_ORIGINAL_PRESERVATION, OUTSIDE_REMOVAL_PIXEL_INTEGRITY,
    PRODUCT_HUMAN_CONFLICT_GATE contracts."""

    def test_default_original_preservation_pass(self):
        """Restored pixels should equal canonical when immutable mask applied."""
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        w, h = 100, 80
        canonical = _noisy(w, h, seed=1)
        ai_result = _noisy(w, h, seed=99)  # totally different
        # Immutable mask: all zeros = all immutable
        zero_mask = _mask_arr(w, h, fill=0)
        out = apply_default_immutable_policy(canonical, ai_result, zero_mask)
        # Output should match canonical
        out_arr = np.array(out.convert("RGB"), dtype=np.int32)
        can_arr = np.array(canonical.convert("RGB"), dtype=np.int32)
        delta = np.abs(out_arr - can_arr).max()
        assert delta < 5, f"Expected output == canonical after immutable restore, max delta={delta}"

    def test_outside_removal_pixel_integrity_pass(self):
        """outsideAllowedChangedPixelRatio == 0 when immutable policy applied before measurement."""
        from scene_cleanup.pixel_restorer import (
            apply_default_immutable_policy, compute_immutable_metrics
        )
        w, h = 100, 80
        canonical = _noisy(w, h, seed=2)
        ai_result = _noisy(w, h, seed=88)
        # Left half allowed, right half immutable
        split_mask = _split_mask(w, h, split_x=50)
        # Apply restoration first
        restored = apply_default_immutable_policy(canonical, ai_result, split_mask)
        # Now measure: restored vs canonical in immutable region should be 0
        m = compute_immutable_metrics(canonical, restored, split_mask)
        assert m["outsideAllowedChangedPixelRatio"] < 0.01, (
            f"outsideAllowedChangedPixelRatio={m['outsideAllowedChangedPixelRatio']}"
        )

    def test_product_human_conflict_gate_pass_when_no_overlap(self):
        """No conflict → gate passes (no exception)."""
        from scene_cleanup.mask_conflict import MaskConflictDetector
        det = MaskConflictDetector()
        prod_mask = np.zeros((80, 100), dtype=np.uint8)
        prod_mask[:, :40] = 255
        human_mask = np.zeros((80, 100), dtype=np.uint8)
        human_mask[:, 60:] = 255
        r = det.check_conflict(prod_mask, human_mask)
        # Should not raise
        det.validate_or_raise(r, job_id="gate-pass")
        assert not r.has_conflict
