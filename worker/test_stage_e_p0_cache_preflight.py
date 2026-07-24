"""Stage E P0-D: Semantic cache validator and preflight gate tests.

Verifies:
  semantic_cache_validator:
    1. SemanticCacheKey.is_complete() true when all fields set
    2. SemanticCacheKey.is_complete() false when any field missing
    3. SemanticCacheKey.missing_fields() returns correct list
    4. is_legacy() detects psd-object-map-v1
    5. is_legacy() detects psd-object-map-v2
    6. is_legacy() detects source-faithful-repair-*
    7. is_legacy() detects legacy-object-map-*
    8. validate_cache_hit() SHA mismatch → rejected
    9. validate_cache_hit() legacy version → rejected
    10. validate_cache_hit() version mismatch → rejected
    11. validate_cache_hit() valid matching key → accepted
    12. log_cache_reject() emits [SEMANTIC_CACHE_REJECT]

  preflight_gate:
    13. Passes with valid canonical + finalized manifest + valid chain
    14. Fails when canonical is None
    15. Fails when manifest is None
    16. Fails when manifest not finalized
    17. Fails when SHA chain has mismatch
    18. Fails when required object IDs missing from manifest
    19. Fails when mask conflict IDs present
    20. Fails when expected group IDs missing
    21. Passes when all groups present
    22. [SEMANTIC_PREFLIGHT] log emitted with status

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _valid_key(sha="abc123"):
    from verdict.semantic_cache_validator import SemanticCacheKey
    return SemanticCacheKey(
        canonicalImageSha256=sha,
        pipelinePolicy="full-image-semantic-v1",
        manifestVersion="unified-semantic-v1",
        analysisVersion="full-image-semantic-v1",
        analysisPromptVersion="v1.0",
        maskPolicyVersion="default-immutable-v1",
        sourceNormalizationVersion="canonical-source-v1",
        semanticRoleSchemaVersion="v1.0",
        model="gpt-4.1-mini",
        modelConfigHash="abc123",
    )


def _legacy_key(sha="abc123", pipeline="psd-object-map-v1"):
    from verdict.semantic_cache_validator import SemanticCacheKey
    return SemanticCacheKey(
        canonicalImageSha256=sha,
        pipelinePolicy=pipeline,
        manifestVersion="psd-object-map-v1",
        analysisVersion="psd-object-map-v1",
        analysisPromptVersion="v0.1",
        maskPolicyVersion="v0.1",
        sourceNormalizationVersion="v0.1",
        semanticRoleSchemaVersion="v0.1",
        model="gpt-4",
        modelConfigHash="old123",
    )


def _finalized_manifest(has_conflict=False, contradictions=None):
    from verdict.unified_semantic_manifest import build_semantic_manifest, finalize
    layers = [
        {"objectId": "obj1", "semanticRole": "human_subject"},
        {"objectId": "obj2", "semanticRole": "background"},
    ]
    m = build_semantic_manifest(job_id="t", spec_id="s", d2_fg_layers=layers)
    if has_conflict:
        m.mask_conflict_ids = ["obj1"]
    if contradictions:
        m.text_human_contradictions = contradictions
    finalize(m)
    return m


def _valid_chain():
    from scene_cleanup.sha_chain import CanonicalSHAChain
    c = CanonicalSHAChain(canonical_sha="abc123")
    c.record_analysis("abc123")
    return c


def _mock_canonical():
    class FakeCanonical:
        canonical_image_sha256 = "abc123"
    return FakeCanonical()


# ── P0-D-1: SemanticCacheKey ──────────────────────────────────────────────────

class TestSemanticCacheKey:
    def test_complete_when_all_fields_set(self):
        k = _valid_key()
        assert k.is_complete() is True

    def test_incomplete_when_field_missing(self):
        from verdict.semantic_cache_validator import SemanticCacheKey
        k = SemanticCacheKey(canonicalImageSha256="abc")  # rest missing
        assert k.is_complete() is False

    def test_missing_fields_returns_list(self):
        from verdict.semantic_cache_validator import SemanticCacheKey
        k = SemanticCacheKey(canonicalImageSha256="abc123")
        missing = k.missing_fields()
        assert "pipelinePolicy" in missing
        assert "manifestVersion" in missing
        assert "model" in missing

    def test_no_missing_when_complete(self):
        k = _valid_key()
        assert k.missing_fields() == []

    def test_is_legacy_v1(self):
        from verdict.semantic_cache_validator import SemanticCacheKey
        k = SemanticCacheKey(pipelinePolicy="psd-object-map-v1")
        is_leg, reason = k.is_legacy()
        assert is_leg is True
        assert "psd-object-map-v1" in reason

    def test_is_legacy_v2(self):
        from verdict.semantic_cache_validator import SemanticCacheKey
        k = SemanticCacheKey(pipelinePolicy="psd-object-map-v2")
        is_leg, _ = k.is_legacy()
        assert is_leg is True

    def test_is_legacy_sfr(self):
        from verdict.semantic_cache_validator import SemanticCacheKey
        k = SemanticCacheKey(pipelinePolicy="source-faithful-repair-v1")
        is_leg, _ = k.is_legacy()
        assert is_leg is True

    def test_is_legacy_legacy_prefix(self):
        from verdict.semantic_cache_validator import SemanticCacheKey
        k = SemanticCacheKey(analysisVersion="legacy-object-map-v3")
        is_leg, _ = k.is_legacy()
        assert is_leg is True

    def test_not_legacy_for_valid_version(self):
        k = _valid_key()
        is_leg, _ = k.is_legacy()
        assert is_leg is False


# ── P0-D-2: validate_cache_hit ───────────────────────────────────────────────

class TestValidateCacheHit:
    def test_accept_matching_valid_key(self):
        from verdict.semantic_cache_validator import validate_cache_hit
        k = _valid_key("abc123")
        valid, reason = validate_cache_hit(k, k)
        assert valid is True
        assert reason == ""

    def test_reject_sha_mismatch(self):
        from verdict.semantic_cache_validator import validate_cache_hit
        cached = _valid_key("old_sha")
        current = _valid_key("new_sha")
        valid, reason = validate_cache_hit(cached, current)
        assert valid is False
        assert "SHA_MISMATCH" in reason

    def test_reject_legacy_version(self):
        from verdict.semantic_cache_validator import validate_cache_hit
        cached = _legacy_key("abc123")
        current = _valid_key("abc123")
        valid, reason = validate_cache_hit(cached, current)
        assert valid is False
        assert "LEGACY_VERSION_REJECTED" in reason

    def test_reject_version_mismatch(self):
        from verdict.semantic_cache_validator import SemanticCacheKey, validate_cache_hit
        cached = _valid_key()
        # Change pipelinePolicy to something non-legacy but wrong
        cached_wrong = SemanticCacheKey(
            **{k: getattr(cached, k) for k in cached.__dataclass_fields__}
        )
        cached_wrong.pipelinePolicy = "full-image-semantic-v0"  # old version
        valid, reason = validate_cache_hit(cached_wrong, cached)
        assert valid is False
        assert "VERSION_MISMATCH" in reason or "LEGACY_VERSION_REJECTED" in reason

    def test_reject_incomplete_cache_key(self):
        from verdict.semantic_cache_validator import SemanticCacheKey, validate_cache_hit
        cached = SemanticCacheKey(canonicalImageSha256="abc123")
        current = _valid_key("abc123")
        valid, reason = validate_cache_hit(cached, current)
        assert valid is False
        assert "INCOMPLETE_CACHE_KEY" in reason

    def test_reject_legacy_psd_v2(self):
        from verdict.semantic_cache_validator import validate_cache_hit
        cached = _legacy_key("abc123", pipeline="psd-object-map-v2")
        current = _valid_key("abc123")
        valid, reason = validate_cache_hit(cached, current)
        assert valid is False


# ── P0-D-3: log_cache_reject ─────────────────────────────────────────────────

class TestLogCacheReject:
    def test_log_emitted(self, capsys):
        from verdict.semantic_cache_validator import log_cache_reject
        log_cache_reject(
            "LEGACY_VERSION_REJECTED",
            cached_version="psd-object-map-v1",
            required_version="full-image-semantic-v1",
            cached_canonical_sha="old_sha",
            current_canonical_sha="new_sha",
            job_id="p0d-log",
        )
        out = capsys.readouterr().out
        assert "[SEMANTIC_CACHE_REJECT]" in out
        assert "jobId=p0d-log" in out
        assert "LEGACY_VERSION_REJECTED" in out


# ── P0-D-4: SemanticPreflightGate ────────────────────────────────────────────

class TestSemanticPreflightGate:
    def _gate(self):
        from verdict.preflight_gate import SemanticPreflightGate
        return SemanticPreflightGate()

    def test_passes_with_valid_inputs(self):
        gate = self._gate()
        r = gate.run_preflight(
            _mock_canonical(),
            _finalized_manifest(),
            _valid_chain(),
            job_id="p0d-pass",
        )
        assert r.passed is True
        assert r.status == "PASSED"

    def test_fails_when_canonical_none(self):
        gate = self._gate()
        r = gate.run_preflight(None, _finalized_manifest(), _valid_chain(), job_id="p0d-nc")
        assert r.passed is False
        assert "CANONICAL_SOURCE_MISSING" in r.reason_codes

    def test_fails_when_manifest_none(self):
        gate = self._gate()
        r = gate.run_preflight(_mock_canonical(), None, _valid_chain(), job_id="p0d-nm")
        assert r.passed is False
        assert "UNIFIED_MANIFEST_INCOMPLETE" in r.reason_codes

    def test_fails_when_manifest_not_finalized(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        gate = self._gate()
        m = build_semantic_manifest(job_id="t", spec_id="s")
        # NOT finalized
        r = gate.run_preflight(_mock_canonical(), m, _valid_chain(), job_id="p0d-nf")
        assert r.passed is False
        assert "UNIFIED_MANIFEST_NOT_FINALIZED" in r.reason_codes

    def test_fails_when_sha_chain_mismatch(self):
        from scene_cleanup.sha_chain import CanonicalSHAChain
        gate = self._gate()
        bad_chain = CanonicalSHAChain(canonical_sha="correct")
        bad_chain.record_analysis("DIFFERENT")
        r = gate.run_preflight(
            _mock_canonical(), _finalized_manifest(), bad_chain, job_id="p0d-sha"
        )
        assert r.passed is False
        assert "CANONICAL_SOURCE_HASH_MISMATCH" in r.reason_codes

    def test_fails_when_required_object_missing(self):
        gate = self._gate()
        r = gate.run_preflight(
            _mock_canonical(),
            _finalized_manifest(),
            _valid_chain(),
            required_object_ids=["obj_nonexistent"],
            job_id="p0d-ro",
        )
        assert r.passed is False
        assert "REQUIRED_SEMANTIC_OBJECT_MISSING" in r.reason_codes

    def test_passes_when_required_object_present(self):
        gate = self._gate()
        r = gate.run_preflight(
            _mock_canonical(),
            _finalized_manifest(),
            _valid_chain(),
            required_object_ids=["obj1"],
            job_id="p0d-rok",
        )
        assert r.passed is True

    def test_fails_when_mask_conflict_present(self):
        gate = self._gate()
        m = _finalized_manifest(has_conflict=True)
        r = gate.run_preflight(_mock_canonical(), m, _valid_chain(), job_id="p0d-mc")
        assert r.passed is False
        assert "MASK_CONFLICT_UNRESOLVED" in r.reason_codes

    def test_fails_when_expected_groups_missing(self):
        gate = self._gate()
        r = gate.run_preflight(
            _mock_canonical(),
            _finalized_manifest(),
            _valid_chain(),
            expected_group_ids=["cta_group_99"],
            job_id="p0d-eg",
        )
        assert r.passed is False
        assert "SEMANTIC_GROUP_INCOMPLETE" in r.reason_codes

    def test_fails_when_role_contradiction(self):
        gate = self._gate()
        m = _finalized_manifest(contradictions=["obj_contradiction"])
        r = gate.run_preflight(_mock_canonical(), m, _valid_chain(), job_id="p0d-rc")
        assert r.passed is False
        assert "SEMANTIC_ROLE_CONTRADICTION_DETECTED" in r.reason_codes

    def test_status_blocked_when_failed(self):
        gate = self._gate()
        r = gate.run_preflight(None, None, None, job_id="p0d-status")
        assert r.status == "BLOCKED/FAILED_PREFLIGHT"
        assert r.passed is False

    def test_log_emitted(self, capsys):
        gate = self._gate()
        gate.run_preflight(
            _mock_canonical(), _finalized_manifest(), _valid_chain(), job_id="p0d-log"
        )
        out = capsys.readouterr().out
        assert "[SEMANTIC_PREFLIGHT]" in out
        assert "jobId=p0d-log" in out
        assert "passed=True" in out

    def test_no_sha_chain_still_passes(self):
        """SHA chain is optional — None means not yet tracked."""
        gate = self._gate()
        r = gate.run_preflight(
            _mock_canonical(), _finalized_manifest(), None, job_id="p0d-nochain"
        )
        assert r.passed is True
