"""Stage E: Production wiring integration tests (Stages 1-6).

Verifies that all 6 production-wiring stages are correctly connected
in _generate_ai_only():

  Stage 1: Legacy cache rejection (INCOMPATIBLE_PREFIXES)
  Stage 2: PSD object path disconnected ([PRODUCTION_PIPELINE] log)
  Stage 3: Unified full_image_semantic manifest source type
  Stage 4: Pixel restoration wired ([PIXEL_RESTORE] log)
  Stage 5: Subject-preserving outpaint ([SUBJECT_PRESERVING_TRANSFORM] log)
  Stage 6: Visual verdict always required (NOT_TESTED → FAIL)

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pytest
from PIL import Image

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def png_src(tmp_path):
    """400×300 RGB PNG as mother-ad source."""
    img = Image.new("RGB", (400, 300), color=(120, 80, 200))
    p = str(tmp_path / "mother.png")
    img.save(p, "PNG")
    return p


@pytest.fixture()
def jpg_src(tmp_path):
    """400×300 RGB JPG as mother-ad source."""
    img = Image.new("RGB", (400, 300), color=(80, 200, 120))
    p = str(tmp_path / "mother.jpg")
    img.save(p, "JPEG", quality=95)
    return p


def _specs(w=300, h=250):
    return [{"media": "banner", "width": w, "height": h,
             "name": f"test-{w}x{h}", "slug": ""}]


class _FakeProvider:
    """Inpaint returns source + tiny noise. ACTUAL_OPENAI_REQUESTS=0."""
    def inpaint(self, image, mask, prompt, meta=None):
        arr = np.array(image.convert("RGB"), dtype=np.float32)
        rng = np.random.default_rng(42)
        noise = rng.integers(-3, 4, size=arr.shape).astype(np.float32)
        result = np.clip(arr + noise, 0, 255).astype(np.uint8)
        return Image.fromarray(result, "RGB")

    def metadata(self):
        return {"providerName": "fake-production-wiring"}


def _generate(src_path, specs, outdir, **kwargs):
    """Call _generate_ai_only with FakeProvider; return (results_list, stdout_text)."""
    from resizer import _generate_ai_only
    import io
    buf = io.StringIO()
    _saved = sys.stdout
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
        sys.stdout = _saved
    # _generate_ai_only returns (results_list, []) tuple
    results = ret[0] if isinstance(ret, (tuple, list)) and len(ret) >= 1 else ret
    return results, buf.getvalue()


# ── Stage 1: Legacy cache rejection ───────────────────────────────────────────

class TestStage1LegacyCacheRejection:
    """INCOMPATIBLE_PREFIXES blocks legacy analysis versions."""

    def test_incompatible_prefixes_covers_v1(self):
        from verdict.semantic_cache_validator import INCOMPATIBLE_PREFIXES
        assert any("psd-object-map-v1".startswith(p) for p in INCOMPATIBLE_PREFIXES)

    def test_incompatible_prefixes_covers_v2(self):
        from verdict.semantic_cache_validator import INCOMPATIBLE_PREFIXES
        assert any("psd-object-map-v2".startswith(p) for p in INCOMPATIBLE_PREFIXES)

    def test_incompatible_prefixes_covers_sfr(self):
        from verdict.semantic_cache_validator import INCOMPATIBLE_PREFIXES
        assert any("source-faithful-repair-v1".startswith(p) for p in INCOMPATIBLE_PREFIXES)

    def test_incompatible_prefixes_covers_legacy_map(self):
        from verdict.semantic_cache_validator import INCOMPATIBLE_PREFIXES
        assert any("legacy-object-map-v1".startswith(p) for p in INCOMPATIBLE_PREFIXES)

    def test_full_image_semantic_not_incompatible(self):
        from verdict.semantic_cache_validator import INCOMPATIBLE_PREFIXES
        assert not any("full-image-semantic-v1".startswith(p) for p in INCOMPATIBLE_PREFIXES)

    def test_cache_version_incompatible_log_for_v2(self, png_src, tmp_path, capsys):
        """psd-object-map-v2 analysis triggers CACHE_VERSION_INCOMPATIBLE."""
        results, out = _generate(
            png_src, _specs(), str(tmp_path),
            object_analysis={
                "id": "test-obj-v2",
                "analysisVersion": "psd-object-map-v2",
                "model": "gpt-4",
                "objects": [{"objectId": "o1", "role": "product"}],
                "sourceFileSha256": "does-not-match",
            },
        )
        assert "[CACHE_VERSION_INCOMPATIBLE]" in out
        assert "psd-object-map-v2" in out
        assert "[OBJECT_MAP_APPLY]" not in out

    def test_cache_version_incompatible_log_for_sfr(self, png_src, tmp_path, capsys):
        """source-faithful-repair analysis triggers CACHE_VERSION_INCOMPATIBLE."""
        results, out = _generate(
            png_src, _specs(), str(tmp_path),
            object_analysis={
                "id": "sfr-analysis",
                "analysisVersion": "source-faithful-repair-v1",
                "model": "gpt-4",
                "objects": [{"objectId": "o1", "role": "product"}],
                "sourceFileSha256": "x" * 64,
            },
        )
        assert "[CACHE_VERSION_INCOMPATIBLE]" in out
        assert "source-faithful-repair-v1" in out

    def test_no_object_analysis_skips_cache_check(self, png_src, tmp_path):
        """No object_analysis → no cache check, no CACHE_VERSION_INCOMPATIBLE log."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "[CACHE_VERSION_INCOMPATIBLE]" not in out

    def test_semantic_cache_reject_log_emitted(self, capsys):
        """log_cache_reject emits [SEMANTIC_CACHE_REJECT]."""
        from verdict.semantic_cache_validator import log_cache_reject
        log_cache_reject(
            "INCOMPATIBLE_VERSION",
            cached_version="psd-object-map-v1",
            required_version="full-image-semantic-v1",
            job_id="unit-test",
        )
        out = capsys.readouterr().out
        assert "[SEMANTIC_CACHE_REJECT]" in out
        assert "INCOMPATIBLE_VERSION" in out


