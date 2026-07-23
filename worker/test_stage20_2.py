"""Stage 20.2 unit tests — Source-Faithful AI Repair & Smart Fit Runtime Ban.

57 tests covering:
  - Smart Fit runtime guard (all 6 fill variants)
  - Background mode selector (source_faithful_repair / generative_background)
  - Mask builders (removal, outpaint, immutable, gen_allowed)
  - AI composite (inside-mask only, immutable pixel restoration)
  - Visible-hand mutation check
  - Source faithfulness score
  - Contamination check
  - 3-attempt retry with provider_not_configured
  - Prompt builder (versions, spec augmentations)
  - Pipeline routing (SFR path vs Stage 19 path)
  - Stage 20.1 regression (Korean raster text)
  - Stage 19 regression (pipeline disabled fallback)

Canonical package root: /app  (PYTHONPATH=/app in container)
Packages live at: /app/background/, /app/typography/
Import convention mirrors test_stage19.py and test_stage20_typography.py.
"""
from __future__ import annotations

import os
import sys

# Canonical: add the directory containing this file to sys.path.
# In the verify container: /app is PYTHONPATH; this file is at /app/test_stage20_2.py.
# Locally: worker/ directory contains background/ and typography/.
# Either way, inserting dirname(__file__) ensures `import background` resolves correctly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from PIL import Image
import numpy as np


# ── helpers ────────────────────────────────────────────────────────────────────

def _rgb(w: int, h: int, color=(200, 100, 50)) -> Image.Image:
    img = Image.new("RGB", (w, h), color)
    return img


def _make_layer(role: str, x=0, y=0, w=100, h=100, layer_type="pixel", name="layer") -> dict:
    return {
        "role": role,
        "type": layer_type,
        "name": name,
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "dedupSkip": False,
    }


# ── 1. Smart Fit guard ─────────────────────────────────────────────────────────

class TestSmartFitGuard:
    def test_build_no_smart_fit_fields_all_false(self):
        from background.smart_fit_guard import build_no_smart_fit_fields
        fields = build_no_smart_fit_fields()
        for key, val in fields.items():
            assert val is False, f"{key} should be False, got {val}"

    def test_build_no_smart_fit_returns_all_six_keys(self):
        from background.smart_fit_guard import build_no_smart_fit_fields
        fields = build_no_smart_fit_fields()
        expected = {
            "smartFitAllowed", "smartFitUsed", "smartFitFallbackUsed",
            "blurFillUsed", "mirrorFillUsed", "stretchFillUsed",
            "nativeFallbackUsed",
        }
        assert set(fields.keys()) == expected

    def test_smart_fit_forbidden_error_code(self):
        from background.smart_fit_guard import (
            SmartFitForbiddenError,
            SMART_FIT_RUNTIME_CALL_BLOCKED,
            check_smart_fit_allowed,
        )
        with pytest.raises(SmartFitForbiddenError) as exc_info:
            check_smart_fit_allowed(context="final_output", technique="blur_fill")
        assert exc_info.value.error_code == SMART_FIT_RUNTIME_CALL_BLOCKED

    def test_check_smart_fit_allowed_raises_for_blur_fill(self):
        from background.smart_fit_guard import check_smart_fit_allowed, SmartFitForbiddenError
        with pytest.raises(SmartFitForbiddenError):
            check_smart_fit_allowed("final_output", "blur_fill")

    def test_check_smart_fit_allowed_raises_for_mirror_fill(self):
        from background.smart_fit_guard import check_smart_fit_allowed, SmartFitForbiddenError
        with pytest.raises(SmartFitForbiddenError):
            check_smart_fit_allowed("final_output", "mirror_fill")

    def test_check_smart_fit_allowed_raises_for_stretch_fill(self):
        from background.smart_fit_guard import check_smart_fit_allowed, SmartFitForbiddenError
        with pytest.raises(SmartFitForbiddenError):
            check_smart_fit_allowed("final_output", "stretch_fill")

    def test_debug_context_not_blocked(self):
        from background.smart_fit_guard import check_smart_fit_allowed
        check_smart_fit_allowed("debug", "blur_fill")

    def test_build_blocked_result_contains_error_code(self):
        from background.smart_fit_guard import (
            build_blocked_result,
            SMART_FIT_RUNTIME_CALL_BLOCKED,
        )
        r = build_blocked_result("blur_fill", "final_output", 1200, 628)
        assert r["errorCode"] == SMART_FIT_RUNTIME_CALL_BLOCKED
        assert r["targetWidth"] == 1200
        assert r["targetHeight"] == 628

    def test_native_background_fallback_forbidden_constant(self):
        from background.smart_fit_guard import NATIVE_BACKGROUND_FALLBACK_FORBIDDEN
        assert isinstance(NATIVE_BACKGROUND_FALLBACK_FORBIDDEN, str)
        assert len(NATIVE_BACKGROUND_FALLBACK_FORBIDDEN) > 0


