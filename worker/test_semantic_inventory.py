"""Stage 3 tests: SemanticSceneInventory — expected required roles before extraction.

Verifies that build_semantic_inventory:
  - classifies layers as high-quality or rejected based on confidence/evidence/maskRef
  - expectedRequiredRoles contains only high-quality layers with inherently-required roles
  - rejected layers are logged with reason codes
  - empty input returns empty inventory
  - does not derive required roles from contaminated objects

Zero actual AI/OpenAI requests.
"""
from __future__ import annotations

import io
import contextlib
import pytest


def _run(layers, job_id="j", spec_id="s"):
    from scene_cleanup.semantic_inventory import build_semantic_inventory
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        inv = build_semantic_inventory(layers, job_id=job_id, spec_id=spec_id)
    return inv, buf.getvalue()


def _good_layer(object_id="o1", role="product", confidence=0.9,
                evidence=None, mask_ref="abc123"):
    return {
        "objectId": object_id,
        "role": role,
        "semanticRole": role,      # build_semantic_manifest reads semanticRole
        "confidence": confidence,
        "semanticEvidence": evidence if evidence is not None else ["gdino_box"],
        "maskRef": mask_ref,
    }


def _bad_layer(object_id="bad1", role="product", confidence=0.0,
               evidence=None, mask_ref=""):
    return {
        "objectId": object_id,
        "role": role,
        "semanticRole": role,
        "confidence": confidence,
        "semanticEvidence": evidence if evidence is not None else [],
        "maskRef": mask_ref,
    }


# ── Empty input ───────────────────────────────────────────────────────────────

class TestEmptyInventory:
    def test_empty_layers_returns_empty_inventory(self):
        inv, _ = _run([])
        assert inv.expectedRequiredRoles == []
        assert inv.detectedRoles == []
        assert inv.highQualityLayers == []
        assert inv.rejectedLayers == []

    def test_none_layers_treated_as_empty(self):
        from scene_cleanup.semantic_inventory import build_semantic_inventory
        inv = build_semantic_inventory(None, job_id="j", spec_id="s")
        assert inv.expectedRequiredRoles == []

    def test_empty_emits_inventory_build_log(self):
        _, out = _run([])
        assert "SEMANTIC_INVENTORY_BUILD" in out


# ── Quality filtering ─────────────────────────────────────────────────────────

class TestQualityFiltering:
    def test_high_quality_layer_passes(self):
        inv, _ = _run([_good_layer()])
        assert len(inv.highQualityLayers) == 1
        assert len(inv.rejectedLayers) == 0

    def test_confidence_zero_rejected(self):
        layer = _good_layer()
        layer["confidence"] = 0.0
        inv, _ = _run([layer])
        assert len(inv.rejectedLayers) == 1
        assert "CONFIDENCE_ZERO" in inv.rejectionReasons.get("o1", [])

    def test_no_evidence_rejected(self):
        layer = _good_layer()
        layer["semanticEvidence"] = []
        inv, _ = _run([layer])
        assert len(inv.rejectedLayers) == 1
        assert "NO_SEMANTIC_EVIDENCE" in inv.rejectionReasons.get("o1", [])

    def test_no_mask_ref_rejected(self):
        layer = _good_layer()
        layer["maskRef"] = ""
        inv, _ = _run([layer])
        assert len(inv.rejectedLayers) == 1
        assert "NO_MASK_REF" in inv.rejectionReasons.get("o1", [])

    def test_all_three_failures_recorded(self):
        layer = _bad_layer()
        inv, _ = _run([layer])
        reasons = inv.rejectionReasons.get("bad1", [])
        assert "CONFIDENCE_ZERO" in reasons
        assert "NO_SEMANTIC_EVIDENCE" in reasons
        assert "NO_MASK_REF" in reasons

    def test_mixed_layers_separated(self):
        layers = [
            _good_layer("g1", "product"),
            _bad_layer("b1", "title"),
            _good_layer("g2", "cta"),
        ]
        inv, _ = _run(layers)
        assert len(inv.highQualityLayers) == 2
        assert len(inv.rejectedLayers) == 1

    def test_rejected_layer_logged(self):
        _, out = _run([_bad_layer("bad99")])
        assert "SEMANTIC_INVENTORY_REJECT" in out
        assert "bad99" in out