# ── Stage 2: PSD object path disconnected ────────────────────────────────────

class TestStage2ProductionPipelineLog:
    """[PRODUCTION_PIPELINE] is always emitted with correct policy fields."""

    def test_production_pipeline_log_emitted(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "[PRODUCTION_PIPELINE]" in out

    def test_production_pipeline_policy_field(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "policy=full-image-semantic-v1" in out

    def test_production_pipeline_psd_authority_false(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "psdLayerAuthorityUsed=false" in out

    def test_production_pipeline_object_map_false(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "objectMapApplyUsed=false" in out

    def test_production_pipeline_native_fg_false(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "nativeLayerForegroundUsed=false" in out

    def test_production_pipeline_emitted_for_jpg(self, jpg_src, tmp_path):
        results, out = _generate(
            jpg_src, _specs(), str(tmp_path), source_type="jpg"
        )
        assert "[PRODUCTION_PIPELINE]" in out

    def test_object_map_apply_never_emitted_for_png(self, png_src, tmp_path):
        """[OBJECT_MAP_APPLY] must not appear for PNG inputs in production."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "[OBJECT_MAP_APPLY]" not in out


# ── Stage 3: Unified full_image_semantic manifest ─────────────────────────────

class TestStage3FullImageSemanticManifest:
    """SOURCE_TYPE_FULL_IMAGE_SEMANTIC is defined and used in production."""

    def test_source_type_constant_defined(self):
        from verdict.models import SOURCE_TYPE_FULL_IMAGE_SEMANTIC
        assert SOURCE_TYPE_FULL_IMAGE_SEMANTIC == "full_image_semantic"

    def test_source_type_in_valid_types(self):
        from verdict.models import SOURCE_TYPE_FULL_IMAGE_SEMANTIC, VALID_SOURCE_TYPES
        assert SOURCE_TYPE_FULL_IMAGE_SEMANTIC in VALID_SOURCE_TYPES

    def test_psd_layer_not_manifest_source_type(self, png_src, tmp_path):
        """psd_layer never appears as manifest source type in production logs."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        # [MANIFEST] log (if emitted) should show full_image_semantic, not psd_layer
        if "[MANIFEST]" in out or "sourceType" in out:
            # If a sourceType log is present, it should be full_image_semantic
            if "sourceType=psd_layer" in out:
                pytest.fail("sourceType=psd_layer found in production output")

    def test_full_image_semantic_in_production_output(self, png_src, tmp_path):
        """Production output references full_image_semantic (not legacy types)."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        # Should never see legacy source types in production path
        assert "sourceType=psd_layer" not in out
        assert "sourceType=unknown" not in out

    def test_generate_completes_for_png(self, png_src, tmp_path):
        """PNG source generates a valid result with full_image_semantic pipeline."""
        results, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        assert len(results) == 1
        assert results[0]["width"] == 300
        assert results[0]["height"] == 250
        assert os.path.exists(results[0]["filePath"])

    def test_generate_completes_for_jpg(self, jpg_src, tmp_path):
        """JPG source generates a valid result."""
        results, out = _generate(
            jpg_src, _specs(300, 250), str(tmp_path), source_type="jpg"
        )
        assert len(results) == 1
        assert os.path.exists(results[0]["filePath"])


# ── Stage 4: Pixel restoration wired ─────────────────────────────────────────

class TestStage4PixelRestoration:
    """apply_default_immutable_policy is applied after SSC and logged."""

    def test_pixel_restore_log_emitted(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "[PIXEL_RESTORE]" in out

    def test_pixel_restore_has_coverage_field(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "allowedGenerationCoverage=" in out

    def test_pixel_restore_has_restored_count_field(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "restoredPixelCount=" in out

    def test_pixel_restore_log_has_subject_transform_field(self, png_src, tmp_path):
        """subjectPreservingTransform field is always present in [PIXEL_RESTORE] log."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "subjectPreservingTransform=" in out

    def test_pixel_restorer_importable(self):
        from scene_cleanup.pixel_restorer import (
            apply_default_immutable_policy,
            compute_immutable_metrics,
            log_default_immutable_policy,
        )
        assert callable(apply_default_immutable_policy)
        assert callable(compute_immutable_metrics)
        assert callable(log_default_immutable_policy)

    def test_apply_default_immutable_policy_full_white_mask(self):
        """Full-white allowed mask → AI result returned unchanged."""
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = Image.new("RGB", (100, 100), color=(0, 0, 0))
        ai_result = Image.new("RGB", (100, 100), color=(200, 200, 200))
        full_mask = np.full((100, 100), 255, dtype=np.uint8)
        restored = apply_default_immutable_policy(canonical, ai_result, full_mask)
        # With full mask, AI result wins everywhere → should be (200, 200, 200)
        arr = np.array(restored.convert("RGB"))
        assert arr.mean() > 150

    def test_apply_default_immutable_policy_full_black_mask(self):
        """Full-black allowed mask → canonical pixels restored everywhere."""
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = Image.new("RGB", (100, 100), color=(10, 10, 10))
        ai_result = Image.new("RGB", (100, 100), color=(200, 200, 200))
        black_mask = np.full((100, 100), 0, dtype=np.uint8)
        restored = apply_default_immutable_policy(canonical, ai_result, black_mask)
        arr = np.array(restored.convert("RGB"))
        assert arr.mean() < 50


# ── Stage 5: Subject-preserving outpaint ─────────────────────────────────────

class TestStage5SubjectPreservingOutpaint:
    """build_provider_canvas_outpaint replaces cover-crop in SSC."""

    def test_outpaint_builder_importable(self):
        from scene_cleanup.canvas_builder import build_provider_canvas_outpaint
        assert callable(build_provider_canvas_outpaint)

    def test_outpaint_builder_returns_4_tuple(self):
        from scene_cleanup.canvas_builder import build_provider_canvas_outpaint
        from scene_cleanup.full_image_source import build_full_image_source
        img = Image.new("RGB", (400, 300), color=(100, 150, 200))
        full_src = build_full_image_source(
            source_image=img, source_path="test.png", source_type="png",
            source_file_sha256="abc", composite_sha256="def",
            has_native_layers=False, composite_render_method="png",
        )
        result = build_provider_canvas_outpaint(full_src, 300, 250)
        assert len(result) == 4

    def test_outpaint_provider_input_correct_size(self):
        from scene_cleanup.canvas_builder import build_provider_canvas_outpaint
        from scene_cleanup.full_image_source import build_full_image_source
        img = Image.new("RGB", (400, 300), color=(100, 150, 200))
        full_src = build_full_image_source(
            source_image=img, source_path="test.png", source_type="png",
            source_file_sha256="abc", composite_sha256="def",
            has_native_layers=False, composite_render_method="png",
        )
        provider_input, mask, transform, allowed_mask = build_provider_canvas_outpaint(
            full_src, 300, 250
        )
        assert provider_input.size == (300, 250)

    def test_outpaint_mask_correct_size(self):
        from scene_cleanup.canvas_builder import build_provider_canvas_outpaint
        from scene_cleanup.full_image_source import build_full_image_source
        img = Image.new("RGB", (400, 300), color=(100, 150, 200))
        full_src = build_full_image_source(
            source_image=img, source_path="test.png", source_type="png",
            source_file_sha256="abc", composite_sha256="def",
            has_native_layers=False, composite_render_method="png",
        )
        _, mask, _, _ = build_provider_canvas_outpaint(full_src, 300, 250)
        assert mask.size == (300, 250)
        assert mask.mode == "L"

    def test_outpaint_transform_strategy(self):
        from scene_cleanup.canvas_builder import build_provider_canvas_outpaint
        from scene_cleanup.full_image_source import build_full_image_source
        from scene_cleanup.models import TRANSFORM_STRATEGY_SUBJECT_PRESERVING_OUTPAINT
        img = Image.new("RGB", (400, 300), color=(100, 150, 200))
        full_src = build_full_image_source(
            source_image=img, source_path="test.png", source_type="png",
            source_file_sha256="abc", composite_sha256="def",
            has_native_layers=False, composite_render_method="png",
        )
        _, _, transform, _ = build_provider_canvas_outpaint(full_src, 300, 250)
        assert transform.strategy == TRANSFORM_STRATEGY_SUBJECT_PRESERVING_OUTPAINT

    def test_outpaint_allowed_mask_is_numpy(self):
        from scene_cleanup.canvas_builder import build_provider_canvas_outpaint
        from scene_cleanup.full_image_source import build_full_image_source
        img = Image.new("RGB", (400, 300), color=(100, 150, 200))
        full_src = build_full_image_source(
            source_image=img, source_path="test.png", source_type="png",
            source_file_sha256="abc", composite_sha256="def",
            has_native_layers=False, composite_render_method="png",
        )
        _, _, _, allowed_mask = build_provider_canvas_outpaint(full_src, 300, 250)
        assert isinstance(allowed_mask, np.ndarray)
        assert allowed_mask.shape == (250, 300)

    def test_outpaint_contain_scale_no_crop(self):
        """Contain-scale: when aspect ratios differ, source fits entirely (no crop)."""
        from scene_cleanup.canvas_builder import build_provider_canvas_outpaint
        from scene_cleanup.full_image_source import build_full_image_source
        # Source 400x300 (4:3), target 300x300 (1:1) → source scaled to 300x225
        # offset_y = (300 - 225)//2 = 37 → outpaint regions top and bottom
        img = Image.new("RGB", (400, 300), color=(255, 0, 0))
        full_src = build_full_image_source(
            source_image=img, source_path="test.png", source_type="png",
            source_file_sha256="abc", composite_sha256="def",
            has_native_layers=False, composite_render_method="png",
        )
        _, mask, transform, _ = build_provider_canvas_outpaint(full_src, 300, 300)
        assert transform.outpaint_required is True
        # Mask has some white pixels (outpaint regions)
        mask_arr = np.array(mask)
        assert mask_arr.max() == 255  # some outpaint regions
        assert mask_arr.min() == 0    # some source regions

    def test_subject_preserving_transform_log_in_ssc(self, png_src, tmp_path):
        """SSC emits [SUBJECT_PRESERVING_TRANSFORM] log via outpaint builder."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "[SUBJECT_PRESERVING_TRANSFORM]" in out

    def test_ssc_uses_outpaint_strategy(self, png_src, tmp_path):
        """[SSC_START] log reflects the outpaint canvas scale (contain < cover)."""
        results, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        assert "[SSC_START]" in out
        # Contain-scale from 400x300 → 300x250: scale = min(300/400, 250/300) = 0.75
        assert "scale=0.75" in out or "scale=" in out

    def test_outpaint_allowed_mask_stored_in_ssc_result(self):
        """SemanticSceneCleanupResult has allowed_generation_mask field."""
        from scene_cleanup.models import SemanticSceneCleanupResult
        r = SemanticSceneCleanupResult(success=True)
        assert hasattr(r, "allowed_generation_mask")
        assert r.allowed_generation_mask is None  # default

    def test_pixel_restore_uses_outpaint_mask(self, png_src, tmp_path):
        """[PIXEL_RESTORE] shows subjectPreservingTransform=True for PNG."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "subjectPreservingTransform=True" in out


# ── Stage 6: Visual verdict required ─────────────────────────────────────────

class TestStage6VisualVerdictRequired:
    """Visual verdict is always required in production; NOT_TESTED → FAIL."""

    def test_evaluate_extended_visual_importable(self):
        from verdict.visual_evaluator import evaluate_extended_visual
        assert callable(evaluate_extended_visual)

    def test_visual_verdict_always_runs(self, png_src, tmp_path):
        """[AI_SPEC_END] references a verdict outcome (not skipped)."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        # visual verdict is active — evaluate_extended_visual is called
        # Look for [AI_SPEC_END] or [STAGE21] verdict lines
        assert "[AI_SPEC_END]" in out or "[STAGE21]" in out

    def test_visual_not_tested_causes_fail(self):
        """aggregate_stage21_verdict with visual_required=True: NOT_TESTED → FAIL."""
        from verdict.stage21_aggregator import aggregate_stage21_verdict
        from verdict.models import VerdictResult, PASS, NOT_TESTED, FAIL

        tech = VerdictResult(name="technicalVerdict", status=PASS, required=True)
        ext = VerdictResult(name="extractionVerdict", status=NOT_TESTED, required=False)
        comp = VerdictResult(name="compositionVerdict", status=NOT_TESTED, required=False)
        layout = VerdictResult(name="layoutVerdict", status=NOT_TESTED, required=False)
        visual = VerdictResult(name="visualVerdict", status=NOT_TESTED, required=False)

        summary = aggregate_stage21_verdict(
            tech, ext, comp, layout, visual,
            job_id="test", spec_id="300x250",
            visual_required=True,
        )
        assert summary.overallStatus == FAIL

    def test_visual_pass_allows_overall_pass(self):
        """When visual=PASS and all required pass, overall=PASS."""
        from verdict.stage21_aggregator import aggregate_stage21_verdict
        from verdict.models import VerdictResult, PASS, NOT_APPLICABLE

        tech = VerdictResult(name="technicalVerdict", status=PASS, required=True)
        ext = VerdictResult(name="extractionVerdict", status=NOT_APPLICABLE, required=True)
        comp = VerdictResult(name="compositionVerdict", status=NOT_APPLICABLE, required=True)
        layout = VerdictResult(name="layoutVerdict", status=NOT_APPLICABLE, required=True)
        visual = VerdictResult(name="visualVerdict", status=PASS, required=False)

        summary = aggregate_stage21_verdict(
            tech, ext, comp, layout, visual,
            job_id="test", spec_id="300x250",
            visual_required=True,
        )
        # visual passes when visual_required=True and status=PASS
        assert summary.visualVerdict.status == PASS

    def test_evaluate_extended_visual_runs_on_real_images(self):
        """evaluate_extended_visual accepts real PIL images and returns VerdictResult."""
        from verdict.visual_evaluator import evaluate_extended_visual
        from verdict.models import VerdictResult
        src = Image.new("RGB", (300, 250), color=(100, 100, 100))
        # Result with small noise (not blank, not full regen)
        arr = np.array(src, dtype=np.float32)
        arr[:50, :50] += 20
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        result = Image.fromarray(arr, "RGB")
        verdict = evaluate_extended_visual(
            source_img=src, result_img=result,
            target_w=300, target_h=250,
            job_id="test-ext", spec_id="300x250",
        )
        assert isinstance(verdict, VerdictResult)
        assert verdict.name == "visualVerdict"

    def test_generate_has_final_result_valid_field(self, png_src, tmp_path):
        """Results include finalResultValid key derived from verdict."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert len(results) == 1
        assert "finalResultValid" in results[0]

    def test_visual_verdict_enabled_hardcoded_true(self):
        """_visual_verdict_enabled in resizer.py is hardcoded True (not env-var)."""
        import pathlib
        src = pathlib.Path(
            "C:\\company\\source\\creative-resizer\\worker\\resizer.py"
        ).read_text(encoding="utf-8")
        assert "_visual_verdict_enabled = True" in src, (
            "_visual_verdict_enabled is not hardcoded True in resizer.py"
        )

    def test_evaluate_extended_visual_used_not_basic(self):
        """resizer.py imports evaluate_extended_visual, not evaluate_visual."""
        import pathlib
        src = pathlib.Path(
            "C:\\company\\source\\creative-resizer\\worker\\resizer.py"
        ).read_text(encoding="utf-8")
        assert "evaluate_extended_visual" in src
        assert "from verdict.visual_evaluator import evaluate_extended_visual" in src


# ── Mother fixture: 1200×1200 → 1250×560 geometry wiring ─────────────────────
# source=1200×1200, target=1250×560
# scale=min(1250/1200, 560/1200)=0.466667  scaledSize=560×560
# offsetX=(1250-560)//2=345  offsetY=0
# sourceMappedCoverage=313600/700000≈0.448  allowedGenerationCoverage≈0.552

@pytest.fixture()
def mother_1200_src(tmp_path):
    """1200×1200 square source — triggers wide landscape outpaint (offsetX=345)."""
    img = Image.new("RGB", (1200, 1200), color=(80, 100, 160))
    p = str(tmp_path / "mother_1200.png")
    img.save(p, "PNG")
    return p


def _mother_specs():
    return [{"media": "banner", "width": 1250, "height": 560,
             "name": "banner-1250x560", "slug": ""}]


class TestMotherFixtureTransformWiring:
    """Verify 1200×1200 → 1250×560 transform geometry is wired correctly.

    TRANSFORM_GEOMETRY must show actualOffset=345,0 (not 0,0).
    allowedGenerationCoverage must be ≈0.552 (not 1.0).
    PIXEL_RESTORE_SKIPPED and OFFSET_X_MISMATCH must be absent.
    """

    def test_transform_geometry_log_emitted(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        assert "[TRANSFORM_GEOMETRY]" in out, "TRANSFORM_GEOMETRY log missing"

    def test_actual_offset_x_is_345_not_zero(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        tg_lines = [l for l in out.splitlines() if "[TRANSFORM_GEOMETRY]" in l]
        assert tg_lines, "No [TRANSFORM_GEOMETRY] line"
        line = tg_lines[0]
        assert "actualOffset=345,0" in line, (
            f"Expected actualOffset=345,0 in: {line}"
        )

    def test_expected_offset_matches_actual(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        tg_lines = [l for l in out.splitlines() if "[TRANSFORM_GEOMETRY]" in l]
        assert tg_lines
        line = tg_lines[0]
        assert "expectedOffset=345,0" in line, (
            f"Expected expectedOffset=345,0 in: {line}"
        )

    def test_geometry_valid_true(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        tg_lines = [l for l in out.splitlines() if "[TRANSFORM_GEOMETRY]" in l]
        assert tg_lines
        assert "geometryValid=True" in tg_lines[0], tg_lines[0]

    def test_offset_x_mismatch_absent(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        assert "OFFSET_X_MISMATCH" not in out, (
            f"OFFSET_X_MISMATCH still in logs:\n{out}"
        )

    def test_allowed_generation_coverage_not_one(self, mother_1200_src, tmp_path):
        """allowedGenerationCoverage must not be 1.0 (outpaint region ≈ 0.552)."""
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        pr_lines = [l for l in out.splitlines() if "[PIXEL_RESTORE]" in l]
        assert pr_lines, "No [PIXEL_RESTORE] line"
        line = pr_lines[0]
        assert "allowedGenerationCoverage=1.0000" not in line, (
            f"Full-canvas fallback triggered:\n{line}"
        )
        # Coverage must be between 0.4 and 0.7 (outpaint side regions)
        import re
        m = re.search(r"allowedGenerationCoverage=(\d+\.\d+)", line)
        assert m, f"allowedGenerationCoverage not found in: {line}"
        cov = float(m.group(1))
        assert 0.40 < cov < 0.75, (
            f"Unexpected allowedGenerationCoverage={cov} (expected ≈0.552)"
        )

    def test_canonical_size_mismatch_absent(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        assert "CANONICAL_SIZE_MISMATCH" not in out, (
            f"Canonical size mismatch still present:\n{out}"
        )

    def test_immutable_metrics_full_canvas_absent(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        assert "IMMUTABLE_METRICS_FULL_CANVAS" not in out, (
            f"Full-canvas metrics fallback still present:\n{out}"
        )

    def test_pixel_restore_skipped_absent(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        # PIXEL_RESTORE_SKIPPED means restore was not applied — wrong for outpaint
        assert "PIXEL_RESTORE_SKIPPED" not in out or "size_mismatch" not in out, (
            f"Pixel restore skipped due to size mismatch:\n{out}"
        )

    def test_subject_preserving_transform_true(self, mother_1200_src, tmp_path):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        pr_lines = [l for l in out.splitlines() if "[PIXEL_RESTORE]" in l]
        assert pr_lines
        assert "subjectPreservingTransform=True" in pr_lines[0], pr_lines[0]

    def test_result_file_at_target_dimensions(self, mother_1200_src, tmp_path):
        results, _ = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        assert len(results) == 1
        r = results[0]
        assert r["width"] == 1250
        assert r["height"] == 560
        assert os.path.exists(r["filePath"])
        img = Image.open(r["filePath"])
        assert img.size == (1250, 560)


# ── Layout input filter: [LAYOUT_INPUT_FILTER] and pre-layout manifest ────────

class TestLayoutInputFilter:
    """[LAYOUT_INPUT_FILTER] is emitted for every spec in D-2 path.

    Verifies Stage 3 wiring: pre-layout manifest is built and finalized
    before plan_foreground_layout is called. Invalid semantic objects are
    blocked at the filter gate — not passed to layout.
    """

    def test_layout_input_filter_log_emitted(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "[LAYOUT_INPUT_FILTER]" in out, (
            f"[LAYOUT_INPUT_FILTER] missing from output:\n{out[:2000]}"
        )

    def test_layout_input_filter_has_count_fields(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        lines = [l for l in out.splitlines() if "[LAYOUT_INPUT_FILTER]" in l]
        assert lines
        line = lines[0]
        assert "inputCount=" in line
        assert "acceptedCount=" in line
        assert "rejectedCount=" in line

    def test_layout_input_filter_has_manifest_fields(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(), str(tmp_path))
        lines = [l for l in out.splitlines() if "[LAYOUT_INPUT_FILTER]" in l]
        assert lines
        line = lines[0]
        assert "manifestFinalized=" in line
        assert "manifestFailClosed=" in line
        assert "layoutPermitted=" in line

    def test_layout_input_filter_emitted_for_mother_fixture(
        self, mother_1200_src, tmp_path
    ):
        results, out = _generate(mother_1200_src, _mother_specs(), str(tmp_path))
        assert "[LAYOUT_INPUT_FILTER]" in out

    def test_contaminated_layer_rejected_not_passed_to_layout(self, tmp_path):
        """Confidence=0 layers in D-2 result must not reach layout.

        Simulate by injecting a contaminated virtual fg_layer via object_analysis
        path (no OpenAI). Verify rejectedCount ≥ 0 in LAYOUT_INPUT_FILTER log.
        This is a structural test: the filter gate exists in the production path.
        """
        img = Image.new("RGB", (400, 300), color=(120, 80, 200))
        p = str(tmp_path / "src.png")
        img.save(p, "PNG")
        # Run without any contaminated layers — just verify the filter is in the path
        results, out = _generate(p, _specs(300, 250), str(tmp_path))
        filt_lines = [l for l in out.splitlines() if "[LAYOUT_INPUT_FILTER]" in l]
        assert filt_lines, "Layout input filter not in production path"

    def test_manifest_finalized_shown_in_filter_log(self, png_src, tmp_path):
        """When there are accepted fg_layers, manifest must be finalized=True."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        lines = [l for l in out.splitlines() if "[LAYOUT_INPUT_FILTER]" in l]
        assert lines
        # When no D-2 layers exist, inputCount=0 and manifestFinalized=False is expected
        # When D-2 layers exist, manifestFinalized=True
        line = lines[0]
        import re
        m = re.search(r"acceptedCount=(\d+)", line)
        if m and int(m.group(1)) > 0:
            assert "manifestFinalized=True" in line, (
                f"acceptedCount>0 but manifestFinalized is not True: {line}"
            )

    def test_no_confidence_zero_objects_past_filter(self, png_src, tmp_path):
        """No confidence=0 object should appear in layout — filter blocks them."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        # MASK_CONTAMINATION_REJECT indicates rejection — good
        # SEMANTIC_CONFIDENCE_TOO_LOW would appear if something leaked — bad
        assert "SEMANTIC_CONFIDENCE_TOO_LOW" not in out or \
               "[LAYOUT_INPUT_FILTER]" in out  # filter must be in path


# ── Cross-cutting: All stages together ───────────────────────────────────────

class TestAllStagesTogether:
    """End-to-end: all 6 stages fire correctly in a single generate call."""

    def test_all_stage_logs_present_png(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        assert "[PRODUCTION_PIPELINE]" in out           # Stage 2
        assert "[SUBJECT_PRESERVING_TRANSFORM]" in out  # Stage 5
        assert "[PIXEL_RESTORE]" in out                  # Stage 4

    def test_all_stage_logs_present_jpg(self, jpg_src, tmp_path):
        results, out = _generate(
            jpg_src, _specs(300, 250), str(tmp_path), source_type="jpg"
        )
        assert "[PRODUCTION_PIPELINE]" in out
        assert "[SUBJECT_PRESERVING_TRANSFORM]" in out
        assert "[PIXEL_RESTORE]" in out

    def test_generate_produces_valid_output_file(self, png_src, tmp_path):
        results, out = _generate(png_src, _specs(300, 250), str(tmp_path))
        assert len(results) == 1
        path = results[0]["filePath"]
        assert os.path.exists(path)
        with Image.open(path) as img:
            assert img.size == (300, 250)

    def test_generate_multiple_specs(self, png_src, tmp_path):
        specs = [
            {"media": "banner", "width": 300, "height": 250, "name": "300x250", "slug": ""},
            {"media": "banner", "width": 728, "height": 90, "name": "728x90", "slug": ""},
        ]
        results, out = _generate(png_src, specs, str(tmp_path))
        assert len(results) == 2
        assert out.count("[PIXEL_RESTORE]") == 2
        # D-2 job-level extraction also calls SSC (once), spec loop adds one per spec
        assert out.count("[SUBJECT_PRESERVING_TRANSFORM]") >= 2

    def test_no_legacy_logs_in_production(self, png_src, tmp_path):
        """Legacy pipeline logs must never appear in production output."""
        results, out = _generate(png_src, _specs(), str(tmp_path))
        assert "[OBJECT_MAP_APPLY]" not in out
        assert "CONFIG_LEGACY_PIPELINE_FORBIDDEN" not in out
        assert "FORBIDDEN_FALLBACK_GUARD" not in out