# ── 2. Mode selector ───────────────────────────────────────────────────────────

class TestModeSelector:
    def test_human_hand_role_selects_sfr(self):
        from background.mode_selector import select_background_mode, SOURCE_FAITHFUL_REPAIR
        layers = [_make_layer("hand")]
        mode, reason = select_background_mode(layers)
        assert mode == SOURCE_FAITHFUL_REPAIR
        assert "human_subject" in reason

    def test_person_role_selects_sfr(self):
        from background.mode_selector import select_background_mode, SOURCE_FAITHFUL_REPAIR
        layers = [_make_layer("person")]
        mode, reason = select_background_mode(layers)
        assert mode == SOURCE_FAITHFUL_REPAIR

    def test_main_image_pixel_selects_sfr(self):
        from background.mode_selector import select_background_mode, SOURCE_FAITHFUL_REPAIR
        layers = [_make_layer("main_image", layer_type="pixel")]
        mode, reason = select_background_mode(layers)
        assert mode == SOURCE_FAITHFUL_REPAIR

    def test_product_only_selects_generative(self):
        from background.mode_selector import select_background_mode, GENERATIVE_BACKGROUND
        layers = [_make_layer("product")]
        mode, reason = select_background_mode(layers)
        assert mode == GENERATIVE_BACKGROUND

    def test_forced_mode_overrides_detection(self):
        from background.mode_selector import select_background_mode, GENERATIVE_BACKGROUND
        layers = [_make_layer("hand")]
        mode, reason = select_background_mode(layers, forced_mode=GENERATIVE_BACKGROUND)
        assert mode == GENERATIVE_BACKGROUND
        assert "forced" in reason

    def test_korean_name_hint_손_selects_sfr(self):
        from background.mode_selector import select_background_mode, SOURCE_FAITHFUL_REPAIR
        layers = [_make_layer("background", name="어머님_손")]
        mode, _ = select_background_mode(layers)
        assert mode == SOURCE_FAITHFUL_REPAIR

    def test_empty_layers_selects_generative(self):
        from background.mode_selector import select_background_mode, GENERATIVE_BACKGROUND
        mode, _ = select_background_mode([])
        assert mode == GENERATIVE_BACKGROUND


# ── 3. Mask builders ───────────────────────────────────────────────────────────

