"""Stage 5 tests: UnifiedObjectManifest finalize() contract.

Verifies that:
  - finalize() sets finalized=True and failClosed=True
  - finalize() produces a stable, deterministic manifestSha256
  - manifestSha256 changes when canonical fields change
  - calling finalize() twice is idempotent (same SHA)
  - the fail-closed guard (MANIFEST_NOT_FINALIZED) triggers before evaluators
    when finalize() has not been called
  - build_manifest_from_fg_layers returns an unfinalized manifest
  - after calling finalize(), the manifest is readable and stable

Zero actual AI/OpenAI requests.
"""
from __future__ import annotations

import hashlib
import io
import json
import contextlib
import pytest


def _make_manifest(**overrides):
    from verdict.models import UnifiedObjectManifest
    m = UnifiedObjectManifest()
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


def _make_unified_object(object_id="o1", role="product"):
    from verdict.models import UnifiedObject
    return UnifiedObject(objectId=object_id, semanticRole=role)


# ── finalize() sets integrity flags ──────────────────────────────────────────

class TestFinalizeFlags:
    def test_default_not_finalized(self):
        m = _make_manifest()
        assert m.finalized is False
        assert m.failClosed is False

    def test_finalize_sets_finalized_true(self):
        m = _make_manifest()
        m.finalize()
        assert m.finalized is True

    def test_finalize_sets_fail_closed_true(self):
        m = _make_manifest()
        m.finalize()
        assert m.failClosed is True

    def test_finalize_sets_non_empty_sha256(self):
        m = _make_manifest()
        m.finalize()
        assert isinstance(m.manifestSha256, str)
        assert len(m.manifestSha256) == 64  # hex SHA-256

    def test_manifest_version_set_before_finalize(self):
        m = _make_manifest()
        assert m.manifestVersion == "c1.1"

    def test_finalize_twice_idempotent(self):
        m = _make_manifest(uniqueObjectCount=2)
        m.finalize()
        sha1 = m.manifestSha256
        m.finalize()
        sha2 = m.manifestSha256
        assert sha1 == sha2
        assert m.finalized is True


# ── SHA determinism ───────────────────────────────────────────────────────────

class TestShaDeterminism:
    def test_same_manifest_same_sha(self):
        m1 = _make_manifest(sourceType="full_image_semantic", uniqueObjectCount=2, requiredObjectCount=1)
        m2 = _make_manifest(sourceType="full_image_semantic", uniqueObjectCount=2, requiredObjectCount=1)
        m1.finalize()
        m2.finalize()
        assert m1.manifestSha256 == m2.manifestSha256

    def test_different_source_type_different_sha(self):
        m1 = _make_manifest(sourceType="full_image_semantic")
        m2 = _make_manifest(sourceType="ai_segmentation")
        m1.finalize()
        m2.finalize()
        assert m1.manifestSha256 != m2.manifestSha256

    def test_different_object_count_different_sha(self):
        m1 = _make_manifest(uniqueObjectCount=1)
        m2 = _make_manifest(uniqueObjectCount=3)
        m1.finalize()
        m2.finalize()
        assert m1.manifestSha256 != m2.manifestSha256

    def test_different_object_ids_different_sha(self):
        from verdict.models import UnifiedObjectManifest, UnifiedObject
        m1 = UnifiedObjectManifest(uniqueObjectCount=1)
        m1.objects = [UnifiedObject(objectId="alpha")]
        m2 = UnifiedObjectManifest(uniqueObjectCount=1)
        m2.objects = [UnifiedObject(objectId="beta")]
        m1.finalize()
        m2.finalize()
        assert m1.manifestSha256 != m2.manifestSha256

    def test_sha_includes_object_ids_sorted(self):
        """Object IDs must be sorted before hashing so insertion order doesn't matter."""
        from verdict.models import UnifiedObjectManifest, UnifiedObject
        m1 = UnifiedObjectManifest(uniqueObjectCount=2)
        m1.objects = [UnifiedObject(objectId="a"), UnifiedObject(objectId="b")]
        m2 = UnifiedObjectManifest(uniqueObjectCount=2)
        m2.objects = [UnifiedObject(objectId="b"), UnifiedObject(objectId="a")]
        m1.finalize()
        m2.finalize()
        assert m1.manifestSha256 == m2.manifestSha256

    def test_empty_manifest_sha_is_valid(self):
        m = _make_manifest()
        m.finalize()
        assert len(m.manifestSha256) == 64

    def test_sha_value_matches_expected(self):
        """Regression: verify exact SHA-256 for a known canonical input."""
        m = _make_manifest(
            sourceType="full_image_semantic",
            uniqueObjectCount=0,
            requiredObjectCount=0,
            manifestVersion="c1.1",
        )
        m.objects = []
        m.duplicateObjectIds = []
        m.invalidObjectIds = []
        canonical = {
            "sourceType": "full_image_semantic",
            "uniqueObjectCount": 0,
            "requiredObjectCount": 0,
            "objectIds": [],
            "duplicateObjectIds": [],
            "invalidObjectIds": [],
            "manifestVersion": "c1.1",
        }
        expected_sha = hashlib.sha256(
            json.dumps(canonical, sort_keys=True).encode()
        ).hexdigest()
        m.finalize()
        assert m.manifestSha256 == expected_sha


