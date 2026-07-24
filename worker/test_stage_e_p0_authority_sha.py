"""Stage E P0-C: Single manifest authority and SHA chain tests.

Verifies:
  sha_chain:
    1. CanonicalSHAChain records SHAs correctly
    2. validate_chain() empty when all match
    3. validate_chain() returns mismatch when analysis SHA differs
    4. validate_chain() returns mismatch when manifest SHA differs
    5. validate_chain() skips unset SHAs (not fatal)
    6. all_match() returns True/False correctly
    7. validate_or_raise() raises RuntimeError on mismatch
    8. log_sha_chain() emits [CANONICAL_SHA_CHAIN] with allMatched field

  manifest finalization:
    9. SemanticManifest starts with finalized=False
    10. finalize() sets finalized=True
    11. try_mutate_field() works on non-finalized manifest
    12. try_mutate_field() raises MANIFEST_MUTATION_AFTER_FINALIZE when finalized
    13. try_mutate_field() increments mutation counter
    14. try_mutate_field() allows non-immutable field mutation even when finalized
    15. manifest_mutation_count_after_finalization=0 for clean manifest
    16. E-2 tests still pass (backward compat: new fields have defaults)

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import pytest


# ── P0-C-1: CanonicalSHAChain ────────────────────────────────────────────────

class TestCanonicalSHAChain:
    def _chain(self, canonical="abc123"):
        from scene_cleanup.sha_chain import CanonicalSHAChain
        return CanonicalSHAChain(canonical_sha=canonical)

    def test_initial_state(self):
        c = self._chain("abc123")
        assert c.canonical_sha == "abc123"
        assert c.analysis_sha == ""
        assert c.manifest_sha == ""
        assert c.extraction_sha == ""
        assert c.generation_sha == ""

    def test_record_analysis(self):
        c = self._chain("abc123")
        c.record_analysis("abc123")
        assert c.analysis_sha == "abc123"

    def test_record_manifest(self):
        c = self._chain("abc123")
        c.record_manifest("abc123")
        assert c.manifest_sha == "abc123"

    def test_record_extraction(self):
        c = self._chain("abc123")
        c.record_extraction("abc123")
        assert c.extraction_sha == "abc123"

    def test_record_generation(self):
        c = self._chain("abc123")
        c.record_generation("abc123")
        assert c.generation_sha == "abc123"

    def test_validate_chain_empty_when_all_match(self):
        c = self._chain("abc123")
        c.record_analysis("abc123")
        c.record_manifest("abc123")
        c.record_extraction("abc123")
        c.record_generation("abc123")
        assert c.validate_chain() == []

    def test_validate_chain_mismatch_analysis(self):
        c = self._chain("abc123")
        c.record_analysis("DIFFERENT")
        violations = c.validate_chain()
        assert any("stage=analysis" in v for v in violations)

    def test_validate_chain_mismatch_manifest(self):
        c = self._chain("abc123")
        c.record_manifest("DIFFERENT")
        violations = c.validate_chain()
        assert any("stage=manifest" in v for v in violations)

    def test_validate_chain_mismatch_extraction(self):
        c = self._chain("abc123")
        c.record_extraction("DIFFERENT_SHA")
        violations = c.validate_chain()
        assert any("stage=extraction" in v for v in violations)

    def test_validate_chain_mismatch_generation(self):
        c = self._chain("abc123")
        c.record_generation("OTHER_SHA")
        violations = c.validate_chain()
        assert any("stage=generation" in v for v in violations)

    def test_unset_sha_not_checked(self):
        """Empty string SHA means 'not recorded' — not a mismatch."""
        c = self._chain("abc123")
        # Only record analysis (others empty)
        c.record_analysis("abc123")
        violations = c.validate_chain()
        assert violations == []

    def test_all_match_true_when_valid(self):
        c = self._chain("abc123")
        c.record_analysis("abc123")
        assert c.all_match() is True

    def test_all_match_false_when_mismatch(self):
        c = self._chain("abc123")
        c.record_analysis("DIFFERENT")
        assert c.all_match() is False

    def test_validate_or_raise_passes_when_valid(self):
        c = self._chain("abc123")
        c.record_analysis("abc123")
        c.validate_or_raise(job_id="p0c-ok")

    def test_validate_or_raise_raises_when_mismatch(self):
        c = self._chain("abc123")
        c.record_analysis("DIFFERENT")
        with pytest.raises(RuntimeError, match="CANONICAL_SOURCE_HASH_MISMATCH"):
            c.validate_or_raise(job_id="p0c-fail")

    def test_canonical_sha_not_set(self):
        from scene_cleanup.sha_chain import CanonicalSHAChain
        c = CanonicalSHAChain()  # no canonical sha
        violations = c.validate_chain()
        assert any("CANONICAL_SHA_NOT_SET" in v for v in violations)


# ── P0-C-2: log_sha_chain ────────────────────────────────────────────────────

class TestLogSHAChain:
    def test_log_emitted(self, capsys):
        from scene_cleanup.sha_chain import CanonicalSHAChain, log_sha_chain
        c = CanonicalSHAChain(canonical_sha="abc123")
        c.record_analysis("abc123")
        log_sha_chain(c, job_id="p0c-log", spec_id="300x250")
        out = capsys.readouterr().out
        assert "[CANONICAL_SHA_CHAIN]" in out
        assert "jobId=p0c-log" in out

    def test_log_all_matched_true(self, capsys):
        from scene_cleanup.sha_chain import CanonicalSHAChain, log_sha_chain
        c = CanonicalSHAChain(canonical_sha="abc123")
        c.record_analysis("abc123")
        log_sha_chain(c, job_id="p0c-match")
        out = capsys.readouterr().out
        assert "allMatched=true" in out

    def test_log_all_matched_false_on_mismatch(self, capsys):
        from scene_cleanup.sha_chain import CanonicalSHAChain, log_sha_chain
        c = CanonicalSHAChain(canonical_sha="abc123")
        c.record_analysis("DIFFERENT")
        log_sha_chain(c, job_id="p0c-mismatch")
        out = capsys.readouterr().out
        assert "allMatched=false" in out

    def test_build_sha_chain_from_canonical(self):
        from scene_cleanup.sha_chain import build_sha_chain_from_canonical
        c = build_sha_chain_from_canonical("abc123")
        assert c.canonical_sha == "abc123"
        assert c.all_match() is True  # no stages recorded yet


# ── P0-C-3: SemanticManifest finalization ────────────────────────────────────

class TestManifestFinalization:
    def _make_manifest(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        return build_semantic_manifest(
            job_id="p0c-test",
            spec_id="300x250",
            d2_fg_layers=[],
        )

    def test_starts_not_finalized(self):
        m = self._make_manifest()
        assert m.finalized is False

    def test_finalize_sets_flag(self):
        from verdict.unified_semantic_manifest import finalize
        m = self._make_manifest()
        finalize(m)
        assert m.finalized is True

    def test_mutation_count_zero_initially(self):
        m = self._make_manifest()
        assert m.manifest_mutation_count_after_finalization == 0

    def test_try_mutate_field_before_finalize(self):
        from verdict.unified_semantic_manifest import try_mutate_field
        m = self._make_manifest()
        # Should succeed (not finalized)
        try_mutate_field(m, "preserve_roles", ["human_subject"])
        assert m.preserve_roles == ["human_subject"]

    def test_try_mutate_field_after_finalize_raises(self):
        from verdict.unified_semantic_manifest import finalize, try_mutate_field
        m = self._make_manifest()
        finalize(m)
        with pytest.raises(RuntimeError, match="MANIFEST_MUTATION_AFTER_FINALIZE"):
            try_mutate_field(m, "preserve_roles", ["human_subject"])

    def test_mutation_count_increments_on_violation(self):
        from verdict.unified_semantic_manifest import finalize, try_mutate_field
        m = self._make_manifest()
        finalize(m)
        for _ in range(3):
            try:
                try_mutate_field(m, "preserve_roles", [])
            except RuntimeError:
                pass
        assert m.manifest_mutation_count_after_finalization == 3

    def test_non_immutable_field_can_mutate_after_finalize(self):
        from verdict.unified_semantic_manifest import finalize, try_mutate_field
        m = self._make_manifest()
        finalize(m)
        # job_id is not in _IMMUTABLE_FIELDS → can mutate freely
        try_mutate_field(m, "job_id", "new-job")
        assert m.job_id == "new-job"
        assert m.manifest_mutation_count_after_finalization == 0

    def test_manifest_owner_default(self):
        m = self._make_manifest()
        assert m.manifest_owner == "worker"

    def test_all_immutable_fields_blocked(self):
        from verdict.unified_semantic_manifest import (
            finalize, try_mutate_field, _IMMUTABLE_FIELDS
        )
        m = self._make_manifest()
        finalize(m)
        blocked_count = 0
        for field_name in _IMMUTABLE_FIELDS:
            try:
                try_mutate_field(m, field_name, [])
            except RuntimeError as exc:
                if "MANIFEST_MUTATION_AFTER_FINALIZE" in str(exc):
                    blocked_count += 1
        assert blocked_count == len(_IMMUTABLE_FIELDS)


# ── P0-C-4: Backward compat — E-2 build still works ─────────────────────────

class TestBackwardCompat:
    def test_build_with_layers_still_works(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [
            {"objectId": "obj1", "semanticRole": "human_subject"},
            {"objectId": "obj2", "semanticRole": "background"},
        ]
        m = build_semantic_manifest(
            job_id="bc-test", spec_id="s1", d2_fg_layers=layers
        )
        assert "human_subject" in m.preserve_roles
        assert m.finalized is False
        assert m.manifest_mutation_count_after_finalization == 0

    def test_manifest_sha_present(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        m = build_semantic_manifest(job_id="sha-test", spec_id="s1")
        assert len(m.manifest_sha256) > 0