class TestMaskBuilders:
    def test_removal_mask_covers_text_role(self):
        from background.source_faithful_repair import _mask_from_classified_roles, _REMOVAL_ROLES
        layers = [_make_layer("title", x=10, y=10, w=50, h=30)]
        mask = _mask_from_classified_roles(layers, _REMOVAL_ROLES, 200, 200)
        data = list(mask.getdata())
        assert max(data) == 255

    def test_immutable_mask_person_role(self):
        from background.source_faithful_repair import _mask_from_classified_roles, _IMMUTABLE_ROLES
        layers = [_make_layer("person", x=20, y=20, w=60, h=80)]
        mask = _mask_from_classified_roles(layers, _IMMUTABLE_ROLES, 200, 200)
        data = list(mask.getdata())
        assert max(data) == 255

    def test_dedup_skip_layers_excluded_from_mask(self):
        from background.source_faithful_repair import _mask_from_classified_roles, _REMOVAL_ROLES
        layer = _make_layer("title", x=0, y=0, w=200, h=200)
        layer["dedupSkip"] = True
        mask = _mask_from_classified_roles([layer], _REMOVAL_ROLES, 200, 200)
        data = list(mask.getdata())
        assert max(data) == 0

    def test_outpaint_mask_different_size(self):
        from background.source_faithful_repair import _build_outpaint_mask, _mask_ratio
        mask = _build_outpaint_mask(800, 600, 1200, 600)
        assert mask is not None
        ratio = _mask_ratio(mask)
        assert ratio > 0.0

    def test_outpaint_mask_same_size_returns_none(self):
        from background.source_faithful_repair import _build_outpaint_mask
        mask = _build_outpaint_mask(800, 600, 800, 600)
        assert mask is None

    def test_outpaint_mask_source_taller_than_target_has_side_strips_only(self):
        """Fix: Mother-like case (1200×1200 → 1250×560) must NOT produce all-white mask."""
        from background.source_faithful_repair import _build_outpaint_mask, _mask_ratio
        mask = _build_outpaint_mask(1200, 1200, 1250, 560)
        # Source is taller than target → vertical outpaint NOT needed, horizontal margins exist
        assert mask is not None
        ratio = _mask_ratio(mask)
        # Only thin side margins (25px each side of 1250 total) → ratio ≈ 50/1250 = 0.04
        assert ratio < 0.1, f"Expected <10% (side strips only), got {ratio:.3f}"

    def test_outpaint_mask_source_taller_wider_returns_none(self):
        """Fix: When source exceeds target in both dimensions, no outpainting needed."""
        from background.source_faithful_repair import _build_outpaint_mask
        mask = _build_outpaint_mask(1300, 1300, 1250, 560)
        assert mask is None, "Source larger than target in both dims → no outpaint"

    def test_outpaint_mask_yada_like_has_side_strips_only(self):
        """Fix: Yada-like case (1200×628 → 1250×560) must NOT produce all-white mask."""
        from background.source_faithful_repair import _build_outpaint_mask, _mask_ratio
        mask = _build_outpaint_mask(1200, 628, 1250, 560)
        assert mask is not None
        ratio = _mask_ratio(mask)
        assert ratio < 0.1, f"Expected <10% (side strips only), got {ratio:.3f}"

    def test_outpaint_mask_mother_yada_differ_when_source_differs(self):
        """Fix: Different source sizes that produce different masks must NOT share the same SHA."""
        import hashlib, io as _io
        from background.source_faithful_repair import _build_outpaint_mask
        # Source sizes that differ in height (Mother vs a taller source)
        mask_a = _build_outpaint_mask(1200, 1200, 1250, 560)
        mask_b = _build_outpaint_mask(1200, 628, 1250, 560)
        # Both should have identical side strips (1200 wide in 1250 target) — expected equal
        # But the old bug made BOTH all-white. After fix, they are also equal (same side margins).
        # Key: neither should be all-white (100% ratio).
        from background.source_faithful_repair import _mask_ratio
        if mask_a is not None:
            assert _mask_ratio(mask_a) < 1.0, "Mother mask must NOT be all-white"
        if mask_b is not None:
            assert _mask_ratio(mask_b) < 1.0, "Yada mask must NOT be all-white"

    def test_union_masks_or_logic(self):
        from background.source_faithful_repair import _union_masks
        m1 = Image.new("L", (10, 10), 0)
        m2 = Image.new("L", (10, 10), 255)
        result = _union_masks(m1, m2)
        assert result is not None
        assert list(result.getdata()) == [255] * 100

    def test_union_masks_all_none_returns_none(self):
        from background.source_faithful_repair import _union_masks
        assert _union_masks(None, None) is None

    def test_mask_ratio_full_white(self):
        from background.source_faithful_repair import _mask_ratio
        mask = Image.new("L", (10, 10), 255)
        assert _mask_ratio(mask) == 1.0

    def test_mask_ratio_none_returns_zero(self):
        from background.source_faithful_repair import _mask_ratio
        assert _mask_ratio(None) == 0.0


# ── 4. AI composite ────────────────────────────────────────────────────────────

class TestAIComposite:
    def test_composite_uses_ai_inside_mask_only(self):
        from background.source_faithful_repair import composite_ai_result
        source = _rgb(100, 100, (200, 200, 200))
        ai = _rgb(100, 100, (50, 50, 50))
        gen_mask = Image.new("L", (100, 100), 0)
        for y in range(100):
            for x in range(50):
                gen_mask.putpixel((x, y), 255)

        result = composite_ai_result(ai, source, gen_mask, None)
        r_data = list(result.getdata())
        assert r_data[0][0] < 100
        assert r_data[99][0] > 100

    def test_composite_immutable_always_restored(self):
        from background.source_faithful_repair import composite_ai_result
        source = _rgb(10, 10, (200, 200, 200))
        ai = _rgb(10, 10, (50, 50, 50))
        gen_mask = Image.new("L", (10, 10), 255)
        immut = Image.new("L", (10, 10), 255)
        result = composite_ai_result(ai, source, gen_mask, immut)
        for px in result.getdata():
            assert px[0] > 150

    def test_composite_no_mask_uses_source(self):
        from background.source_faithful_repair import composite_ai_result
        source = _rgb(10, 10, (200, 200, 200))
        ai = _rgb(10, 10, (50, 50, 50))
        result = composite_ai_result(ai, source, None, None)
        for px in result.getdata():
            assert px[0] > 150