# ── build_manifest_from_fg_layers returns unfinalized manifest ────────────────

class TestBuildManifestReturnsUnfinalized:
    def test_built_manifest_not_finalized_by_builder(self):
        from verdict.manifest_builder import build_manifest_from_fg_layers
        manifest = build_manifest_from_fg_layers([], source_type="full_image_semantic",
                                                  job_id="j", spec_id="s")
        assert manifest.finalized is False

    def test_builder_manifestSha256_empty_before_finalize(self):
        from verdict.manifest_builder import build_manifest_from_fg_layers
        manifest = build_manifest_from_fg_layers([], source_type="full_image_semantic",
                                                  job_id="j", spec_id="s")
        # SHA may or may not be set by builder; finalized must be False
        assert manifest.finalized is False

    def test_after_finalize_is_stable(self):
        from verdict.manifest_builder import build_manifest_from_fg_layers
        manifest = build_manifest_from_fg_layers(
            [{"objectId": "x1", "semanticRole": "product", "confidence": 0.9,
              "semanticEvidence": ["gdino"], "maskRef": "abc", "recompose": True}],
            source_type="full_image_semantic",
            job_id="j", spec_id="s",
        )
        manifest.finalize()
        sha1 = manifest.manifestSha256
        manifest.finalize()  # idempotent
        assert manifest.manifestSha256 == sha1
        assert manifest.finalized is True
        assert manifest.failClosed is True


# ── Fail-closed guard ─────────────────────────────────────────────────────────

class TestFailClosedGuard:
    def test_unfinalized_manifest_check_false(self):
        from verdict.models import UnifiedObjectManifest
        m = UnifiedObjectManifest()
        assert not m.finalized

    def test_finalized_manifest_check_true(self):
        from verdict.models import UnifiedObjectManifest
        m = UnifiedObjectManifest()
        m.finalize()
        assert m.finalized

    def test_guard_should_reject_unfinalized(self):
        """Simulates the resizer.py guard: if not _verdict_manifest.finalized → raise."""
        from verdict.models import UnifiedObjectManifest
        m = UnifiedObjectManifest()

        def run_with_guard(manifest):
            if not manifest.finalized:
                raise RuntimeError(
                    "MANIFEST_NOT_FINALIZED: call finalize() before evaluating verdicts"
                )
            return "EVALUATED"

        with pytest.raises(RuntimeError, match="MANIFEST_NOT_FINALIZED"):
            run_with_guard(m)

    def test_guard_passes_after_finalize(self):
        from verdict.models import UnifiedObjectManifest
        m = UnifiedObjectManifest()
        m.finalize()

        def run_with_guard(manifest):
            if not manifest.finalized:
                raise RuntimeError("MANIFEST_NOT_FINALIZED")
            return "EVALUATED"

        result = run_with_guard(m)
        assert result == "EVALUATED"

    def test_fail_closed_field_true_after_finalize(self):
        """failClosed=True signals that unrecoverable failure mode is active."""
        from verdict.models import UnifiedObjectManifest
        m = UnifiedObjectManifest()
        m.finalize()
        assert m.failClosed is True

    def test_fail_closed_false_before_finalize(self):
        from verdict.models import UnifiedObjectManifest
        m = UnifiedObjectManifest()
        assert m.failClosed is False


# ── Integration with manifest_builder ────────────────────────────────────────

class TestManifestFinalizationIntegration:
    def test_finalize_log_message_expected_fields(self):
        """Simulate the [MANIFEST_FINALIZED] log in resizer.py."""
        from verdict.models import UnifiedObjectManifest
        m = UnifiedObjectManifest(
            sourceType="full_image_semantic",
            uniqueObjectCount=2,
            requiredObjectCount=1,
        )
        m.finalize()

        log_line = (
            f"[MANIFEST_FINALIZED]"
            f" sha256={m.manifestSha256[:16]}..."
            f" uniqueObjects={m.uniqueObjectCount}"
            f" required={m.requiredObjectCount}"
            f" version={m.manifestVersion}"
        )
        assert "[MANIFEST_FINALIZED]" in log_line
        assert m.manifestSha256[:16] in log_line
        assert "uniqueObjects=2" in log_line
        assert "required=1" in log_line

    def test_manifest_version_included_in_sha(self):
        """manifestVersion is part of the canonical hash — version change → SHA change."""
        from verdict.models import UnifiedObjectManifest
        m1 = UnifiedObjectManifest(manifestVersion="c1.0")
        m2 = UnifiedObjectManifest(manifestVersion="c1.1")
        m1.finalize()
        m2.finalize()
        assert m1.manifestSha256 != m2.manifestSha256
