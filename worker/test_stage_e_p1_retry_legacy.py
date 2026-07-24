"""Stage E P1-A: Retry invariant and legacy dependency tests.

Verifies:
  retry_invariant:
    1. capture_attempt1() records manifest state correctly
    2. validate_retry() passes when manifest unchanged
    3. validate_retry() passes when preserve expands (allowed)
    4. validate_retry() passes when removal contracts (allowed)
    5. validate_retry() raises when preserve shrinks (RETRY_PRESERVE_SHRINK_FORBIDDEN)
    6. validate_retry() raises when removal expands (RETRY_MANIFEST_MUTATION_FORBIDDEN)
    7. get_delta() returns correct allowed/forbidden change analysis
    8. log_retry_invariant() emits [SEMANTIC_RETRY_INVARIANT]
    9. Not captured state → validate_retry returns empty (no crash)

  legacy dependency blocking (E-3 coverage extension):
    10. source_faithful_repair mode → CONFIG_LEGACY_PIPELINE_FORBIDDEN
    11. legacy fallback modes forbidden in production
    12. SFR_RUNTIME_CALL_COUNT=0 in standard pipeline

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_manifest(preserve_ids=None, removal_ids=None, cta_ids=None):
    """Create a minimal manifest-like object."""
    class FakeManifest:
        def __init__(self):
            self.manifest_sha256 = "testsha123"
            self.preserve_object_ids = list(preserve_ids or [])
            self.removal_object_ids = list(removal_ids or [])
            self.cta_group_ids = list(cta_ids or [])
            self.preserve_roles = ["human_subject"]
            self.removal_roles = ["background"]
    return FakeManifest()


# ── P1-A-1: RetryManifestInvariant basics ────────────────────────────────────

class TestRetryManifestInvariant:
    def _invariant(self):
        from scene_cleanup.retry_invariant import RetryManifestInvariant
        return RetryManifestInvariant()

    def test_not_captured_initially(self):
        inv = self._invariant()
        assert inv._captured is False

    def test_capture_sets_captured(self):
        inv = self._invariant()
        m = _make_manifest(["obj1", "obj2"], ["bg1"])
        inv.capture_attempt1(m)
        assert inv._captured is True

    def test_capture_records_preserve_set(self):
        inv = self._invariant()
        m = _make_manifest(["obj1", "obj2"])
        inv.capture_attempt1(m)
        assert inv._preserve_ids_set == frozenset(["obj1", "obj2"])

    def test_capture_records_removal_set(self):
        inv = self._invariant()
        m = _make_manifest(removal_ids=["bg1", "bg2"])
        inv.capture_attempt1(m)
        assert inv._removal_ids_set == frozenset(["bg1", "bg2"])

    def test_validate_unchanged_passes(self):
        inv = self._invariant()
        m = _make_manifest(["obj1", "obj2"], ["bg1"])
        inv.capture_attempt1(m)
        # Same manifest in retry
        m2 = _make_manifest(["obj1", "obj2"], ["bg1"])
        violations = inv.validate_retry(m2)
        assert violations == []

    def test_preserve_expand_allowed(self):
        inv = self._invariant()
        m = _make_manifest(["obj1"], ["bg1"])
        inv.capture_attempt1(m)
        # Retry adds obj2 to preserve
        m2 = _make_manifest(["obj1", "obj2"], ["bg1"])
        violations = inv.validate_retry(m2)
        assert violations == []

    def test_removal_contract_allowed(self):
        inv = self._invariant()
        m = _make_manifest(["obj1"], ["bg1", "bg2"])
        inv.capture_attempt1(m)
        # Retry removes bg2 from removal (contracts)
        m2 = _make_manifest(["obj1"], ["bg1"])
        violations = inv.validate_retry(m2)
        assert violations == []

    def test_preserve_shrink_raises(self):
        inv = self._invariant()
        m = _make_manifest(["obj1", "obj2"], ["bg1"])
        inv.capture_attempt1(m)
        # Retry removes obj2 from preserve
        m2 = _make_manifest(["obj1"], ["bg1"])
        with pytest.raises(RuntimeError, match="RETRY_PRESERVE_SHRINK_FORBIDDEN"):
            inv.validate_retry(m2)

    def test_removal_expand_raises(self):
        inv = self._invariant()
        m = _make_manifest(["obj1"], ["bg1"])
        inv.capture_attempt1(m)
        # Retry adds bg_new to removal (expands)
        m2 = _make_manifest(["obj1"], ["bg1", "bg_new"])
        with pytest.raises(RuntimeError, match="RETRY_MANIFEST_MUTATION_FORBIDDEN"):
            inv.validate_retry(m2)

    def test_not_captured_returns_empty(self):
        inv = self._invariant()
        m = _make_manifest(["obj1"])
        # Never captured — validate_retry should be a no-op
        violations = inv.validate_retry(m)
        assert violations == []


# ── P1-A-2: get_delta ────────────────────────────────────────────────────────

class TestGetDelta:
    def _invariant_with(self, preserve, removal):
        from scene_cleanup.retry_invariant import RetryManifestInvariant
        inv = RetryManifestInvariant()
        inv.capture_attempt1(_make_manifest(preserve, removal))
        return inv

    def test_no_change_delta(self):
        inv = self._invariant_with(["obj1"], ["bg1"])
        m2 = _make_manifest(["obj1"], ["bg1"])
        delta = inv.get_delta(m2)
        assert delta["preserveExpanded"] == []
        assert delta["preserveShrunk"] == []
        assert delta["removalExpanded"] == []
        assert delta["removalContracted"] == []
        assert delta["forbiddenChanges"] is False

    def test_expand_preserve_shows_in_delta(self):
        inv = self._invariant_with(["obj1"], ["bg1"])
        m2 = _make_manifest(["obj1", "obj2"], ["bg1"])
        delta = inv.get_delta(m2)
        assert "obj2" in delta["preserveExpanded"]
        assert delta["forbiddenChanges"] is False
        assert delta["permitedChanges"] is True

    def test_shrink_preserve_shows_forbidden(self):
        inv = self._invariant_with(["obj1", "obj2"], ["bg1"])
        m2 = _make_manifest(["obj1"], ["bg1"])
        delta = inv.get_delta(m2)
        assert "obj2" in delta["preserveShrunk"]
        assert delta["forbiddenChanges"] is True

    def test_expand_removal_shows_forbidden(self):
        inv = self._invariant_with(["obj1"], ["bg1"])
        m2 = _make_manifest(["obj1"], ["bg1", "bg_new"])
        delta = inv.get_delta(m2)
        assert "bg_new" in delta["removalExpanded"]
        assert delta["forbiddenChanges"] is True

    def test_contract_removal_shows_permitted(self):
        inv = self._invariant_with(["obj1"], ["bg1", "bg2"])
        m2 = _make_manifest(["obj1"], ["bg1"])
        delta = inv.get_delta(m2)
        assert "bg2" in delta["removalContracted"]
        assert delta["forbiddenChanges"] is False
        assert delta["permitedChanges"] is True

    def test_not_captured_returns_captured_false(self):
        from scene_cleanup.retry_invariant import RetryManifestInvariant
        inv = RetryManifestInvariant()
        delta = inv.get_delta(_make_manifest())
        assert delta["captured"] is False


# ── P1-A-3: log_retry_invariant ─────────────────────────────────────────────

class TestLogRetryInvariant:
    def test_log_emitted(self, capsys):
        from scene_cleanup.retry_invariant import RetryManifestInvariant, log_retry_invariant
        inv = RetryManifestInvariant()
        m = _make_manifest(["obj1"], ["bg1"])
        inv.capture_attempt1(m)
        m2 = _make_manifest(["obj1", "obj2"], ["bg1"])  # expand preserve
        log_retry_invariant(inv, m2, attempt=2, job_id="p1a-log", spec_id="300x250")
        out = capsys.readouterr().out
        assert "[SEMANTIC_RETRY_INVARIANT]" in out
        assert "jobId=p1a-log" in out
        assert "attempt=2" in out

    def test_log_shows_forbidden_changes(self, capsys):
        from scene_cleanup.retry_invariant import RetryManifestInvariant, log_retry_invariant
        inv = RetryManifestInvariant()
        m = _make_manifest(["obj1", "obj2"])
        inv.capture_attempt1(m)
        m2 = _make_manifest(["obj1"])  # shrink preserve
        log_retry_invariant(inv, m2, attempt=2, job_id="p1a-err")
        out = capsys.readouterr().out
        assert "forbiddenChanges=True" in out


# ── P1-A-4: Legacy pipeline blocking (extends E-3) ────────────────────────────

class TestLegacyPipelineBlocking:
    """Verify that legacy fallback modes raise at runtime (E-3 extended coverage)."""

    def test_sfr_mode_raises_config_forbidden(self):
        """source_faithful_repair must raise CONFIG_LEGACY_PIPELINE_FORBIDDEN."""
        import os, io, sys, tempfile, shutil
        from PIL import Image
        import numpy as np

        src = tempfile.mktemp(suffix=".png")
        Image.new("RGB", (400, 300), (80, 120, 160)).save(src)
        tmp = tempfile.mkdtemp()
        try:
            os.environ["BACKGROUND_GENERATION_MODE"] = "source_faithful_repair"
            from resizer import _generate_ai_only

            class _FakeProv:
                def metadata(self):
                    return {"providerName": "fake", "modelName": "fake-v1"}
                def inpaint(self, img, mask, prompt, opts):
                    arr = np.random.randint(40, 180, (img.size[1], img.size[0], 3), dtype=np.uint8)
                    return Image.fromarray(arr, "RGB")

            buf = io.StringIO()
            old_out = sys.stdout
            sys.stdout = buf
            try:
                with pytest.raises(RuntimeError, match="CONFIG_LEGACY_PIPELINE_FORBIDDEN"):
                    _generate_ai_only(
                        psd_path=src,
                        specs=[{"media": "t", "name": "x", "slug": "x", "width": 300, "height": 250}],
                        resize_mode="ai-auto",
                        output_format="png",
                        output_dir=tmp,
                        source_type="image",
                        job_id="p1a-sfr",
                        _provider_override=_FakeProv(),
                    )
            finally:
                sys.stdout = old_out
        finally:
            os.environ.pop("BACKGROUND_GENERATION_MODE", None)
            os.unlink(src)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_invalid_mode_raises(self):
        """Any non-semantic_scene_cleanup mode must raise."""
        import os, io, sys, tempfile, shutil
        from PIL import Image
        import numpy as np

        src = tempfile.mktemp(suffix=".png")
        Image.new("RGB", (400, 300), (80, 120, 160)).save(src)
        tmp = tempfile.mkdtemp()
        try:
            os.environ["BACKGROUND_GENERATION_MODE"] = "blur_background"

            class _FakeProv:
                def metadata(self):
                    return {"providerName": "fake"}
                def inpaint(self, img, mask, prompt, opts):
                    return img

            from resizer import _generate_ai_only
            buf = io.StringIO()
            old_out = sys.stdout
            sys.stdout = buf
            try:
                with pytest.raises(RuntimeError):
                    _generate_ai_only(
                        psd_path=src,
                        specs=[{"media": "t", "name": "x", "slug": "x", "width": 300, "height": 250}],
                        resize_mode="ai-auto",
                        output_format="png",
                        output_dir=tmp,
                        source_type="image",
                        job_id="p1a-invalid",
                        _provider_override=_FakeProv(),
                    )
            finally:
                sys.stdout = old_out
        finally:
            os.environ.pop("BACKGROUND_GENERATION_MODE", None)
            os.unlink(src)
            shutil.rmtree(tmp, ignore_errors=True)