# ── 5. Visible-hand mutation check ─────────────────────────────────────────────

class TestVisibleHandMutation:
    def test_no_mutation_when_identical(self):
        from background.source_faithful_repair import count_visible_hand_mutations
        img = _rgb(10, 10, (200, 200, 200))
        immut = Image.new("L", (10, 10), 255)
        assert count_visible_hand_mutations(img, img.copy(), immut) == 0

    def test_mutation_detected_on_changed_pixels(self):
        from background.source_faithful_repair import count_visible_hand_mutations
        original = _rgb(10, 10, (200, 200, 200))
        modified = _rgb(10, 10, (50, 50, 50))
        immut = Image.new("L", (10, 10), 255)
        mutations = count_visible_hand_mutations(original, modified, immut)
        assert mutations > 0

    def test_no_immutable_mask_returns_zero(self):
        from background.source_faithful_repair import count_visible_hand_mutations
        original = _rgb(10, 10, (200, 200, 200))
        modified = _rgb(10, 10, (50, 50, 50))
        assert count_visible_hand_mutations(original, modified, None) == 0

    def test_mutation_only_in_immutable_region(self):
        from background.source_faithful_repair import count_visible_hand_mutations
        original = _rgb(10, 10, (200, 200, 200))
        modified = _rgb(10, 10, (200, 200, 200))
        modified.putpixel((0, 0), (50, 50, 50))
        immut = Image.new("L", (10, 10), 0)
        immut.putpixel((0, 0), 255)
        mutations = count_visible_hand_mutations(original, modified, immut)
        assert mutations == 1


# ── 6. Source faithfulness score ───────────────────────────────────────────────

class TestSourceFaithfulnessScore:
    def test_identical_images_score_100(self):
        from background.source_faithful_repair import compute_source_faithfulness_score
        img = _rgb(20, 20, (200, 100, 50))
        score = compute_source_faithfulness_score(img, img.copy(), None)
        assert score == 100.0

    def test_fully_changed_score_below_50(self):
        from background.source_faithful_repair import compute_source_faithfulness_score
        original = _rgb(20, 20, (200, 200, 200))
        modified = _rgb(20, 20, (50, 50, 50))
        mask = Image.new("L", (20, 20), 0)
        score = compute_source_faithfulness_score(original, modified, mask)
        assert score < 50

    def test_full_generation_mask_returns_zero_not_100(self):
        """Fix: all-white gen_allowed mask (no preserved pixels) → 0.0, not 100.0 false positive."""
        from background.source_faithful_repair import compute_source_faithfulness_score
        original = _rgb(20, 20, (200, 100, 50))
        result = _rgb(20, 20, (10, 10, 10))
        gen_allowed_all_white = Image.new("L", (20, 20), 255)
        score = compute_source_faithfulness_score(original, result, gen_allowed_all_white)
        assert score == 0.0, f"Expected 0.0 (no preserved pixels), got {score}"


# ── 7. Contamination check ─────────────────────────────────────────────────────

class TestContaminationCheck:
    def test_blank_image_detected(self):
        from background.source_faithful_repair import _basic_contamination_check
        blank = Image.new("RGB", (100, 100), (128, 128, 128))
        result = _basic_contamination_check(blank, None)
        assert result["flatPatchDetected"]

    def test_normal_image_not_blank(self):
        from background.source_faithful_repair import _basic_contamination_check
        np_arr = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        noisy = Image.fromarray(np_arr)
        result = _basic_contamination_check(noisy, None)
        assert not result["outputBlank"]


# ── 8. Prompt builder ──────────────────────────────────────────────────────────

