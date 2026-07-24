"""Stage E P0/P1 Risk Hardening — integration tests.

Verifies that all P0/P1 contracts hold together in a single pipeline run
using synthetic mother-ad fixtures (PSD/PNG/JPG). No real OpenAI calls.

P0 contracts:
  P0-A: renderProvenance includes pipeline sequence fields
  P0-B: pixel restorer and mask conflict modules importable and functional
  P0-C: CanonicalSHAChain and manifest finalization guard work together
  P0-D: SemanticCacheKey completeness + SemanticPreflightGate integration

P1 contracts:
  P1-A: RetryManifestInvariant captures attempt-1 state and blocks forbidden mutations
  P1-B: SubjectPreservingTransform produces contain-scale with full allowed mask
  P1-C: SubjectAvoidanceMask + GroupRGBABuilder chain together
  P1-D: evaluate_extended_visual produces evidence dict with all 15 metric keys

Synthetic fixture: 400×300 PNG with distinct content areas (red left, blue right)
used as a "mother ad" — stands in for a real PSD/PNG/JPG source.

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile

import numpy as np
import pytest
from PIL import Image


# ── Fixture factories ─────────────────────────────────────────────────────────

def _mother_ad_png(w=400, h=300) -> str:
    """Synthetic mother-ad PNG: left half red, right half blue."""
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, : w // 2] = (200, 50, 50)
    arr[:, w // 2 :] = (50, 50, 200)
    img = Image.fromarray(arr, "RGB")
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path, format="PNG")
    return path


def _mother_ad_jpg(w=400, h=300) -> str:
    """Synthetic mother-ad JPG."""
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, : w // 2] = (180, 60, 60)
    arr[:, w // 2 :] = (60, 60, 180)
    img = Image.fromarray(arr, "RGB")
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img.save(path, format="JPEG", quality=85)
    return path


class _FakeProvider:
    """Returns a slightly varied version of input image (not all-noise)."""

    def metadata(self):
        return {"providerName": "fake-p0p1", "modelName": "fake-p0p1-v1"}

    def inpaint(self, image, mask, prompt, options):
        arr = np.array(image, dtype=np.uint8)
        rng = np.random.RandomState(42)
        noise = rng.randint(-20, 20, arr.shape, dtype=np.int16)
        result = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        return Image.fromarray(result, "RGB")


def _run_pipeline(src_path, source_type="image", job_id="hardening") -> tuple[list, str]:
    from resizer import _generate_ai_only
    specs = [{"media": "ht", "name": "a", "slug": "a", "width": 300, "height": 250}]
    tmp = tempfile.mkdtemp()
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        os.environ.pop("PSD_LAYER_HINTS_ENABLED", None)
        os.environ.pop("VISUAL_VERDICT_ENABLED", None)
        results, _ = _generate_ai_only(
            psd_path=src_path,
            specs=specs,
            resize_mode="ai-auto",
            output_format="png",
            output_dir=tmp,
            source_type=source_type,
            job_id=job_id,
            _provider_override=_FakeProvider(),
        )
        return results, buf.getvalue()
    finally:
        sys.stdout = old
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("PSD_LAYER_HINTS_ENABLED", None)
        os.environ.pop("VISUAL_VERDICT_ENABLED", None)


# ── P0-A: Pipeline sequence provenance ───────────────────────────────────────

class TestP0APipelineSequence:
    """P0-A: renderProvenance must contain pipeline sequence tracking fields."""

    def test_png_provenance_has_pipeline_sequence_valid(self):
        src = _mother_ad_png()
        try:
            results, _ = _run_pipeline(src, "image", "p0a-png")
            prov = results[0].get("renderProvenance", {})
            assert "pipelineSequenceValid" in prov, (
                f"pipelineSequenceValid missing from renderProvenance: {list(prov.keys())}"
            )
        finally:
            os.unlink(src)

    def test_jpg_provenance_has_pipeline_sequence_valid(self):
        src = _mother_ad_jpg()
        try:
            results, _ = _run_pipeline(src, "jpg", "p0a-jpg")
            prov = results[0].get("renderProvenance", {})
            assert "pipelineSequenceValid" in prov
        finally:
            os.unlink(src)

    def test_png_analysis_before_target_transform(self):
        src = _mother_ad_png()
        try:
            results, _ = _run_pipeline(src, "image", "p0a-order")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("analysisBeforeTargetTransform") is True
        finally:
            os.unlink(src)

    def test_extraction_before_target_transform(self):
        src = _mother_ad_png()
        try:
            results, _ = _run_pipeline(src, "image", "p0a-ext")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("extractionBeforeTargetTransform") is True
        finally:
            os.unlink(src)

    def test_pipeline_log_emitted(self):
        src = _mother_ad_png()
        try:
            _, logs = _run_pipeline(src, "image", "p0a-log")
            assert "[PIPELINE_SEQUENCE]" in logs
        finally:
            os.unlink(src)


# ── P0-B: Pixel restorer + mask conflict modules ──────────────────────────────

class TestP0BPixelAndMaskModules:
    """P0-B: Both modules importable and produce correct output contracts."""

    def test_pixel_restorer_apply_returns_pil_image(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = Image.new("RGB", (200, 100), color=(100, 50, 20))
        ai_result = Image.new("RGB", (200, 100), color=(30, 200, 150))
        # Allowed mask: left half
        mask_arr = np.zeros((100, 200), dtype=np.uint8)
        mask_arr[:, :100] = 255
        result = apply_default_immutable_policy(canonical, ai_result, mask_arr)
        assert isinstance(result, Image.Image)
        assert result.size == (200, 100)

    def test_pixel_restorer_canonical_pixels_preserved_outside_mask(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = Image.new("RGB", (100, 50), color=(255, 0, 0))
        ai_result = Image.new("RGB", (100, 50), color=(0, 255, 0))
        # Allowed mask: only left 50 pixels
        mask_arr = np.zeros((50, 100), dtype=np.uint8)
        mask_arr[:, :50] = 255
        result = apply_default_immutable_policy(canonical, ai_result, mask_arr)
        arr = np.array(result)
        # Right half should be canonical (red), not AI (green)
        right_half = arr[:, 50:, :]
        assert int(right_half[:, :, 0].mean()) > 200  # red channel dominant

    def test_mask_conflict_no_conflict_baseline(self):
        from scene_cleanup.mask_conflict import MaskConflictDetector
        det = MaskConflictDetector()
        prod_mask = np.zeros((100, 100), dtype=np.uint8)
        human_mask = np.zeros((100, 100), dtype=np.uint8)
        prod_mask[:50, :50] = 255   # top-left
        human_mask[50:, 50:] = 255  # bottom-right — no overlap
        result = det.check_conflict(prod_mask, human_mask)
        assert not result.has_conflict

    def test_mask_conflict_detected_when_overlap(self):
        from scene_cleanup.mask_conflict import MaskConflictDetector
        det = MaskConflictDetector()
        prod_mask = np.ones((100, 100), dtype=np.uint8) * 255
        human_mask = np.ones((100, 100), dtype=np.uint8) * 255
        result = det.check_conflict(prod_mask, human_mask)
        assert result.has_conflict

    def test_mask_conflict_log_emitted(self, capsys):
        from scene_cleanup.mask_conflict import MaskConflictDetector, log_mask_conflict_analysis
        det = MaskConflictDetector()
        prod_mask = np.zeros((100, 100), dtype=np.uint8)
        human_mask = np.zeros((100, 100), dtype=np.uint8)
        result = det.check_conflict(prod_mask, human_mask)
        log_mask_conflict_analysis(result, job_id="p0b-log", spec_id="300x250")
        out = capsys.readouterr().out
        assert "[MASK_CONFLICT_ANALYSIS]" in out


# ── P0-C: SHA chain + manifest finalization ───────────────────────────────────

class TestP0CSHAChainAndManifest:
    """P0-C: SHA chain and manifest finalization work together end-to-end."""

    def test_sha_chain_validate_canonical_not_set(self):
        from scene_cleanup.sha_chain import CanonicalSHAChain
        chain = CanonicalSHAChain()
        violations = chain.validate_chain()
        assert "CANONICAL_SHA_NOT_SET" in violations

    def test_sha_chain_passes_after_canonical_set(self):
        from scene_cleanup.sha_chain import build_sha_chain_from_canonical
        chain = build_sha_chain_from_canonical("abc123")
        violations = chain.validate_chain()
        assert "CANONICAL_SHA_NOT_SET" not in violations

    def test_sha_chain_mismatch_detected(self):
        from scene_cleanup.sha_chain import build_sha_chain_from_canonical
        chain = build_sha_chain_from_canonical("abc123")
        chain.analysis_sha = "DIFFERENT"
        violations = chain.validate_chain()
        assert len(violations) > 0

    def test_manifest_finalization_blocks_immutable_mutation(self):
        from verdict.unified_semantic_manifest import (
            SemanticManifest, finalize, try_mutate_field
        )
        m = SemanticManifest(job_id="p0c-blk")
        finalize(m)
        with pytest.raises(RuntimeError, match="MANIFEST_MUTATION_AFTER_FINALIZE"):
            try_mutate_field(m, "preserve_roles", ["extra_role"])

    def test_manifest_allows_non_immutable_mutation_after_finalize(self):
        from verdict.unified_semantic_manifest import (
            SemanticManifest, finalize, try_mutate_field
        )
        m = SemanticManifest(job_id="p0c-allow")
        finalize(m)
        # Non-immutable fields (e.g., manifest_sha256) can be mutated after finalization
        try_mutate_field(m, "manifest_sha256", "new_sha")
        assert m.manifest_sha256 == "new_sha"

    def test_sha_chain_log_emitted(self, capsys):
        from scene_cleanup.sha_chain import build_sha_chain_from_canonical, log_sha_chain
        chain = build_sha_chain_from_canonical("deadbeef")
        log_sha_chain(chain, job_id="p0c-sha", spec_id="300x250")
        out = capsys.readouterr().out
        assert "[CANONICAL_SHA_CHAIN]" in out
        assert "allMatched=true" in out


# ── P0-D: Cache validator + preflight gate ────────────────────────────────────

class TestP0DCachePreflight:
    """P0-D: Cache validation and preflight gate integration."""

    def test_legacy_cache_key_rejected(self):
        from verdict.semantic_cache_validator import SemanticCacheKey, validate_cache_hit
        sha = "abc123"
        old_key = SemanticCacheKey(
            canonicalImageSha256=sha,
            pipelinePolicy="psd-object-map-v1",  # legacy prefix → rejected
        )
        new_key = SemanticCacheKey(canonicalImageSha256=sha)
        valid, reason = validate_cache_hit(old_key, new_key)
        assert not valid
        assert "legacy" in reason.lower() or "incompatible" in reason.lower()

    def test_incomplete_cache_key_rejected(self):
        from verdict.semantic_cache_validator import SemanticCacheKey
        # Only canonicalImageSha256 set; 9 required fields missing
        key = SemanticCacheKey(canonicalImageSha256="abc")
        assert not key.is_complete()

    def test_preflight_blocks_missing_canonical(self):
        from verdict.preflight_gate import SemanticPreflightGate
        gate = SemanticPreflightGate()
        result = gate.run_preflight(
            canonical_src=None,
            manifest=None,
            sha_chain=None,
            required_object_ids=[],
            expected_group_ids=[],
            job_id="p0d-gate",
            spec_id="300x250",
        )
        assert not result.passed
        assert "CANONICAL_SOURCE_MISSING" in result.reason_codes

    def test_preflight_passes_with_valid_minimal_inputs(self):
        from verdict.preflight_gate import SemanticPreflightGate
        from verdict.unified_semantic_manifest import SemanticManifest, finalize
        from scene_cleanup.sha_chain import build_sha_chain_from_canonical

        # Preflight checks canonical_src.canonical_image_sha256 attribute
        class _FakeCanonicalSrc:
            canonical_image_sha256 = "abc123"

        manifest = SemanticManifest(job_id="p0d-pass")
        finalize(manifest)
        chain = build_sha_chain_from_canonical("abc123")

        gate = SemanticPreflightGate()
        result = gate.run_preflight(
            canonical_src=_FakeCanonicalSrc(),
            manifest=manifest,
            sha_chain=chain,
            required_object_ids=[],
            expected_group_ids=[],
            job_id="p0d-pass",
            spec_id="300x250",
        )
        assert result.passed, f"Expected preflight to pass, got: {result.reason_codes}"

    def test_preflight_log_emitted(self, capsys):
        from verdict.preflight_gate import SemanticPreflightGate
        gate = SemanticPreflightGate()
        gate.run_preflight(
            canonical_src=None,
            manifest=None,
            sha_chain=None,
            required_object_ids=[],
            expected_group_ids=[],
            job_id="p0d-log",
            spec_id="300x250",
        )
        out = capsys.readouterr().out
        assert "[SEMANTIC_PREFLIGHT]" in out


# ── P1-A: Retry manifest invariant ───────────────────────────────────────────

class TestP1ARetryInvariant:
    """P1-A: RetryManifestInvariant blocks forbidden mutations."""

    def test_capture_and_validate_pass_identical(self):
        from scene_cleanup.retry_invariant import RetryManifestInvariant
        from verdict.unified_semantic_manifest import SemanticManifest

        m = SemanticManifest(
            preserve_object_ids=["obj1", "obj2"],
            removal_object_ids=["obj3"],
        )
        inv = RetryManifestInvariant()
        inv.capture_attempt1(m, canonical_sha="sha1")
        # Same manifest → should pass
        inv.validate_retry(m, attempt=2)

    def test_capture_and_validate_fails_removed_preserve(self):
        from scene_cleanup.retry_invariant import RetryManifestInvariant
        from verdict.unified_semantic_manifest import SemanticManifest

        m = SemanticManifest(
            preserve_object_ids=["obj1", "obj2"],
            removal_object_ids=[],
        )
        inv = RetryManifestInvariant()
        inv.capture_attempt1(m, canonical_sha="sha1")

        # Remove a preserve ID → forbidden
        m.preserve_object_ids = ["obj1"]  # obj2 removed
        with pytest.raises(RuntimeError, match="RETRY_MANIFEST_MUTATION_FORBIDDEN|PRESERVE_SHRINK"):
            inv.validate_retry(m, attempt=2)

    def test_retry_invariant_log_emitted(self, capsys):
        from scene_cleanup.retry_invariant import RetryManifestInvariant, log_retry_invariant
        from verdict.unified_semantic_manifest import SemanticManifest

        m = SemanticManifest()
        inv = RetryManifestInvariant()
        inv.capture_attempt1(m, canonical_sha="sha1")
        delta = inv.get_delta(m)
        log_retry_invariant(inv, delta, job_id="p1a-log", spec_id="300x250", attempt=2)
        out = capsys.readouterr().out
        assert "[SEMANTIC_RETRY_INVARIANT]" in out


# ── P1-B: Subject-preserving transform ───────────────────────────────────────

class TestP1BSubjectPreservingTransform:
    """P1-B: Contain-scale outpaint generates correct mask contract."""

    def test_contain_scale_png_to_target(self):
        from scene_cleanup.subject_preserving_transform import SubjectPreservingTransform
        t = SubjectPreservingTransform()
        r = t.compute(400, 300, 300, 250)
        assert r.scale == pytest.approx(min(300 / 400, 250 / 300), abs=0.01)

    def test_allowed_mask_covers_full_target(self):
        from scene_cleanup.subject_preserving_transform import SubjectPreservingTransform
        t = SubjectPreservingTransform()
        r = t.compute(400, 300, 300, 250)
        total = r.target_w * r.target_h
        allowed = int((r.allowed_generation_mask > 0).sum())
        assert allowed == total

    def test_source_mapped_plus_new_canvas_full(self):
        from scene_cleanup.subject_preserving_transform import SubjectPreservingTransform
        t = SubjectPreservingTransform()
        r = t.compute(400, 300, 600, 250)
        total = r.target_w * r.target_h
        source_pixels = int((r.source_mapped_region_mask > 0).sum())
        new_pixels = int((r.new_canvas_region_mask > 0).sum())
        assert source_pixels + new_pixels == total

    def test_background_scene_transform_label(self):
        from scene_cleanup.subject_preserving_transform import SubjectPreservingTransform
        t = SubjectPreservingTransform()
        r = t.compute(400, 300, 300, 250)
        assert r.background_scene_transform == "subject-preserving-outpaint"


# ── P1-C: Avoidance mask + group RGBA builder ─────────────────────────────────

class TestP1CLayoutComponents:
    """P1-C: Avoidance mask and group builder chain together."""

    def test_avoidance_mask_built_from_bboxes(self):
        from layout.avoidance_mask import SubjectAvoidanceMask
        sam = SubjectAvoidanceMask(canvas_w=300, canvas_h=250)
        bboxes = [
            {"x": 10, "y": 10, "w": 50, "h": 40, "type": "face"},
            {"x": 200, "y": 100, "w": 60, "h": 50, "type": "hand"},
        ]
        mask = sam.build_avoidance_mask(bboxes, margin=5)
        assert mask.shape == (250, 300)
        assert mask[10, 10] == 255  # inside face bbox
        assert mask[0, 0] == 0      # corner not in any bbox

    def test_group_rgba_builder_synthetic_cta_group(self):
        from layout.group_rgba_builder import GroupRGBABuilder
        builder = GroupRGBABuilder()
        layout = {
            "width": 200, "height": 60,
            "children": [
                {"objectId": "cta_text", "x": 10, "y": 10, "w": 150, "h": 40, "required": True},
                {"objectId": "cta_bg", "x": 0, "y": 0, "w": 200, "h": 60, "required": False},
            ],
        }
        child_images = {
            "cta_text": Image.new("RGBA", (150, 40), color=(255, 255, 255, 255)),
            "cta_bg": Image.new("RGBA", (200, 60), color=(50, 100, 200, 200)),
        }
        r = builder.build_group_image(child_images, layout, group_id="cta1", group_type="cta")
        assert r.group_image_created is True
        assert r.all_required_children_rendered is True
        assert r.group_image.size == (200, 60)

    def test_occlusion_validation_chains_avoidance(self):
        from layout.avoidance_mask import SubjectAvoidanceMask
        sam = SubjectAvoidanceMask(canvas_w=300, canvas_h=250)
        face_bboxes = [{"x": 50, "y": 50, "w": 80, "h": 60, "type": "face"}]
        # CTA placed far from face → passes
        cta_placed = {"x": 200, "y": 150, "w": 80, "h": 40}
        result = sam.validate_subject_occlusion(cta_placed, face_bboxes)
        assert result.passed is True

    def test_occlusion_fails_when_cta_covers_face(self):
        from layout.avoidance_mask import SubjectAvoidanceMask
        sam = SubjectAvoidanceMask(canvas_w=300, canvas_h=250, face_threshold=0.10)
        face_bboxes = [{"x": 50, "y": 50, "w": 80, "h": 60, "type": "face"}]
        # CTA placed directly over face
        cta_placed = {"x": 50, "y": 50, "w": 80, "h": 60}
        result = sam.validate_subject_occlusion(cta_placed, face_bboxes)
        assert result.passed is False
        assert result.reason_code == "FACE_OCCLUSION_EXCEEDED"


# ── P1-D: Visual verdict with synthetic mother-ad images ─────────────────────

class TestP1DVisualVerdictMothered:
    """P1-D: evaluate_extended_visual with synthetic PNG source images."""

    def test_pass_with_minor_noise_source_result(self):
        from verdict.visual_evaluator import evaluate_extended_visual
        from verdict.models import PASS

        rng = np.random.default_rng(200)
        src_arr = (rng.random((300, 400, 3)) * 255).astype(np.uint8)
        src = Image.fromarray(src_arr, "RGB")
        # Result = same with minor noise
        noise = rng.integers(-15, 15, src_arr.shape, dtype=np.int16)
        res_arr = np.clip(src_arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        res = Image.fromarray(res_arr, "RGB")

        r = evaluate_extended_visual(
            source_img=src,
            result_img=res,
            target_w=400,
            target_h=300,
            job_id="p1d-pass",
        )
        assert r.status == PASS

    def test_evidence_has_all_15_metric_keys(self):
        from verdict.visual_evaluator import evaluate_extended_visual

        img = Image.new("RGB", (400, 300), color=(100, 150, 200))
        # Add noise to avoid pure-blank detection
        arr = np.array(img, dtype=np.uint8)
        rng = np.random.default_rng(201)
        noise = rng.integers(-30, 30, arr.shape, dtype=np.int16)
        arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr, "RGB")

        r = evaluate_extended_visual(
            source_img=img,
            result_img=img,
            target_w=400,
            target_h=300,
            job_id="p1d-keys",
        )
        expected = {
            "immutableChangedPixelRatio", "outsideAllowedChangedPixelRatio",
            "sceneSimilarityScore", "backgroundSemanticDriftScore",
            "fullSceneRegenerationScore", "productVisibilityRatio",
            "titleVisibilityRatio", "ctaVisibilityRatio", "titleContrastRatio",
            "ctaContrastRatio", "faceOcclusionRatio", "handOcclusionRatio",
            "groupCompletenessRatio", "duplicateObjectCount", "blankOutputScore",
        }
        assert expected <= set(r.evidence.keys())

    def test_mother_ad_png_pipeline_produces_all_provenance_fields(self):
        """End-to-end: mother-ad PNG → pipeline → renderProvenance has P0-A fields."""
        src = _mother_ad_png()
        try:
            results, logs = _run_pipeline(src, "image", "p1d-e2e")
            prov = results[0].get("renderProvenance", {})
            # P0-A sequence tracking fields
            for field in ("pipelineSequenceValid", "analysisBeforeTargetTransform",
                          "extractionBeforeTargetTransform"):
                assert field in prov, f"{field} missing from renderProvenance"
            # Pipeline ran
            assert "[PIPELINE_SEQUENCE]" in logs
        finally:
            os.unlink(src)

    def test_mother_ad_jpg_pipeline_produces_all_provenance_fields(self):
        """End-to-end: mother-ad JPG → pipeline → renderProvenance has P0-A fields."""
        src = _mother_ad_jpg()
        try:
            results, logs = _run_pipeline(src, "jpg", "p1d-e2e-jpg")
            prov = results[0].get("renderProvenance", {})
            for field in ("pipelineSequenceValid", "analysisBeforeTargetTransform",
                          "extractionBeforeTargetTransform"):
                assert field in prov, f"{field} missing from renderProvenance"
        finally:
            os.unlink(src)


# ── Cross-cutting: All modules importable from single test run ────────────────

class TestAllP0P1ModulesImportable:
    """Sanity: all P0/P1 modules load without import errors."""

    def test_p0a_importable(self):
        from scene_cleanup.pipeline_sequence import (
            PipelineSequenceTracker, validate_sequence, build_provenance_fields
        )
        assert callable(validate_sequence)
        assert callable(build_provenance_fields)

    def test_p0b_importable(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        from scene_cleanup.mask_conflict import MaskConflictDetector
        assert callable(apply_default_immutable_policy)
        assert MaskConflictDetector

    def test_p0c_importable(self):
        from scene_cleanup.sha_chain import CanonicalSHAChain, build_sha_chain_from_canonical
        from verdict.unified_semantic_manifest import finalize, try_mutate_field
        assert callable(build_sha_chain_from_canonical)
        assert callable(finalize)
        assert callable(try_mutate_field)

    def test_p0d_importable(self):
        from verdict.semantic_cache_validator import SemanticCacheKey, validate_cache_hit
        from verdict.preflight_gate import SemanticPreflightGate
        assert callable(validate_cache_hit)
        assert SemanticPreflightGate

    def test_p1a_importable(self):
        from scene_cleanup.retry_invariant import RetryManifestInvariant
        assert RetryManifestInvariant

    def test_p1b_importable(self):
        from scene_cleanup.subject_preserving_transform import (
            SubjectPreservingTransform, validate_subject_not_cropped
        )
        assert callable(validate_subject_not_cropped)

    def test_p1c_importable(self):
        from layout.avoidance_mask import SubjectAvoidanceMask
        from layout.group_rgba_builder import GroupRGBABuilder
        assert SubjectAvoidanceMask
        assert GroupRGBABuilder

    def test_p1d_importable(self):
        from verdict.visual_evaluator import (
            compute_extended_visual_metrics, evaluate_extended_visual
        )
        assert callable(compute_extended_visual_metrics)
        assert callable(evaluate_extended_visual)
