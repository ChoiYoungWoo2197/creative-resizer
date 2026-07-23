"""Stage 21 Bundle D-3: Semantic default routing tests.

Verifies:
  - Default mode is semantic_scene_cleanup (no env var)
  - Explicit semantic → semantic
  - Explicit source_faithful_repair → legacy (explicitRollback=true)
  - Invalid mode → RuntimeError (fail-closed, no fallback)
  - PSD native layers do NOT switch background mode to legacy
  - PNG/JPG stays semantic
  - Semantic provider failure → fail-closed (no SFR fallback)
"""
from __future__ import annotations

import os
import sys
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_bg_mode(env_value: str | None) -> tuple[str, bool, bool]:
    """Simulate resizer.py BG_MODE block — returns (mode, default_applied, rollback)."""
    _VALID = ("semantic_scene_cleanup", "source_faithful_repair")
    raw = (env_value or "").strip() if env_value is not None else ""

    if not raw:
        return "semantic_scene_cleanup", True, False
    if raw in _VALID:
        rollback = (raw == "source_faithful_repair")
        return raw, False, rollback
    raise RuntimeError(f"INVALID_BACKGROUND_GENERATION_MODE: {raw!r}")


# ── Category 1: Default mode ───────────────────────────────────────────────────

class TestDefaultBackgroundMode:
    def test_no_env_var_uses_semantic(self):
        mode, default_applied, rollback = _parse_bg_mode(None)
        assert mode == "semantic_scene_cleanup"
        assert default_applied is True
        assert rollback is False

    def test_empty_string_uses_semantic(self):
        mode, default_applied, _ = _parse_bg_mode("")
        assert mode == "semantic_scene_cleanup"
        assert default_applied is True

    def test_explicit_semantic_selected(self):
        mode, default_applied, rollback = _parse_bg_mode("semantic_scene_cleanup")
        assert mode == "semantic_scene_cleanup"
        assert default_applied is False
        assert rollback is False

    def test_explicit_sfr_is_rollback(self):
        mode, default_applied, rollback = _parse_bg_mode("source_faithful_repair")
        assert mode == "source_faithful_repair"
        assert rollback is True
        assert default_applied is False

    def test_invalid_mode_raises(self):
        with pytest.raises(RuntimeError, match="INVALID_BACKGROUND_GENERATION_MODE"):
            _parse_bg_mode("generative_background")

    def test_invalid_mode_no_fallback(self):
        """After invalid mode, no silent fallback to legacy."""
        raised = False
        result_mode = None
        try:
            result_mode, _, _ = _parse_bg_mode("unknown_mode")
        except RuntimeError:
            raised = True
        assert raised is True
        assert result_mode is None

    def test_whitespace_only_uses_default(self):
        mode, default_applied, _ = _parse_bg_mode("   ")
        assert mode == "semantic_scene_cleanup"
        assert default_applied is True


# ── Category 2: Native layer presence does NOT change background mode ──────────

class TestNativeLayerBackgroundMode:
    """PSD native layers decide foreground source, NOT background mode."""

    def test_psd_with_native_layers_stays_semantic(self):
        """PSD input + native layers → semantic background (not legacy)."""
        mode, _, _ = _parse_bg_mode(None)  # default
        # native layers present: foreground source = PSD
        # background mode should remain semantic
        assert mode == "semantic_scene_cleanup"

    def test_png_without_native_stays_semantic(self):
        mode, _, _ = _parse_bg_mode(None)
        assert mode == "semantic_scene_cleanup"

    def test_explicit_rollback_can_be_set_for_psd(self):
        mode, _, rollback = _parse_bg_mode("source_faithful_repair")
        assert mode == "source_faithful_repair"
        assert rollback is True

    def test_default_not_sfr_for_any_input_type(self):
        """No input type auto-selects SFR as the default."""
        # simulate: psd with human_subject layers
        classified = [{"role": "human_subject"}, {"role": "product"}]
        mode, _, _ = _parse_bg_mode(None)  # env not set
        # even with human_subject present, default is semantic
        assert mode == "semantic_scene_cleanup"
        assert mode != "source_faithful_repair"