# ── Expected required roles ───────────────────────────────────────────────────

class TestExpectedRequiredRoles:
    def test_product_is_required_when_high_quality(self):
        inv, _ = _run([_good_layer(role="product")])
        assert "product" in inv.expectedRequiredRoles

    def test_cta_is_required_when_high_quality(self):
        inv, _ = _run([_good_layer(role="cta")])
        assert "cta" in inv.expectedRequiredRoles

    def test_contaminated_product_not_required(self):
        layer = _bad_layer(role="product")
        inv, _ = _run([layer])
        assert "product" not in inv.expectedRequiredRoles

    def test_non_inherently_required_role_not_in_expected(self):
        # "background" is not inherently required even when high-quality
        layer = _good_layer(role="background")
        inv, _ = _run([layer])
        assert "background" not in inv.expectedRequiredRoles

    def test_mixed_quality_product_only_high_required(self):
        """One high-quality product + one contaminated title → only product required."""
        layers = [
            _good_layer("g1", "product"),
            _bad_layer("b1", "title"),
        ]
        inv, _ = _run(layers)
        assert "product" in inv.expectedRequiredRoles
        assert "title" not in inv.expectedRequiredRoles

    def test_detected_roles_includes_all(self):
        """detectedRoles includes both high-quality and rejected layers' roles."""
        layers = [
            _good_layer("g1", "product"),
            _bad_layer("b1", "title"),
        ]
        inv, _ = _run(layers)
        assert "product" in inv.detectedRoles
        assert "title" in inv.detectedRoles

    def test_expected_roles_are_sorted(self):
        layers = [
            _good_layer("g1", "title"),
            _good_layer("g2", "cta"),
            _good_layer("g3", "product"),
        ]
        inv, _ = _run(layers)
        assert inv.expectedRequiredRoles == sorted(inv.expectedRequiredRoles)


# ── Inventory log output ──────────────────────────────────────────────────────

class TestInventoryLogging:
    def test_inventory_build_log_emitted(self):
        _, out = _run([_good_layer()])
        assert "SEMANTIC_INVENTORY_BUILD" in out

    def test_inventory_build_log_has_counts(self):
        _, out = _run([_good_layer("g1"), _bad_layer("b1")])
        assert "detectedCount=2" in out
        assert "highQualityCount=1" in out
        assert "rejectedCount=1" in out

    def test_inventory_build_log_has_expected_roles(self):
        _, out = _run([_good_layer(role="product")])
        assert "expectedRequiredRoles=" in out
        assert "product" in out

    def test_reject_log_contains_reason_codes(self):
        _, out = _run([_bad_layer("badobj")])
        assert "SEMANTIC_INVENTORY_REJECT" in out
        assert "CONFIDENCE_ZERO" in out or "NO_SEMANTIC_EVIDENCE" in out


# ── build_semantic_manifest uses high-quality layers ────────────────────────

class TestManifestUsesHighQualityLayers:
    """After Stage 3, build_semantic_manifest receives filtered layers."""

    def test_manifest_excludes_contaminated_objects(self):
        from scene_cleanup.semantic_inventory import build_semantic_inventory
        from verdict.unified_semantic_manifest import build_semantic_manifest

        layers = [
            _good_layer("g1", "product"),  # high quality
            _bad_layer("b1", "title"),      # contaminated
        ]
        inv = build_semantic_inventory(layers, job_id="j", spec_id="s")
        manifest = build_semantic_manifest(
            job_id="j", spec_id="s",
            d2_fg_layers=inv.highQualityLayers,
        )
        # Only product (high quality) should be in preserve_roles
        assert "product" in manifest.preserve_roles
        # title from contaminated layer should NOT appear
        assert "title" not in manifest.preserve_roles

    def test_manifest_object_count_reflects_filtered(self):
        from scene_cleanup.semantic_inventory import build_semantic_inventory
        from verdict.unified_semantic_manifest import build_semantic_manifest

        layers = [
            _good_layer("g1"),
            _good_layer("g2"),
            _bad_layer("b1"),
        ]
        inv = build_semantic_inventory(layers)
        manifest = build_semantic_manifest(d2_fg_layers=inv.highQualityLayers)
        assert manifest.object_count == 2  # only high-quality
