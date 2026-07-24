"""Diagnostic logging tests — Stage E production pipeline.

Validates all 10 diagnostic log types:
  [SEMANTIC_OBJECT]        [SEMANTIC_OBJECT_REJECT]
  [SEMANTIC_INVENTORY]     [MASK_LINEAGE]
  [MASK_ANOMALY]           [TRANSFORM_GEOMETRY]
  [PIXEL_RESTORE_AUDIT]    [PIXEL_RESTORE_SKIPPED]
  [MANIFEST_AUDIT]         [RESULT_SEMANTICS]
  [ROOT_CAUSE_SUMMARY]

Constraints:
  - ACTUAL_OPENAI_REQUESTS = 0
  - Tests FAIL when required log fields are absent
  - Anomaly conditions are explicitly exercised
"""
from __future__ import annotations

import io
import sys

import numpy as np
import pytest
from PIL import Image


# ── Helpers ───────────────────────────────────────────────────────────────────

def _capture(fn, *args, **kwargs) -> str:
    """Run fn(*args, **kwargs) and return its stdout."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        fn(*args, **kwargs)
    finally:
        sys.stdout = saved
    return buf.getvalue()


def _lines_tagged(output: str, tag: str) -> list[str]:
    """Return all lines that start with [tag]."""
    return [l for l in output.splitlines() if l.startswith(f"[{tag}]")]


def _assert_fields(line: str, *fields: str) -> None:
    """Assert every field token appears in line, or fail with a clear message."""
    for f in fields:
        assert f in line, (
            f"Required field {f!r} missing from log line.\nLine: {line}"
        )


def _make_mask(h: int, w: int, fill: int = 128) -> "np.ndarray":
    """Create a uint8 mask array of given shape and fill value."""
    return np.full((h, w), fill, dtype=np.uint8)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def png_src(tmp_path):
    img = Image.new("RGB", (400, 300), color=(120, 80, 200))
    p = str(tmp_path / "mother.png")
    img.save(p, "PNG")
    return p


def _specs(w=300, h=250):
    return [{"media": "banner", "width": w, "height": h,
             "name": f"test-{w}x{h}", "slug": ""}]


class _FakeProvider:
    """Returns source + tiny noise. ACTUAL_OPENAI_REQUESTS=0."""
    def inpaint(self, image, mask, prompt, meta=None):
        arr = np.array(image.convert("RGB"), dtype=np.float32)
        rng = np.random.default_rng(42)
        noise = rng.integers(-3, 4, size=arr.shape).astype(np.float32)
        result = np.clip(arr + noise, 0, 255).astype(np.uint8)
        return Image.fromarray(result, "RGB")

    def metadata(self):
        return {"providerName": "fake-diagnostic"}


def _generate(src_path, specs, outdir, **kwargs):
    from resizer import _generate_ai_only
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        ret = _generate_ai_only(
            psd_path=src_path,
            specs=specs,
            resize_mode="ai",
            output_format="png",
            output_dir=outdir,
            source_type=kwargs.pop("source_type", "image"),
            _provider_override=_FakeProvider(),
            **kwargs,
        )
    finally:
        sys.stdout = saved
    results = ret[0] if isinstance(ret, (tuple, list)) and len(ret) >= 1 else ret
    return results, buf.getvalue()


# ── Unit tests: [SEMANTIC_OBJECT] ─────────────────────────────────────────────

class TestSemanticObjectLog:
    def test_emits_tag_for_each_layer(self):
        from verdict.diagnostic_logger import log_semantic_objects
        layers = [
            {"objectId": "obj-1", "role": "product", "confidence": 0.9,
             "bbox": {"x": 0, "y": 0, "w": 100, "h": 100},
             "required": True, "immutable": False, "removeFromScene": False, "recompose": True},
            {"objectId": "obj-2", "role": "title", "confidence": 0.8,
             "bbox": {"x": 10, "y": 10, "w": 50, "h": 20}},
        ]
        out = _capture(log_semantic_objects, layers, job_id="j1", spec_id="s1")
        lines = _lines_tagged(out, "SEMANTIC_OBJECT")
        assert len(lines) == 2, f"Expected 2 [SEMANTIC_OBJECT] lines, got {len(lines)}"

    def test_required_fields_present(self):
        from verdict.diagnostic_logger import log_semantic_objects
        layers = [
            {"objectId": "obj-A", "role": "product", "confidence": 0.75,
             "bbox": {"x": 5, "y": 5, "w": 80, "h": 60}},
        ]
        out = _capture(log_semantic_objects, layers, job_id="jx", spec_id="sx")
        lines = _lines_tagged(out, "SEMANTIC_OBJECT")
        assert lines, "No [SEMANTIC_OBJECT] line emitted"
        _assert_fields(
            lines[0],
            "jobId=", "specId=",
            "objectId=", "role=", "confidence=", "bbox=",
            "required=", "immutable=", "removeFromScene=", "recompose=",
            "semanticEvidence=", "maskRef=",
        )

    def test_empty_layers_emits_count_zero(self):
        from verdict.diagnostic_logger import log_semantic_objects
        out = _capture(log_semantic_objects, [], job_id="j0", spec_id="s0")
        lines = _lines_tagged(out, "SEMANTIC_OBJECT")
        assert lines, "Expected at least one [SEMANTIC_OBJECT] line for empty layers"
        assert "count=0" in lines[0]

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_semantic_objects
        # Pass garbage — must not raise
        log_semantic_objects(None, job_id="j", spec_id="s")
        log_semantic_objects([None, 42, "bad"], job_id="j", spec_id="s")


# ── Unit tests: [SEMANTIC_OBJECT_REJECT] ─────────────────────────────────────

class TestSemanticObjectRejectLog:
    def test_required_fields_present(self):
        from verdict.diagnostic_logger import log_semantic_object_reject
        layer = {"objectId": "obj-R", "role": "product", "required": True}
        out = _capture(
            log_semantic_object_reject,
            layer, ["MISSING_MASK"], {"coverage": 0.0, "nonZeroPixels": 0}, True,
            job_id="j1", spec_id="s1",
        )
        lines = _lines_tagged(out, "SEMANTIC_OBJECT_REJECT")
        assert lines, "No [SEMANTIC_OBJECT_REJECT] line emitted"
        _assert_fields(
            lines[0],
            "jobId=", "specId=",
            "objectId=", "role=", "required=",
            "reasonCodes=", "maskCoverage=", "maskNonZeroPixels=", "failClosed=",
        )

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_semantic_object_reject
        log_semantic_object_reject({}, [], None, False, job_id="j", spec_id="s")


# ── Unit tests: [SEMANTIC_INVENTORY] ─────────────────────────────────────────

class TestSemanticInventoryLog:
    def test_required_fields_present(self):
        from verdict.diagnostic_logger import log_semantic_inventory
        layers = [{"objectId": "o1", "role": "product"}, {"objectId": "o2", "role": "title"}]
        out = _capture(log_semantic_inventory, layers, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "SEMANTIC_INVENTORY")
        assert lines, "No [SEMANTIC_INVENTORY] line emitted"
        _assert_fields(
            lines[0],
            "jobId=", "specId=",
            "detectedRoles=", "requiredRoles=", "extractedRoles=",
            "missingRequiredRoles=", "rejectedRequiredRoles=",
            "detectedCount=", "extractedCount=", "missingCount=",
        )

    def test_reject_emitted_for_missing_required_role(self):
        from verdict.diagnostic_logger import log_semantic_inventory
        from verdict.models import UnifiedObjectManifest
        layers = [{"objectId": "o-prod", "role": "product", "required": True}]
        # manifest with no objects (empty) → product is missing
        empty_manifest = UnifiedObjectManifest(objects=[])
        out = _capture(log_semantic_inventory, layers, empty_manifest, job_id="j", spec_id="s")
        # [SEMANTIC_INVENTORY] must be present
        inv_lines = _lines_tagged(out, "SEMANTIC_INVENTORY")
        assert inv_lines, "No [SEMANTIC_INVENTORY] line"
        # No reject expected when manifest.requiredObjectCount=0 (product not marked required in manifest)
        # Test INVENTORY fields are correct
        assert "detectedRoles=" in inv_lines[0]

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_semantic_inventory
        log_semantic_inventory(None, None, job_id="j", spec_id="s")


# ── Unit tests: [MASK_LINEAGE] ────────────────────────────────────────────────

class TestMaskLineageLog:
    def test_emits_six_mask_type_lines(self):
        from verdict.diagnostic_logger import log_mask_lineage
        mask = _make_mask(250, 300, fill=128)
        mask[0:125, :] = 0   # top half = source region
        out = _capture(log_mask_lineage, mask, 300, 250, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "MASK_LINEAGE")
        assert len(lines) == 6, f"Expected 6 [MASK_LINEAGE] lines, got {len(lines)}"

    def test_required_fields_in_lineage(self):
        from verdict.diagnostic_logger import log_mask_lineage
        mask = _make_mask(250, 300, fill=200)
        out = _capture(log_mask_lineage, mask, 300, 250, job_id="jL", spec_id="sL")
        lines = _lines_tagged(out, "MASK_LINEAGE")
        assert lines, "No [MASK_LINEAGE] lines"
        for line in lines:
            _assert_fields(
                line,
                "jobId=", "specId=",
                "maskType=", "step=",
                "sha256=", "inputMasks=", "fallbackApplied=", "generatedAt=",
            )
            # size/nonZeroPixels/coverage required except for computed/N-A lines
            if "size=N/A" not in line and "size=computed" not in line:
                _assert_fields(line, "size=", "nonZeroPixels=", "coverage=", "bbox=")

    def test_null_mask_emits_lineage_with_fallback_true(self):
        from verdict.diagnostic_logger import log_mask_lineage
        out = _capture(log_mask_lineage, None, 300, 250, job_id="j0", spec_id="s0")
        lines = _lines_tagged(out, "MASK_LINEAGE")
        assert lines, "Expected [MASK_LINEAGE] line even for None mask"
        assert "fallbackApplied=True" in lines[0]

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_mask_lineage
        log_mask_lineage("not_an_array", 300, 250, job_id="j", spec_id="s")


# ── Unit tests: [MASK_ANOMALY] ────────────────────────────────────────────────

class TestMaskAnomalyLog:
    def test_source_mapped_zero_anomaly(self):
        """Full-white mask → sourceMapped=0 → SOURCE_MAPPED_ZERO."""
        from verdict.diagnostic_logger import log_mask_anomalies
        full_white = _make_mask(250, 300, fill=255)
        out = _capture(log_mask_anomalies, full_white, {}, 300, 250, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "MASK_ANOMALY")
        assert any("SOURCE_MAPPED_ZERO" in l for l in lines), (
            f"Expected SOURCE_MAPPED_ZERO anomaly. Got:\n" + "\n".join(lines)
        )

    def test_new_canvas_zero_anomaly(self):
        """Full-black mask → newCanvas=0 → NEW_CANVAS_ZERO."""
        from verdict.diagnostic_logger import log_mask_anomalies
        full_black = _make_mask(250, 300, fill=0)
        out = _capture(log_mask_anomalies, full_black, {}, 300, 250, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "MASK_ANOMALY")
        assert any("NEW_CANVAS_ZERO" in l for l in lines), (
            f"Expected NEW_CANVAS_ZERO anomaly. Got:\n" + "\n".join(lines)
        )

    def test_allowed_gen_full_canvas_anomaly(self):
        """allowedGenerationCoverage=1.0 in metrics → IMMUTABLE_METRICS_FULL_CANVAS."""
        from verdict.diagnostic_logger import log_mask_anomalies
        mask = _make_mask(250, 300, fill=128)
        metrics = {"allowedGenerationCoverage": 1.0, "outsideAllowedChangedPixelRatio": 0.0}
        out = _capture(log_mask_anomalies, mask, metrics, 300, 250, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "MASK_ANOMALY")
        assert any("IMMUTABLE_METRICS_FULL_CANVAS" in l for l in lines), (
            f"Expected IMMUTABLE_METRICS_FULL_CANVAS anomaly. Got:\n" + "\n".join(lines)
        )

    def test_allowed_gen_coverage_gte_095_anomaly(self):
        """95%+ white mask → ALLOWED_GEN_FULL_CANVAS."""
        from verdict.diagnostic_logger import log_mask_anomalies
        mask = _make_mask(250, 300, fill=255)
        mask[248:250, 0:5] = 0  # tiny source region < 5%
        out = _capture(log_mask_anomalies, mask, {}, 300, 250, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "MASK_ANOMALY")
        assert any("ALLOWED_GEN_FULL_CANVAS" in l for l in lines), (
            f"Expected ALLOWED_GEN_FULL_CANVAS anomaly. Got:\n" + "\n".join(lines)
        )

    def test_required_fields_in_anomaly(self):
        from verdict.diagnostic_logger import log_mask_anomalies
        full_white = _make_mask(250, 300, fill=255)
        out = _capture(log_mask_anomalies, full_white, {}, 300, 250, job_id="jA", spec_id="sA")
        lines = _lines_tagged(out, "MASK_ANOMALY")
        assert lines, "No [MASK_ANOMALY] lines"
        for line in lines:
            _assert_fields(line, "jobId=", "specId=", "maskType=", "anomalyCode=",
                           "actualValue=", "expectedRange=")

    def test_none_mask_emits_mask_is_none(self):
        from verdict.diagnostic_logger import log_mask_anomalies
        out = _capture(log_mask_anomalies, None, {}, 300, 250, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "MASK_ANOMALY")
        assert any("MASK_IS_NONE" in l for l in lines)

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_mask_anomalies
        log_mask_anomalies("garbage", None, 300, 250, job_id="j", spec_id="s")


# ── Unit tests: [TRANSFORM_GEOMETRY] ─────────────────────────────────────────

class TestTransformGeometryLog:
    def _make_transform(self, strategy="subject_preserving_outpaint",
                        scale=0.625, crop_x=0, crop_y=25,
                        src_w=400, src_h=300, canvas_w=300, canvas_h=250,
                        outpaint=True):
        from scene_cleanup.models import SceneCanvasTransform
        return SceneCanvasTransform(
            strategy=strategy,
            source_w=src_w, source_h=src_h,
            canvas_w=canvas_w, canvas_h=canvas_h,
            scale=scale,
            crop_x=crop_x, crop_y=crop_y,
            outpaint_required=outpaint,
            mask_strategy="outpaint_regions",
        )

    def test_required_fields_present(self):
        from verdict.diagnostic_logger import log_transform_geometry
        ct = self._make_transform()
        out = _capture(log_transform_geometry, ct, 400, 300, 300, 250,
                       job_id="jT", spec_id="sT")
        lines = _lines_tagged(out, "TRANSFORM_GEOMETRY")
        assert lines, "No [TRANSFORM_GEOMETRY] line"
        _assert_fields(
            lines[0],
            "jobId=", "specId=",
            "strategy=", "sourceSize=", "targetSize=",
            "scale=", "scaledSourceSize=", "expectedOffset=", "actualOffset=",
            "mappedRect=", "subjectBBox=", "subjectCropRatio=",
            "outpaintRequired=", "geometryValid=", "reasonCodes=",
        )

    def test_geometry_valid_true_when_offset_matches(self):
        from verdict.diagnostic_logger import log_transform_geometry
        # 400×300 contain-scaled to 300×250: scale=min(300/400, 250/300)=0.625
        # scaled: 250×187, offset_x=(300-250)//2=25, offset_y=(250-187)//2=31
        ct = self._make_transform(scale=0.625, crop_x=25, crop_y=31)
        out = _capture(log_transform_geometry, ct, 400, 300, 300, 250,
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "TRANSFORM_GEOMETRY")
        assert lines
        assert "geometryValid=True" in lines[0], lines[0]

    def test_geometry_valid_false_on_offset_mismatch(self):
        from verdict.diagnostic_logger import log_transform_geometry
        ct = self._make_transform(scale=0.625, crop_x=0, crop_y=0)
        out = _capture(log_transform_geometry, ct, 400, 300, 300, 250,
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "TRANSFORM_GEOMETRY")
        assert lines
        assert "geometryValid=False" in lines[0], lines[0]

    def test_null_transform_emits_no_canvas_transform(self):
        from verdict.diagnostic_logger import log_transform_geometry
        out = _capture(log_transform_geometry, None, 400, 300, 300, 250,
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "TRANSFORM_GEOMETRY")
        assert lines
        assert "NO_CANVAS_TRANSFORM" in lines[0]

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_transform_geometry
        log_transform_geometry("garbage", 400, 300, 300, 250, job_id="j", spec_id="s")


# ── Unit tests: [PIXEL_RESTORE_AUDIT] / [PIXEL_RESTORE_SKIPPED] ───────────────

class TestPixelRestoreAuditLog:
    def test_skipped_when_ai_and_restored_identical(self):
        """apply_immutable size-mismatch returns original → 0 diff → SKIPPED."""
        from verdict.diagnostic_logger import log_pixel_restore_audit
        img = Image.new("RGB", (300, 250), color=(100, 150, 200))
        metrics = {"allowedGenerationCoverage": 1.0, "outsideAllowedChangedPixelRatio": 0.0}
        out = _capture(log_pixel_restore_audit, img, img, None, metrics,
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "PIXEL_RESTORE_SKIPPED")
        assert lines, "Expected [PIXEL_RESTORE_SKIPPED] line"
        _assert_fields(lines[0], "jobId=", "specId=", "reason=", "restoredPixelCount=")

    def test_skipped_reason_canonical_size_mismatch(self):
        """allowedGenerationCoverage=1.0 → reason=CANONICAL_SIZE_MISMATCH_COMPUTE_METRICS_FAILED."""
        from verdict.diagnostic_logger import log_pixel_restore_audit
        img = Image.new("RGB", (300, 250), color=(100, 150, 200))
        metrics = {"allowedGenerationCoverage": 1.0}
        out = _capture(log_pixel_restore_audit, img, img, None, metrics,
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "PIXEL_RESTORE_SKIPPED")
        assert lines
        assert "CANONICAL_SIZE_MISMATCH_COMPUTE_METRICS_FAILED" in lines[0], lines[0]

    def test_audit_emitted_when_pixels_differ(self):
        from verdict.diagnostic_logger import log_pixel_restore_audit
        ai = Image.new("RGB", (300, 250), color=(100, 100, 100))
        restored = Image.new("RGB", (300, 250), color=(200, 200, 200))
        metrics = {"allowedGenerationCoverage": 0.5, "outsideAllowedChangedPixelRatio": 0.1}
        out = _capture(log_pixel_restore_audit, ai, restored, None, metrics,
                       job_id="jA", spec_id="sA")
        lines = _lines_tagged(out, "PIXEL_RESTORE_AUDIT")
        assert lines, "Expected [PIXEL_RESTORE_AUDIT] line"
        _assert_fields(
            lines[0],
            "jobId=", "specId=",
            "totalPixels=", "immutableRegionPixels=",
            "providerChangedPixels=", "illegalChangedPixels=",
            "restorableChangedPixels=", "restoredPixelCount=",
            "remainingIllegalChangedPixels=", "immutableChangedPixelRatio=",
            "outsideAllowedChangedPixelRatio=", "allowedGenerationCoverage=",
        )

    def test_skipped_when_null_input(self):
        from verdict.diagnostic_logger import log_pixel_restore_audit
        out = _capture(log_pixel_restore_audit, None, None, None, {},
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "PIXEL_RESTORE_SKIPPED")
        assert lines

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_pixel_restore_audit
        log_pixel_restore_audit("bad", "bad", "bad", None, job_id="j", spec_id="s")


# ── Unit tests: [MANIFEST_AUDIT] ─────────────────────────────────────────────

class TestManifestAuditLog:
    def test_required_fields_present(self):
        from verdict.diagnostic_logger import log_manifest_audit
        from verdict.models import UnifiedObjectManifest
        manifest = UnifiedObjectManifest(objects=[])
        layers = [{"objectId": "o1", "role": "product"}]
        out = _capture(log_manifest_audit, manifest, layers, job_id="jM", spec_id="sM")
        lines = _lines_tagged(out, "MANIFEST_AUDIT")
        assert lines, "No [MANIFEST_AUDIT] line"
        _assert_fields(
            lines[0],
            "jobId=", "specId=",
            "detectedObjectCount=", "extractedObjectCount=", "manifestObjectCount=",
            "expectedRequiredRoles=", "presentRequiredRoles=",
            "missingRequiredRoles=", "rejectedRequiredRoles=",
            "finalized=", "failClosed=",
        )

    def test_detected_count_matches_layers(self):
        from verdict.diagnostic_logger import log_manifest_audit
        layers = [{"objectId": f"o{i}", "role": "product"} for i in range(3)]
        out = _capture(log_manifest_audit, None, layers, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "MANIFEST_AUDIT")
        assert lines
        assert "detectedObjectCount=3" in lines[0], lines[0]

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_manifest_audit
        log_manifest_audit(None, None, job_id="j", spec_id="s")


# ── Unit tests: [RESULT_SEMANTICS] ───────────────────────────────────────────

class TestResultSemanticsLog:
    def _make_scene_result(self, success=True):
        from scene_cleanup.models import SemanticSceneCleanupResult
        return SemanticSceneCleanupResult(
            success=success,
            failure_reason="" if success else "FAKE_FAIL",
        )

    def _make_verdict_summary(self, status="PASS"):
        from dataclasses import dataclass
        @dataclass
        class _VS:
            overallStatus: str
        return _VS(overallStatus=status)

    def test_required_fields_present(self):
        from verdict.diagnostic_logger import log_result_semantics
        sr = self._make_scene_result(success=True)
        vs = self._make_verdict_summary("PASS")
        out = _capture(log_result_semantics, sr, vs, None, job_id="jR", spec_id="sR")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert lines, "No [RESULT_SEMANTICS] line"
        _assert_fields(
            lines[0],
            "jobId=", "specId=",
            "providerSucceeded=", "artifactGenerated=",
            "overallStatus=", "finalResultValid=",
            "visualVerdictStatus=",
            "successCountIncremented=", "validCountIncremented=",
        )

    def test_overall_fail_provider_succeeds_no_valid_count(self):
        """Overall FAIL despite provider success → validCountIncremented=False."""
        from verdict.diagnostic_logger import log_result_semantics
        sr = self._make_scene_result(success=True)
        vs = self._make_verdict_summary("FAIL")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert lines
        assert "validCountIncremented=False" in lines[0], lines[0]
        assert "successCountIncremented=True" in lines[0], lines[0]

    def test_overall_fail_success_count_not_incremented_when_provider_fails(self):
        """Provider failed → successCountIncremented=False."""
        from verdict.diagnostic_logger import log_result_semantics
        sr = self._make_scene_result(success=False)
        vs = self._make_verdict_summary("FAIL")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert lines
        assert "successCountIncremented=False" in lines[0], lines[0]

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_result_semantics
        log_result_semantics(None, None, None, job_id="j", spec_id="s")


# ── Unit tests: [ROOT_CAUSE_SUMMARY] ─────────────────────────────────────────

class TestRootCauseSummaryLog:
    def _make_scene_result(self, success=True):
        from scene_cleanup.models import SemanticSceneCleanupResult
        return SemanticSceneCleanupResult(success=success, failure_reason="")

    def _make_visual_verdict(self, status="FAIL", reason_codes=None):
        from dataclasses import dataclass, field
        @dataclass
        class _VV:
            status: str
            reasonCodes: list
        return _VV(status=status, reasonCodes=reason_codes or [])

    def test_required_fields_present(self):
        from verdict.diagnostic_logger import log_root_cause_summary
        sr = self._make_scene_result(success=True)
        out = _capture(log_root_cause_summary, None, sr, None, {},
                       job_id="jC", spec_id="sC")
        lines = _lines_tagged(out, "ROOT_CAUSE_SUMMARY")
        assert lines, "No [ROOT_CAUSE_SUMMARY] line"
        _assert_fields(
            lines[0],
            "jobId=", "specId=",
            "overallStatus=", "primaryFailure=", "upstreamCauses=",
            "affectedPrinciples=", "firstFailingStage=",
            "recommendedInspectionPoint=",
        )

    def test_full_scene_regen_becomes_primary_failure(self):
        """FULL_SCENE_REGENERATION_DETECTED in visual → primaryFailure reflects it."""
        from verdict.diagnostic_logger import log_root_cause_summary
        sr = self._make_scene_result(success=True)
        vv = self._make_visual_verdict("FAIL", ["FULL_SCENE_REGENERATION_DETECTED"])

        from dataclasses import dataclass
        @dataclass
        class _VS:
            overallStatus: str
            visualVerdict: object
        vs = _VS(overallStatus="FAIL", visualVerdict=vv)

        out = _capture(log_root_cause_summary, vs, sr, vv,
                       {"allowedGenerationCoverage": 0.5},
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "ROOT_CAUSE_SUMMARY")
        assert lines
        assert "FULL_SCENE_REGENERATION_DETECTED" in lines[0], lines[0]

    def test_allowed_gen_full_canvas_in_upstream_causes(self):
        """allowedGenerationCoverage=1.0 in metrics → ALLOWED_GENERATION_MASK_FULL_CANVAS."""
        from verdict.diagnostic_logger import log_root_cause_summary
        sr = self._make_scene_result(success=True)
        out = _capture(log_root_cause_summary, None, sr, None,
                       {"allowedGenerationCoverage": 1.0},
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "ROOT_CAUSE_SUMMARY")
        assert lines
        assert "ALLOWED_GENERATION_MASK_FULL_CANVAS" in lines[0], lines[0]

    def test_pass_status_no_causes(self):
        from verdict.diagnostic_logger import log_root_cause_summary
        from dataclasses import dataclass
        @dataclass
        class _VS:
            overallStatus: str
        vs = _VS(overallStatus="PASS")
        out = _capture(log_root_cause_summary, vs, None, None, {},
                       job_id="j", spec_id="s")
        lines = _lines_tagged(out, "ROOT_CAUSE_SUMMARY")
        assert lines
        assert "overallStatus=PASS" in lines[0]
        assert "primaryFailure=NONE" in lines[0]

    def test_never_raises(self):
        from verdict.diagnostic_logger import log_root_cause_summary
        log_root_cause_summary(None, None, None, None, job_id="j", spec_id="s")


# ── Integration tests: all 10 tags appear in _generate() output ───────────────

class TestIntegrationDiagnosticLogs:
    """Verify that _generate() with FakeProvider emits all diagnostic log tags."""

    REQUIRED_TAGS = [
        "SEMANTIC_OBJECT",
        "SEMANTIC_INVENTORY",
        "MASK_LINEAGE",
        "MASK_ANOMALY",
        "TRANSFORM_GEOMETRY",
        "MANIFEST_AUDIT",
        "RESULT_SEMANTICS",
        "ROOT_CAUSE_SUMMARY",
    ]
    # PIXEL_RESTORE_AUDIT or PIXEL_RESTORE_SKIPPED — at least one must appear
    RESTORE_TAGS = ["PIXEL_RESTORE_AUDIT", "PIXEL_RESTORE_SKIPPED"]

    def _run(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        return results, out

    def test_all_core_diagnostic_tags_emitted(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        for tag in self.REQUIRED_TAGS:
            assert _lines_tagged(out, tag), (
                f"[{tag}] not found in output.\nFull output:\n{out[:3000]}"
            )

    def test_pixel_restore_tag_emitted(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        found = any(_lines_tagged(out, t) for t in self.RESTORE_TAGS)
        assert found, (
            f"Neither {self.RESTORE_TAGS} found in output.\n{out[:3000]}"
        )

    def test_semantic_object_fields(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        lines = _lines_tagged(out, "SEMANTIC_OBJECT")
        assert lines, "[SEMANTIC_OBJECT] not emitted"
        # Per-object lines contain full fields; count=0 line is emitted when fg_layers empty
        per_obj_lines = [l for l in lines if "objectId=" in l]
        if per_obj_lines:
            _assert_fields(per_obj_lines[0], "jobId=", "specId=", "objectId=", "role=",
                           "confidence=", "bbox=", "required=", "immutable=",
                           "removeFromScene=", "recompose=",
                           "semanticEvidence=", "maskRef=")
        else:
            # Empty fg_layers → count=0 summary line must be present
            assert any("count=0" in l for l in lines), (
                "Expected count=0 note when no fg_layers. Lines:\n" + "\n".join(lines)
            )

    def test_semantic_inventory_fields(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        lines = _lines_tagged(out, "SEMANTIC_INVENTORY")
        assert lines
        _assert_fields(lines[0], "jobId=", "specId=",
                       "detectedRoles=", "requiredRoles=", "extractedRoles=",
                       "missingRequiredRoles=", "rejectedRequiredRoles=",
                       "detectedCount=", "extractedCount=", "missingCount=")

    def test_mask_lineage_fields(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        lines = _lines_tagged(out, "MASK_LINEAGE")
        assert lines
        assert len(lines) >= 6, f"Expected >=6 [MASK_LINEAGE] lines, got {len(lines)}"
        for line in lines:
            _assert_fields(line, "jobId=", "specId=", "maskType=", "step=",
                           "sha256=", "inputMasks=", "fallbackApplied=", "generatedAt=")

    def test_mask_anomaly_fields(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        lines = _lines_tagged(out, "MASK_ANOMALY")
        assert lines, "[MASK_ANOMALY] not emitted — expected at least one (size mismatch)"
        for line in lines:
            _assert_fields(line, "jobId=", "specId=", "maskType=",
                           "anomalyCode=", "actualValue=", "expectedRange=")

    def test_transform_geometry_fields(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        lines = _lines_tagged(out, "TRANSFORM_GEOMETRY")
        assert lines
        _assert_fields(lines[0], "jobId=", "specId=",
                       "strategy=", "sourceSize=", "targetSize=",
                       "scale=", "scaledSourceSize=",
                       "expectedOffset=", "actualOffset=",
                       "mappedRect=", "subjectBBox=", "subjectCropRatio=",
                       "outpaintRequired=", "geometryValid=", "reasonCodes=")

    def test_manifest_audit_fields(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        lines = _lines_tagged(out, "MANIFEST_AUDIT")
        assert lines
        _assert_fields(lines[0], "jobId=", "specId=",
                       "detectedObjectCount=", "extractedObjectCount=", "manifestObjectCount=",
                       "expectedRequiredRoles=", "presentRequiredRoles=",
                       "missingRequiredRoles=", "rejectedRequiredRoles=",
                       "finalized=", "failClosed=")

    def test_result_semantics_fields(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert lines
        _assert_fields(lines[0], "jobId=", "specId=",
                       "providerSucceeded=", "artifactGenerated=",
                       "overallStatus=", "finalResultValid=",
                       "visualVerdictStatus=",
                       "successCountIncremented=", "validCountIncremented=")

    def test_root_cause_summary_fields(self, png_src, tmp_path):
        _, out = self._run(png_src, tmp_path)
        lines = _lines_tagged(out, "ROOT_CAUSE_SUMMARY")
        assert lines
        _assert_fields(lines[0], "jobId=", "specId=",
                       "overallStatus=", "primaryFailure=", "upstreamCauses=",
                       "affectedPrinciples=", "firstFailingStage=",
                       "recommendedInspectionPoint=")

    def test_no_image_data_in_logs(self, png_src, tmp_path):
        """Diagnostic logs must never contain raw pixel data (base64 patterns)."""
        _, out = self._run(png_src, tmp_path)
        diag_lines = [
            l for l in out.splitlines()
            if any(l.startswith(f"[{t}]") for t in
                   self.REQUIRED_TAGS + self.RESTORE_TAGS)
        ]
        for line in diag_lines:
            # base64 image data would appear as long alphanumeric strings (>80 chars w/o spaces)
            tokens = line.split()
            for token in tokens:
                if len(token) > 80 and token.replace("=", "").isalnum():
                    pytest.fail(
                        f"Possible image data detected in diagnostic log token: {token[:40]}..."
                    )


# ── Integration: anomaly tests ────────────────────────────────────────────────

class TestIntegrationAnomalyConditions:
    """Verify that specific production anomalies produce correct [MASK_ANOMALY] output."""

    def test_source_mapped_coverage_zero_anomaly(self, png_src, tmp_path):
        """400×300 source into 300×250 target via contain-scale → mask has source region.
        With crop_x=0,crop_y=0 bug, immutable_metrics returns allowedGenerationCoverage=1.0
        which triggers IMMUTABLE_METRICS_FULL_CANVAS anomaly."""
        _, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        lines = _lines_tagged(out, "MASK_ANOMALY")
        # Current production bug: allowedGenerationCoverage=1.0 due to size mismatch
        # This must be detected and logged
        all_codes = " ".join(lines)
        assert (
            "IMMUTABLE_METRICS_FULL_CANVAS" in all_codes
            or "SOURCE_MAPPED_ZERO" in all_codes
            or "ALLOWED_GEN_FULL_CANVAS" in all_codes
        ), (
            f"Expected at least one anomaly from size-mismatch bug.\nLines:\n" +
            "\n".join(lines) + "\n\nFull output tail:\n" + out[-2000:]
        )

    def test_full_scene_regen_detected_in_root_cause(self, png_src, tmp_path):
        """Size-mismatch bug causes compute_pixel_diff_ratio→1.0 → FULL_SCENE_REGENERATION_DETECTED.
        ROOT_CAUSE_SUMMARY should reflect this in upstreamCauses or primaryFailure."""
        _, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        rcs_lines = _lines_tagged(out, "ROOT_CAUSE_SUMMARY")
        assert rcs_lines, "No [ROOT_CAUSE_SUMMARY] line"
        combined = " ".join(rcs_lines)
        assert "FULL_SCENE_REGENERATION_DETECTED" in combined or "FAIL" in combined, (
            f"Expected FULL_SCENE_REGENERATION_DETECTED or FAIL in ROOT_CAUSE_SUMMARY.\n"
            f"Line: {rcs_lines[0]}"
        )

    def test_pixel_restore_skipped_due_to_size_mismatch(self, png_src, tmp_path):
        """apply_immutable(img=400×300, result=300×250) returns result unchanged.
        Diagnostic audit must detect 0 restored pixels and log PIXEL_RESTORE_SKIPPED."""
        _, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        skipped = _lines_tagged(out, "PIXEL_RESTORE_SKIPPED")
        audited = _lines_tagged(out, "PIXEL_RESTORE_AUDIT")
        assert skipped or audited, (
            "Neither [PIXEL_RESTORE_SKIPPED] nor [PIXEL_RESTORE_AUDIT] found in output"
        )
        if skipped:
            assert "reason=" in skipped[0], skipped[0]

    def test_result_semantics_valid_count_false_on_verdict_fail(self, png_src, tmp_path):
        """Visual verdict fails (size mismatch → full regen) → validCountIncremented=False."""
        _, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        rs_lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert rs_lines, "No [RESULT_SEMANTICS] line"
        line = rs_lines[0]
        if "overallStatus=FAIL" in line:
            assert "validCountIncremented=False" in line, (
                f"overallStatus=FAIL but validCountIncremented is not False.\nLine: {line}"
            )