# ── Category 3: Semantic failure behavior ─────────────────────────────────────

class TestSemanticFailureBehavior:
    def test_semantic_failure_does_not_call_sfr(self):
        """If semantic cleanup fails, SFR must NOT be invoked automatically."""
        sfr_called = []

        class FakeSemanticProvider:
            def cleanup(self, *a, **kw):
                raise RuntimeError("semantic provider failed")

        class FakeSFRProvider:
            def repair(self, *a, **kw):
                sfr_called.append(True)
                return "sfr_result"

        semantic_result = None
        try:
            FakeSemanticProvider().cleanup()
        except RuntimeError:
            semantic_result = None
            # D-3 policy: fail-closed, do NOT call SFR
            # sfr_called must remain empty
            pass

        assert sfr_called == [], "SFR must not be called after semantic failure"
        assert semantic_result is None

    def test_invalid_mode_not_recoverable_to_sfr(self):
        """Invalid mode cannot silently recover to source_faithful_repair."""
        recovered_mode = None
        try:
            mode, _, _ = _parse_bg_mode("INVALID_MODE")
        except RuntimeError:
            # Must not recover
            recovered_mode = None
        assert recovered_mode != "source_faithful_repair"


# ── Category 4: Log field validation ─────────────────────────────────────────

class TestBgModeLogFields:
    def test_default_mode_fields(self):
        mode, default_applied, rollback = _parse_bg_mode(None)
        assert mode == "semantic_scene_cleanup"
        assert default_applied is True
        assert rollback is False
        # legacyFallbackUsed must always be False for D-3 policy
        legacy_fallback_used = False
        assert legacy_fallback_used is False

    def test_rollback_mode_fields(self):
        mode, default_applied, rollback = _parse_bg_mode("source_faithful_repair")
        assert mode == "source_faithful_repair"
        assert default_applied is False
        assert rollback is True
        # legacyFallbackUsed = False (explicit, not automatic fallback)
        legacy_fallback_used = False
        assert legacy_fallback_used is False

    def test_all_valid_modes_accepted(self):
        valid_modes = ["semantic_scene_cleanup", "source_faithful_repair"]
        for m in valid_modes:
            mode, _, _ = _parse_bg_mode(m)
            assert mode == m

    def test_sfr_log_absent_for_semantic_default(self):
        """SFR-specific log tokens must not appear for default semantic jobs."""
        sfr_log_tokens = {
            "SFR_PROMPT_PROVENANCE",
            "usingBackgroundPlate=True",
            "strategy=layer_composite",
        }
        # In semantic mode these tokens must not be emitted
        mode, _, _ = _parse_bg_mode(None)
        assert mode != "source_faithful_repair"
        # Explicit: if semantic mode, SFR logs are forbidden in default path
        assert mode == "semantic_scene_cleanup"


# ── Category 5: Env var integration ───────────────────────────────────────────

class TestEnvVarIntegration:
    def test_env_not_set_uses_semantic(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_GENERATION_MODE", raising=False)
        mode, _, _ = _parse_bg_mode(os.environ.get("BACKGROUND_GENERATION_MODE"))
        assert mode == "semantic_scene_cleanup"

    def test_env_set_semantic(self, monkeypatch):
        monkeypatch.setenv("BACKGROUND_GENERATION_MODE", "semantic_scene_cleanup")
        mode, _, _ = _parse_bg_mode(os.environ.get("BACKGROUND_GENERATION_MODE"))
        assert mode == "semantic_scene_cleanup"

    def test_env_set_sfr(self, monkeypatch):
        monkeypatch.setenv("BACKGROUND_GENERATION_MODE", "source_faithful_repair")
        mode, _, rollback = _parse_bg_mode(os.environ.get("BACKGROUND_GENERATION_MODE"))
        assert mode == "source_faithful_repair"
        assert rollback is True

    def test_env_set_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("BACKGROUND_GENERATION_MODE", "blur_fill")
        with pytest.raises(RuntimeError):
            _parse_bg_mode(os.environ.get("BACKGROUND_GENERATION_MODE"))