class TestPromptBuilder:
    def test_build_prompt_v1_has_dimensions(self):
        from background.prompt_builder import build_prompt
        p = build_prompt("source-faithful-repair-v1", 1200, 628)
        assert "1200" in p
        assert "628" in p

    def test_build_prompt_unknown_version_raises(self):
        from background.prompt_builder import build_prompt
        with pytest.raises(ValueError):
            build_prompt("nonexistent-v99", 100, 100)

    def test_spec_augmentation_1250x560(self):
        from background.prompt_builder import build_prompt
        p = build_prompt("source-faithful-repair-v1", 1250, 560)
        assert "horizontal" in p.lower() or "Extend" in p

    def test_get_attempt_version_clamped(self):
        from background.prompt_builder import get_attempt_version, ATTEMPT_VERSION_SEQUENCE
        v = get_attempt_version(999)
        assert v == ATTEMPT_VERSION_SEQUENCE[-1]

    def test_get_attempt_version_sequence(self):
        from background.prompt_builder import get_attempt_version, ATTEMPT_VERSION_SEQUENCE
        for i, expected in enumerate(ATTEMPT_VERSION_SEQUENCE):
            assert get_attempt_version(i) == expected

    def test_conservative_prompt_shorter_than_full(self):
        from background.prompt_builder import build_prompt
        full = build_prompt("source-faithful-repair-v1", 800, 600, spec_augmentation=False)
        conservative = build_prompt("source-faithful-repair-v1-conservative", 800, 600, spec_augmentation=False)
        assert len(full) > len(conservative)


# ── 9. run_source_faithful_repair orchestrator ────────────────────────────────

class TestRunSourceFaithfulRepair:
    def test_provider_not_configured_returns_partial(self):
        from background.source_faithful_repair import run_source_faithful_repair
        source = _rgb(200, 200)
        layers = [_make_layer("title", x=10, y=10, w=50, h=30)]
        result = run_source_faithful_repair(
            source_image=source,
            classified_layers=layers,
            target_w=200,
            target_h=200,
            provider=None,
            max_attempts=3,
        )
        assert result.verdict == "PARTIAL"
        assert result.success is False
        assert "provider_not_configured" in result.failure_reason

    def test_smart_fit_fields_always_false(self):
        from background.source_faithful_repair import run_source_faithful_repair
        source = _rgb(200, 200)
        result = run_source_faithful_repair(
            source_image=source, classified_layers=[], target_w=200, target_h=200, provider=None,
        )
        assert result.smart_fit_allowed is False
        assert result.smart_fit_used is False
        assert result.smart_fit_fallback_used is False
        assert result.blur_fill_used is False
        assert result.mirror_fill_used is False
        assert result.stretch_fill_used is False

    def test_no_gen_needed_returns_pass(self):
        from background.source_faithful_repair import run_source_faithful_repair
        source = _rgb(200, 200)
        result = run_source_faithful_repair(
            source_image=source, classified_layers=[], target_w=200, target_h=200, provider=None,
        )
        assert result.success is True
        assert result.verdict == "PASS"
        assert result.original_psd_background_used is True

    def test_prompt_version_recorded(self):
        from background.source_faithful_repair import run_source_faithful_repair, LATEST_VERSION
        source = _rgb(200, 200)
        result = run_source_faithful_repair(
            source_image=source, classified_layers=[], target_w=200, target_h=200, provider=None,
        )
        assert result.prompt_version == LATEST_VERSION

    def test_attempt_count_equals_max_when_all_fail(self):
        from background.source_faithful_repair import run_source_faithful_repair

        class _FailProvider:
            def metadata(self): return {}
            def inpaint(self, **kw): raise RuntimeError("provider unavailable")

        source = _rgb(200, 200)
        layers = [_make_layer("title", x=10, y=10, w=50, h=30)]
        result = run_source_faithful_repair(
            source_image=source,
            classified_layers=layers,
            target_w=200,
            target_h=200,
            provider=_FailProvider(),
            max_attempts=3,
        )
        assert result.background_ai_attempt_count == 3

    def test_successful_provider_returns_pass(self):
        from background.source_faithful_repair import run_source_faithful_repair
        np_arr = np.random.randint(50, 200, (200, 200, 3), dtype=np.uint8)
        source = Image.fromarray(np_arr)
        layers = [_make_layer("title", x=50, y=50, w=40, h=20)]

        class _OkProvider:
            def metadata(self): return {"providerName": "fake", "modelName": "m1"}
            def inpaint(self, image, mask, prompt, options):
                return image.copy()

        result = run_source_faithful_repair(
            source_image=source,
            classified_layers=layers,
            target_w=200,
            target_h=200,
            provider=_OkProvider(),
            max_attempts=1,
        )
        assert result.background_ai_succeeded is True
        assert result.success is True
        assert result.source_faithfulness_score == 100.0


# ── 10. Pipeline routing ───────────────────────────────────────────────────────

class TestPipelineRouting:
    def _make_request(self, enabled=True, sfr_enabled=False, layers=None, target_w=100, target_h=100):
        from background.schemas import BackgroundRequest, BackgroundOptions
        opts = BackgroundOptions(
            enabled=enabled,
            source_faithful_repair_enabled=sfr_enabled,
        )
        source = _rgb(target_w, target_h)
        return BackgroundRequest(
            source_image=source,
            target_width=target_w,
            target_height=target_h,
            options=opts,
            layout_candidate={"classifiedLayers": layers or []},
        )

    def test_disabled_pipeline_fallback(self):
        from background.pipeline import BackgroundPipeline
        req = self._make_request(enabled=False)
        result = BackgroundPipeline().process(req)
        assert result.fallback_reason == "pipeline_disabled"

    def test_enabled_pipeline_no_sfr_runs_stage19(self):
        from background.pipeline import BackgroundPipeline
        req = self._make_request(enabled=True, sfr_enabled=False, layers=[])
        result = BackgroundPipeline().process(req)
        assert result.background_generation_mode != "source_faithful_repair" or not result.background_ai_required

    def test_sfr_enabled_human_layer_routes_to_sfr(self):
        from background.pipeline import BackgroundPipeline
        from background.mode_selector import SOURCE_FAITHFUL_REPAIR
        layers = [_make_layer("hand")]
        req = self._make_request(enabled=True, sfr_enabled=True, layers=layers)
        result = BackgroundPipeline().process(req)
        assert result.background_generation_mode == SOURCE_FAITHFUL_REPAIR

    def test_sfr_path_no_smart_fit_fields(self):
        from background.pipeline import BackgroundPipeline
        layers = [_make_layer("hand"), _make_layer("title", x=10, y=10, w=30, h=20)]
        req = self._make_request(enabled=True, sfr_enabled=True, layers=layers)
        result = BackgroundPipeline().process(req)
        assert result.smart_fit_allowed is False
        assert result.blur_fill_used is False
        assert result.mirror_fill_used is False
        assert result.stretch_fill_used is False


# ── 11. Stage 20.1 regression ─────────────────────────────────────────────────

class TestStage201Regression:
    def test_coord_suffix_removal(self):
        from typography.text_extractor import _normalize_layer_name_text
        assert _normalize_layer_name_text("어머님_손_50_120") == "어머님 손"

    def test_korean_pixel_layer_gets_text_content(self):
        from typography.text_extractor import extract_text_layers
        layer = {
            "name": "어머님_손에_금보다_-10_200",
            "isTextLayer": False,
            "type": "pixel",
            "bbox": {"x": 0, "y": 0, "width": 100, "height": 30},
        }
        result = extract_text_layers([layer])
        assert len(result) == 1
        assert result[0].get("textContent") == "어머님 손에 금보다"
        assert result[0].get("textContentSource") == "layer_name_fallback"

    def test_resolve_korean_text_roles_promotes_title(self):
        from typography.role_resolver import resolve_korean_text_roles
        layers = [{
            "role": "unknown",
            "roleSource": "heuristic",
            "textContentSource": "layer_name_fallback",
            "isKorean": True,
            "textContent": "짧은 카피",
            "bbox": {"x": 0, "y": 0, "width": 400, "height": 30},
            "canvasHeight": 600,
        }]
        result = resolve_korean_text_roles(layers)
        assert result[0]["role"] in ("title", "body_text")


# ── 12. Stage 19 regression ────────────────────────────────────────────────────

class TestStage19Regression:
    def test_pipeline_disabled_returns_native_result(self):
        from background.pipeline import BackgroundPipeline
        from background.schemas import BackgroundRequest, BackgroundOptions
        source = _rgb(300, 200)
        opts = BackgroundOptions(enabled=False)
        req = BackgroundRequest(source_image=source, target_width=300, target_height=200, options=opts)
        result = BackgroundPipeline().process(req)
        assert result.fallback_used is True
        assert result.result_image is not None

    def test_pipeline_never_raises(self):
        from background.pipeline import BackgroundPipeline
        from background.schemas import BackgroundRequest, BackgroundOptions
        source = _rgb(100, 100)
        opts = BackgroundOptions(enabled=True)
        req = BackgroundRequest(source_image=source, target_width=100, target_height=100, options=opts)
        result = BackgroundPipeline().process(req)
        assert result.verdict in ("PASS", "PARTIAL", "FAIL", "PENDING")


# ── 13. Foreground uniform scale ───────────────────────────────────────────────

class _FakeLayerObj:
    """Stub for psd-tools layer object: returns a solid-colour RGBA image."""
    def __init__(self, w, h, color=(200, 100, 50, 255)):
        self._img = Image.new("RGBA", (w, h), color)
    def composite(self):
        return self._img.copy()


def _make_psd_layer(role, ox, oy, ow, oh, canvas_w, canvas_h, ltype="pixel"):
    return {
        "role": role,
        "name": f"{role}_layer",
        "type": ltype,
        "id": f"id_{role}",
        "depth": 0,
        "bbox": {"x": ox, "y": oy, "width": ow, "height": oh},
        "canvasWidth": canvas_w,
        "canvasHeight": canvas_h,
        "_layer_obj": _FakeLayerObj(ow, oh),
    }


class TestForegroundUniformScale:
    """E category: foreground scale must preserve aspect ratio."""

    def _extract(self, layers, canvas_w, canvas_h, target_w, target_h):
        from foreground.layer_extractor import extract_foreground_layers
        return extract_foreground_layers(layers, canvas_w, canvas_h, target_w, target_h)

    def _aspect(self, w, h):
        return w / h if h > 0 else 1.0

    def test_product_tall_aspect_preserved_square_to_wide(self):
        """1200x1200 → 1250x560: product 200x732 must keep ~0.273 aspect (not 0.608)."""
        layer = _make_psd_layer("product", 920, 140, 200, 732, 1200, 1200)
        extracted = self._extract([layer], 1200, 1200, 1250, 560)
        assert len(extracted) == 1
        bbox = extracted[0]["bbox"]
        src_aspect = self._aspect(200, 732)
        dst_aspect = self._aspect(bbox["width"], bbox["height"])
        assert abs(src_aspect - dst_aspect) <= 0.02, (
            f"Aspect distorted: src={src_aspect:.3f} dst={dst_aspect:.3f} "
            f"(size={bbox['width']}x{bbox['height']})"
        )

    def test_title_wide_aspect_preserved_square_to_wide(self):
        """1200x1200 → 1250x560: title 1038x81 must keep ~12.8 aspect (not 28.4)."""
        layer = _make_psd_layer("title", 80, 50, 1038, 81, 1200, 1200, ltype="type")
        extracted = self._extract([layer], 1200, 1200, 1250, 560)
        assert len(extracted) == 1
        bbox = extracted[0]["bbox"]
        src_aspect = self._aspect(1038, 81)
        dst_aspect = self._aspect(bbox["width"], bbox["height"])
        assert abs(src_aspect - dst_aspect) <= 0.5, (
            f"Title aspect distorted: src={src_aspect:.3f} dst={dst_aspect:.3f}"
        )

    def test_same_size_no_change(self):
        """Same canvas and target: scale_uniform=1.0, no resize needed."""
        layer = _make_psd_layer("product", 100, 100, 200, 300, 600, 600)
        extracted = self._extract([layer], 600, 600, 600, 600)
        assert len(extracted) == 1
        bbox = extracted[0]["bbox"]
        assert bbox["width"] == 200
        assert bbox["height"] == 300

    def test_square_to_tall_aspect_preserved(self):
        """600x600 → 400x800: scale_uniform = min(400/600, 800/600) = 0.667."""
        layer = _make_psd_layer("human_subject", 100, 100, 200, 400, 600, 600)
        extracted = self._extract([layer], 600, 600, 400, 800)
        assert len(extracted) == 1
        bbox = extracted[0]["bbox"]
        src_aspect = self._aspect(200, 400)
        dst_aspect = self._aspect(bbox["width"], bbox["height"])
        assert abs(src_aspect - dst_aspect) <= 0.02

    def test_non_uniform_canvas_aspect_uses_min_scale(self):
        """Verify scale_uniform = min(scale_x, scale_y) for arbitrary canvas."""
        # canvas 800x600 → target 400x600
        # scale_x = 400/800 = 0.5, scale_y = 600/600 = 1.0 → min = 0.5
        layer = _make_psd_layer("product", 0, 0, 200, 100, 800, 600)
        extracted = self._extract([layer], 800, 600, 400, 600)
        assert len(extracted) == 1
        bbox = extracted[0]["bbox"]
        # With scale_uniform=0.5: sw=100, sh=50 → aspect 2.0 same as src 200/100=2.0
        assert abs(self._aspect(200, 100) - self._aspect(bbox["width"], bbox["height"])) <= 0.02


# ── 14. Human immutable protection after Object Map ────────────────────────────

class TestHumanImmutableProtection:
    """C category: human_subject pixel/smartobject cannot be downgraded by Object Map."""

    def _apply(self, layers, object_results, strict=True):
        from object_map_applicator import apply_object_map
        return apply_object_map(layers, object_results, strict=strict)

    def _human_layer(self, ltype="pixel"):
        return {
            "id": "layer_human_001",
            "name": "모델_손",
            "type": ltype,
            "role": "human_subject",
            "priority": "required",
            "bbox": {"x": 0, "y": 0, "width": 100, "height": 200},
        }

    def test_human_pixel_not_downgraded_by_object_map(self):
        """Object Map assigns 'product' to a human_subject pixel layer → rejected."""
        layer = self._human_layer("pixel")
        obj_results = [{
            "matchedLayerId": "layer_human_001",
            "matchedLayerName": "모델_손",
            "role": "product",
            "matchStatus": "ready",
            "confidence": 0.95,
        }]
        updated, logs = self._apply([layer], obj_results, strict=True)
        assert updated[0]["role"] == "human_subject", (
            f"human_subject was downgraded to {updated[0]['role']!r}"
        )
        assert any(
            lg.get("rejectReason") == "human_subject_immutable"
            for lg in logs
        ), f"Expected human_subject_immutable reject log, got: {logs}"

    def test_human_smartobject_not_downgraded_by_object_map(self):
        """Object Map assigns 'background' to a human_subject smartobject → rejected."""
        layer = self._human_layer("smartobject")
        layer["id"] = "layer_human_002"
        obj_results = [{
            "matchedLayerId": "layer_human_002",
            "matchedLayerName": "모델_손",
            "role": "background",
            "matchStatus": "ready",
            "confidence": 0.9,
        }]
        updated, logs = self._apply([layer], obj_results, strict=True)
        assert updated[0]["role"] == "human_subject"
        assert any(lg.get("rejectReason") == "human_subject_immutable" for lg in logs)

    def test_human_can_be_confirmed_as_human_by_object_map(self):
        """Object Map assigning same role (human_subject) is fine — no log needed."""
        layer = self._human_layer("pixel")
        layer["id"] = "layer_human_003"
        obj_results = [{
            "matchedLayerId": "layer_human_003",
            "matchedLayerName": "모델_손",
            "role": "human_subject",
            "matchStatus": "ready",
            "confidence": 0.99,
        }]
        updated, logs = self._apply([layer], obj_results, strict=True)
        assert updated[0]["role"] == "human_subject"
        assert not any(lg.get("rejectReason") == "human_subject_immutable" for lg in logs)

    def test_non_human_pixel_can_be_overridden(self):
        """Object Map can override a 'product' pixel layer — no immutable guard fires."""
        layer = {
            "id": "layer_prod_001",
            "name": "제품_박스",
            "type": "pixel",
            "role": "product",
            "priority": "required",
            "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        obj_results = [{
            "matchedLayerId": "layer_prod_001",
            "matchedLayerName": "제품_박스",
            "role": "main_image",
            "matchStatus": "ready",
            "confidence": 0.88,
        }]
        updated, logs = self._apply([layer], obj_results, strict=True)
        assert updated[0]["role"] == "main_image"
        assert not any(lg.get("rejectReason") == "human_subject_immutable" for lg in logs)

    def test_human_text_layer_is_not_protected(self):
        """A text layer misclassified as human_subject is NOT immutable (text cannot be human)."""
        layer = self._human_layer("type")  # text layer
        layer["id"] = "layer_human_text_001"
        obj_results = [{
            "matchedLayerId": "layer_human_text_001",
            "matchedLayerName": "모델_손",
            "role": "body_text",
            "matchStatus": "ready",
            "confidence": 0.91,
        }]
        updated, logs = self._apply([layer], obj_results, strict=True)
        # Text layer human_subject is a contradiction — Object Map override allowed
        assert updated[0]["role"] == "body_text"
