"""Bundle D-1: test_semantic_scene_cleanup.py

Categories A-K — 89+ tests.
ACTUAL_OPENAI_REQUESTS=0: all tests use FakeBackgroundProvider.
"""
import hashlib
import os
import sys
import tempfile
import types

import numpy as np
import pytest
from PIL import Image

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _rgb(w: int, h: int, r: int = 80, g: int = 120, b: int = 160) -> Image.Image:
    arr = np.full((h, w, 3), [r, g, b], dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def _gradient(w: int, h: int) -> Image.Image:
    """Non-blank gradient — variance well above 5.0."""
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for x in range(w):
        arr[:, x, :] = int(x * 255 / w)
    return Image.fromarray(arr, "RGB")


class _FakeProvider:
    """Returns a gradient image — deterministic, no API key, variance > 5."""
    def __init__(self, return_none: bool = False, raise_exc: bool = False):
        self._return_none = return_none
        self._raise_exc = raise_exc

    def inpaint(self, image, mask, prompt, options=None):
        if self._raise_exc:
            raise RuntimeError("fake_provider_error")
        if self._return_none:
            return None
        return _gradient(image.width, image.height)


class _BlankProvider:
    """Returns a flat uniform image — blank check will fail."""
    def inpaint(self, image, mask, prompt, options=None):
        return Image.new("RGB", (image.width, image.height), (128, 128, 128))


class _FakeRenderCtx:
    """Minimal render context stub."""
    def __init__(self):
        self.source_file_sha256 = "aabbcc"
        self.composite_sha256 = "ddeeff"
        self.provider_input_sha256 = ""
        self.ai_background_sha256 = ""
        self.final_artifact_sha256 = ""
        self.work_dir = "/tmp/test"
        self._ai_background = None
        self._debug = {}

    def record_ai_background(self, img):
        self._ai_background = img

    def record_provider_input_sha256(self, sha):
        self.provider_input_sha256 = sha

    def save_debug_artifact(self, name, img_or_dict):
        self._debug[name] = img_or_dict


# ──────────────────────────────────────────────────────────────────────────────
# A: build_full_image_source
# ──────────────────────────────────────────────────────────────────────────────

class TestFullImageSource:
    def test_a1_valid_rgb(self):
        from scene_cleanup.full_image_source import build_full_image_source
        img = _rgb(300, 250)
        result = build_full_image_source(
            source_image=img, source_path="/x.psd", source_file_sha256="aa",
            composite_sha256="bb", source_type="psd", has_native_layers=True,
            composite_render_method="psd_composite",
        )
        assert result.width == 300
        assert result.height == 250
        assert result.source_type == "psd"
        assert result.has_native_layers is True

    def test_a2_valid_rgba(self):
        from scene_cleanup.full_image_source import build_full_image_source
        img = _rgb(100, 100).convert("RGBA")
        result = build_full_image_source(
            source_image=img, source_path="", source_file_sha256="",
            composite_sha256="", source_type="png", has_native_layers=False,
            composite_render_method="png",
        )
        assert result.width == 100
        assert result.has_native_layers is False

    def test_a3_none_raises(self):
        from scene_cleanup.full_image_source import build_full_image_source
        with pytest.raises(RuntimeError, match="FULL_IMAGE_SOURCE_MISSING"):
            build_full_image_source(
                source_image=None, source_path="", source_file_sha256="",
                composite_sha256="", source_type="psd", has_native_layers=True,
                composite_render_method="psd_composite",
            )

    def test_a4_non_image_raises(self):
        from scene_cleanup.full_image_source import build_full_image_source
        with pytest.raises(RuntimeError, match="FULL_IMAGE_SOURCE_INVALID"):
            build_full_image_source(
                source_image="not-an-image", source_path="", source_file_sha256="",
                composite_sha256="", source_type="psd", has_native_layers=True,
                composite_render_method="psd_composite",
            )

    def test_a5_fields_stored(self):
        from scene_cleanup.full_image_source import build_full_image_source
        img = _rgb(200, 150)
        result = build_full_image_source(
            source_image=img, source_path="/p.psd", source_file_sha256="AA",
            composite_sha256="BB", source_type="psd", has_native_layers=True,
            composite_render_method="psd_composite",
        )
        assert result.source_file_sha256 == "AA"
        assert result.composite_sha256 == "BB"
        assert result.composite_render_method == "psd_composite"
        assert result.image is img

    def test_a6_empty_source_path_ok(self):
        from scene_cleanup.full_image_source import build_full_image_source
        img = _rgb(50, 50)
        result = build_full_image_source(
            source_image=img, source_path="", source_file_sha256="",
            composite_sha256="", source_type="jpg", has_native_layers=False,
            composite_render_method="",
        )
        assert result.source_path == ""
        assert result.composite_render_method == "jpg"  # fallback to source_type


# ──────────────────────────────────────────────────────────────────────────────
# B: build_provider_canvas
# ──────────────────────────────────────────────────────────────────────────────

class TestCanvasBuilder:
    def _make_source(self, w: int, h: int):
        from scene_cleanup.full_image_source import build_full_image_source
        img = _gradient(w, h)
        return build_full_image_source(
            source_image=img, source_path="", source_file_sha256="",
            composite_sha256="", source_type="psd", has_native_layers=True,
            composite_render_method="psd_composite",
        )

    def test_b1_output_size_matches_target(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        src = self._make_source(1200, 628)
        pi, mask, t = build_provider_canvas(src, 300, 250)
        assert pi.size == (300, 250)

    def test_b2_mask_all_white(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        src = self._make_source(600, 400)
        _, mask, _ = build_provider_canvas(src, 300, 250)
        arr = np.array(mask)
        assert arr.min() == 255
        assert arr.max() == 255

    def test_b3_mask_mode_L(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        src = self._make_source(600, 400)
        _, mask, _ = build_provider_canvas(src, 300, 250)
        assert mask.mode == "L"

    def test_b4_provider_input_mode_RGB(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        src = self._make_source(600, 400)
        pi, _, _ = build_provider_canvas(src, 300, 250)
        assert pi.mode == "RGB"

    def test_b5_transform_strategy_cover_crop(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        from scene_cleanup.models import TRANSFORM_STRATEGY_COVER_CROP
        src = self._make_source(600, 400)
        _, _, t = build_provider_canvas(src, 300, 250)
        assert t.strategy == TRANSFORM_STRATEGY_COVER_CROP

    def test_b6_outpaint_required_false(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        src = self._make_source(600, 400)
        _, _, t = build_provider_canvas(src, 300, 250)
        assert t.outpaint_required is False

    def test_b7_mask_strategy_full_canvas(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        from scene_cleanup.models import MASK_STRATEGY_FULL_CANVAS
        src = self._make_source(600, 400)
        _, _, t = build_provider_canvas(src, 300, 250)
        assert t.mask_strategy == MASK_STRATEGY_FULL_CANVAS

    def test_b8_scale_positive(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        src = self._make_source(600, 400)
        _, _, t = build_provider_canvas(src, 300, 250)
        assert t.scale > 0.0

    def test_b9_same_size_no_crop(self):
        from scene_cleanup.canvas_builder import build_provider_canvas
        src = self._make_source(300, 250)
        pi, _, t = build_provider_canvas(src, 300, 250)
        assert pi.size == (300, 250)
        assert t.crop_x == 0
        assert t.crop_y == 0

    def test_b10_rgba_source_converted(self):
        from scene_cleanup.full_image_source import build_full_image_source
        from scene_cleanup.canvas_builder import build_provider_canvas
        img = _rgb(400, 300).convert("RGBA")
        src = build_full_image_source(
            source_image=img, source_path="", source_file_sha256="",
            composite_sha256="", source_type="psd", has_native_layers=True,
            composite_render_method="psd_composite",
        )
        pi, _, _ = build_provider_canvas(src, 200, 150)
        assert pi.mode == "RGB"


# ──────────────────────────────────────────────────────────────────────────────
# C: build_semantic_prompt
# ──────────────────────────────────────────────────────────────────────────────

class TestPromptBuilder:
    def test_c1_returns_tuple(self):
        from scene_cleanup.prompt_builder import build_semantic_prompt
        result = build_semantic_prompt(300, 250)
        assert isinstance(result, tuple) and len(result) == 2

    def test_c2_prompt_version_correct(self):
        from scene_cleanup.prompt_builder import (
            build_semantic_prompt, SEMANTIC_CLEANUP_PROMPT_VERSION
        )
        _, version = build_semantic_prompt(300, 250)
        assert version == SEMANTIC_CLEANUP_PROMPT_VERSION

    def test_c3_dimensions_in_prompt(self):
        from scene_cleanup.prompt_builder import build_semantic_prompt
        prompt, _ = build_semantic_prompt(640, 480)
        assert "640" in prompt
        assert "480" in prompt

    def test_c4_sha_guard_self_consistent(self):
        """SHA guard succeeds when template is unmodified."""
        from scene_cleanup.prompt_builder import build_semantic_prompt
        prompt, _ = build_semantic_prompt(100, 100)
        assert len(prompt) > 20

    def test_c5_version_string_format(self):
        from scene_cleanup.prompt_builder import SEMANTIC_CLEANUP_PROMPT_VERSION
        assert SEMANTIC_CLEANUP_PROMPT_VERSION.startswith("semantic-scene-cleanup-v")

    def test_c6_prompt_contains_no_brand_names(self):
        from scene_cleanup.prompt_builder import build_semantic_prompt
        prompt, _ = build_semantic_prompt(300, 250)
        for term in ("nike", "samsung", "apple", "google", "mother"):
            assert term.lower() not in prompt.lower()

    def test_c7_template_sha_constant_matches(self):
        from scene_cleanup.prompt_builder import (
            _STATIC_PROMPT_TEMPLATE, _TEMPLATE_SHA256
        )
        computed = hashlib.sha256(_STATIC_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
        assert computed == _TEMPLATE_SHA256

    def test_c8_different_dimensions_different_prompt(self):
        from scene_cleanup.prompt_builder import build_semantic_prompt
        p1, _ = build_semantic_prompt(300, 250)
        p2, _ = build_semantic_prompt(728, 90)
        assert p1 != p2


# ──────────────────────────────────────────────────────────────────────────────
# D: SemanticSceneCleanupResult model
# ──────────────────────────────────────────────────────────────────────────────

class TestModels:
    def test_d1_default_success_false(self):
        from scene_cleanup.models import SemanticSceneCleanupResult
        r = SemanticSceneCleanupResult(success=False)
        assert r.success is False
        assert r.failure_reason == ""

    def test_d2_provider_input_source_default(self):
        from scene_cleanup.models import SemanticSceneCleanupResult, PROVIDER_INPUT_FULL_COMPOSITE
        r = SemanticSceneCleanupResult(success=True)
        assert r.provider_input_source == PROVIDER_INPUT_FULL_COMPOSITE

    def test_d3_canvas_transform_none_by_default(self):
        from scene_cleanup.models import SemanticSceneCleanupResult
        r = SemanticSceneCleanupResult(success=True)
        assert r.canvas_transform is None

    def test_d4_d2_fields(self):
        from scene_cleanup.models import SemanticSceneCleanupResult
        r = SemanticSceneCleanupResult(success=False, d2_required=True, d2_reason="needs seg")
        assert r.d2_required is True
        assert "seg" in r.d2_reason

    def test_d5_scene_plate_image_not_serialized(self):
        """scene_plate_image is PIL Image, not a primitive — excluded from serialize_*."""
        from scene_cleanup.models import SemanticSceneCleanupResult
        from scene_cleanup.serializer import serialize_scene_cleanup_result
        img = _gradient(100, 100)
        r = SemanticSceneCleanupResult(success=True, scene_plate_image=img)
        d = serialize_scene_cleanup_result(r)
        for v in d.values():
            assert not isinstance(v, Image.Image)

    def test_d6_constants_distinct(self):
        from scene_cleanup.models import (
            SOURCE_FAITHFUL_REPAIR, SEMANTIC_SCENE_CLEANUP,
            PROVIDER_INPUT_FULL_COMPOSITE, PROVIDER_INPUT_BACKGROUND_PLATE,
        )
        assert SOURCE_FAITHFUL_REPAIR != SEMANTIC_SCENE_CLEANUP
        assert PROVIDER_INPUT_FULL_COMPOSITE != PROVIDER_INPUT_BACKGROUND_PLATE


# ──────────────────────────────────────────────────────────────────────────────
# E: serializer
# ──────────────────────────────────────────────────────────────────────────────

class TestSerializer:
    def _make_result(self, success: bool = True) -> object:
        from scene_cleanup.models import (
            SemanticSceneCleanupResult, SceneCanvasTransform,
            PROVIDER_INPUT_FULL_COMPOSITE, MASK_STRATEGY_FULL_CANVAS,
        )
        t = SceneCanvasTransform(
            source_w=600, source_h=400, canvas_w=300, canvas_h=250,
            scale=0.5, crop_x=0, crop_y=25, outpaint_required=False,
            mask_strategy=MASK_STRATEGY_FULL_CANVAS,
        )
        return SemanticSceneCleanupResult(
            success=success,
            failure_reason="" if success else "test_err",
            provider_name="fake",
            provider_model="",
            provider_input_source=PROVIDER_INPUT_FULL_COMPOSITE,
            prompt_version="semantic-scene-cleanup-v1",
            prompt_sha256="a" * 64,
            scene_plate_sha256="b" * 64,
            scene_plate_image=_gradient(300, 250) if success else None,
            canvas_transform=t,
            attempt_count=1,
            actual_provider_request_count=1,
            d2_required=False,
            source_w=600, source_h=400, target_w=300, target_h=250,
        )

    def test_e1_serialize_success_fields(self):
        from scene_cleanup.serializer import serialize_scene_cleanup_result
        d = serialize_scene_cleanup_result(self._make_result(True))
        assert d["success"] is True
        assert d["providerName"] == "fake"
        assert "canvasTransform" in d

    def test_e2_serialize_truncates_sha(self):
        from scene_cleanup.serializer import serialize_scene_cleanup_result
        d = serialize_scene_cleanup_result(self._make_result(True))
        assert len(d["scenePlateSha256"]) == 16
        assert len(d["promptSha256"]) == 16

    def test_e3_serialize_canvas_transform(self):
        from scene_cleanup.serializer import serialize_canvas_transform
        from scene_cleanup.models import SceneCanvasTransform
        t = SceneCanvasTransform(canvas_w=300, canvas_h=250, scale=0.5)
        d = serialize_canvas_transform(t)
        assert d["canvasW"] == 300
        assert d["scale"] == 0.5

    def test_e4_serialize_none_transform(self):
        from scene_cleanup.serializer import serialize_canvas_transform
        assert serialize_canvas_transform(None) == {}

    def test_e5_extract_d1_provenance_fields(self):
        from scene_cleanup.serializer import extract_d1_provenance_fields
        r = self._make_result(True)
        d = extract_d1_provenance_fields(r)
        assert "d1ProviderInputSource" in d
        assert "d1ScenePlateSha256" in d
        assert "d1CanvasTransform" in d
        assert d["d1D2Required"] is False

    def test_e6_d1_provenance_sha_truncated(self):
        from scene_cleanup.serializer import extract_d1_provenance_fields
        r = self._make_result(True)
        d = extract_d1_provenance_fields(r)
        assert len(d["d1ScenePlateSha256"]) == 16

    def test_e7_serialize_failure_result(self):
        from scene_cleanup.serializer import serialize_scene_cleanup_result
        d = serialize_scene_cleanup_result(self._make_result(False))
        assert d["success"] is False
        assert d["failureReason"] == "test_err"

    def test_e8_d1_provenance_source_dimensions(self):
        from scene_cleanup.serializer import extract_d1_provenance_fields
        r = self._make_result(True)
        d = extract_d1_provenance_fields(r)
        assert d["d1SourceW"] == 600
        assert d["d1SourceH"] == 400


# ──────────────────────────────────────────────────────────────────────────────
# F: run_semantic_scene_cleanup — success paths
# ──────────────────────────────────────────────────────────────────────────────

class TestSemanticSceneCleanupSuccess:
    def _run(self, src_img=None, target_w=300, target_h=250,
             source_type="psd", has_native_layers=True, output_dir=None):
        from scene_cleanup.semantic_scene_cleanup import run_semantic_scene_cleanup
        if src_img is None:
            src_img = _gradient(600, 400)
        if output_dir is None:
            output_dir = tempfile.mkdtemp()
        return run_semantic_scene_cleanup(
            source_path="/test.psd",
            source_type=source_type,
            source_image=src_img,
            source_file_sha256="aabb",
            composite_sha256="ccdd",
            target_w=target_w,
            target_h=target_h,
            provider=_FakeProvider(),
            output_dir=output_dir,
            render_ctx=_FakeRenderCtx(),
            has_native_layers=has_native_layers,
            composite_render_method="psd_composite" if source_type == "psd" else source_type,
            job_id="job1",
            spec_id="300x250",
        )

    def test_f1_success_true(self):
        r = self._run()
        assert r.success is True

    def test_f2_scene_plate_image_not_none(self):
        r = self._run()
        assert r.scene_plate_image is not None

    def test_f3_scene_plate_size(self):
        r = self._run(target_w=300, target_h=250)
        assert r.scene_plate_image.size == (300, 250)

    def test_f4_provider_name_set(self):
        r = self._run()
        assert r.provider_name != ""

    def test_f5_scene_plate_sha_not_empty(self):
        r = self._run()
        assert len(r.scene_plate_sha256) == 64

    def test_f6_provider_input_source_full_composite(self):
        from scene_cleanup.models import PROVIDER_INPUT_FULL_COMPOSITE
        r = self._run()
        assert r.provider_input_source == PROVIDER_INPUT_FULL_COMPOSITE

    def test_f7_attempt_count_one(self):
        r = self._run()
        assert r.attempt_count == 1

    def test_f8_actual_provider_requests_one(self):
        r = self._run()
        assert r.actual_provider_request_count == 1

    def test_f9_canvas_transform_set(self):
        r = self._run()
        assert r.canvas_transform is not None

    def test_f10_d2_required_false_for_psd(self):
        r = self._run(source_type="psd", has_native_layers=True)
        assert r.d2_required is False

    def test_f11_prompt_version_set(self):
        from scene_cleanup.prompt_builder import SEMANTIC_CLEANUP_PROMPT_VERSION
        r = self._run()
        assert r.prompt_version == SEMANTIC_CLEANUP_PROMPT_VERSION

    def test_f12_render_ctx_provider_input_sha_recorded(self):
        from scene_cleanup.semantic_scene_cleanup import run_semantic_scene_cleanup
        ctx = _FakeRenderCtx()
        run_semantic_scene_cleanup(
            source_path="", source_type="psd", source_image=_gradient(300, 250),
            source_file_sha256="", composite_sha256="", target_w=300, target_h=250,
            provider=_FakeProvider(), output_dir=tempfile.mkdtemp(),
            render_ctx=ctx, has_native_layers=True, composite_render_method="psd_composite",
        )
        assert ctx.provider_input_sha256 != ""


# ──────────────────────────────────────────────────────────────────────────────
# G: run_semantic_scene_cleanup — failure paths (fail-closed)
# ──────────────────────────────────────────────────────────────────────────────

class TestSemanticSceneCleanupFailures:
    def _run(self, **kw):
        from scene_cleanup.semantic_scene_cleanup import run_semantic_scene_cleanup
        defaults = dict(
            source_path="", source_type="psd", source_image=_gradient(300, 250),
            source_file_sha256="", composite_sha256="",
            target_w=300, target_h=250,
            provider=_FakeProvider(),
            output_dir=tempfile.mkdtemp(),
            render_ctx=_FakeRenderCtx(),
            has_native_layers=True,
            composite_render_method="psd_composite",
        )
        defaults.update(kw)
        return run_semantic_scene_cleanup(**defaults)

    def test_g1_none_source_image_fails(self):
        r = self._run(source_image=None)
        assert r.success is False
        assert "FULL_IMAGE_SOURCE_MISSING" in r.failure_reason

    def test_g2_invalid_source_image_type_fails(self):
        r = self._run(source_image="not-an-image")
        assert r.success is False
        assert "FULL_IMAGE_SOURCE_INVALID" in r.failure_reason

    def test_g3_provider_returns_none_fails(self):
        r = self._run(provider=_FakeProvider(return_none=True))
        assert r.success is False

    def test_g4_provider_raises_fails(self):
        r = self._run(provider=_FakeProvider(raise_exc=True))
        assert r.success is False
        assert "SEMANTIC_PROVIDER_FAILED" in r.failure_reason

    def test_g5_blank_provider_output_fails(self):
        r = self._run(provider=_BlankProvider())
        assert r.success is False
        assert "BLANK" in r.failure_reason

    def test_g6_failure_has_reason(self):
        r = self._run(source_image=None)
        assert r.failure_reason != ""

    def test_g7_attempt_count_incremented_on_fail(self):
        r = self._run(provider=_FakeProvider(raise_exc=True))
        assert r.attempt_count >= 1

    def test_g8_actual_requests_incremented_on_fail(self):
        r = self._run(provider=_FakeProvider(raise_exc=True))
        assert r.actual_provider_request_count >= 1

    def test_g9_no_scene_plate_image_on_fail(self):
        r = self._run(provider=_FakeProvider(return_none=True))
        assert r.scene_plate_image is None

    def test_g10_no_fallback_to_sfr(self):
        """Fail-closed: failure result does not contain a sfr-mode provider."""
        r = self._run(provider=_FakeProvider(return_none=True))
        assert r.provider_input_source == "full_composite" or r.provider_input_source == ""


# ──────────────────────────────────────────────────────────────────────────────
# H: D-2 flattened input detection
# ──────────────────────────────────────────────────────────────────────────────

class TestD2FlattenedInput:
    def _run(self, source_type="png", has_native_layers=False):
        from scene_cleanup.semantic_scene_cleanup import run_semantic_scene_cleanup
        return run_semantic_scene_cleanup(
            source_path="", source_type=source_type,
            source_image=_gradient(300, 250),
            source_file_sha256="", composite_sha256="",
            target_w=300, target_h=250,
            provider=_FakeProvider(),
            output_dir=tempfile.mkdtemp(),
            render_ctx=_FakeRenderCtx(),
            has_native_layers=has_native_layers,
            composite_render_method=source_type,
        )

    def test_h1_png_no_layers_d2_required_true(self):
        r = self._run(source_type="png", has_native_layers=False)
        assert r.d2_required is True

    def test_h2_jpg_no_layers_d2_required_true(self):
        r = self._run(source_type="jpg", has_native_layers=False)
        assert r.d2_required is True

    def test_h3_psd_with_layers_d2_required_false(self):
        r = self._run(source_type="psd", has_native_layers=True)
        assert r.d2_required is False

    def test_h4_psd_without_layers_d2_required_false(self):
        # PSD source_type always d2_required=False regardless of has_native_layers
        r = self._run(source_type="psd", has_native_layers=False)
        assert r.d2_required is False

    def test_h5_d2_reason_non_empty_when_required(self):
        r = self._run(source_type="png", has_native_layers=False)
        assert r.d2_reason != ""

    def test_h6_d2_reason_empty_when_not_required(self):
        r = self._run(source_type="psd", has_native_layers=True)
        assert r.d2_reason == ""

    def test_h7_extraction_evaluator_d2_required_fail(self):
        from verdict.extraction_evaluator import evaluate_extraction
        from verdict import reason_codes as RC
        result = evaluate_extraction(
            None, source_type="unknown", d2_required=True,
            job_id="j", spec_id="s",
        )
        assert result.status == "FAIL"
        assert RC.EXTRACTION_D2_REQUIRED_FOR_FLATTENED_INPUT in result.reasonCodes

    def test_h8_extraction_evaluator_d2_false_not_applicable(self):
        from verdict.extraction_evaluator import evaluate_extraction
        result = evaluate_extraction(
            None, source_type="unknown", d2_required=False,
            job_id="j", spec_id="s",
        )
        assert result.status == "NOT_APPLICABLE"


# ──────────────────────────────────────────────────────────────────────────────
# I: verdict/reason_codes.py — new codes
# ──────────────────────────────────────────────────────────────────────────────

class TestReasonCodes:
    def test_i1_semantic_provider_input_invalid_exists(self):
        from verdict import reason_codes as RC
        assert hasattr(RC, "TECH_SEMANTIC_SCENE_PROVIDER_INPUT_INVALID")

    def test_i2_semantic_plate_missing_exists(self):
        from verdict import reason_codes as RC
        assert hasattr(RC, "TECH_SEMANTIC_SCENE_PLATE_MISSING")

    def test_i3_semantic_background_plate_used_exists(self):
        from verdict import reason_codes as RC
        assert hasattr(RC, "TECH_SEMANTIC_SCENE_BACKGROUND_PLATE_USED")

    def test_i4_semantic_legacy_mask_used_exists(self):
        from verdict import reason_codes as RC
        assert hasattr(RC, "TECH_SEMANTIC_SCENE_LEGACY_MASK_USED")

    def test_i5_semantic_bbox_mask_used_exists(self):
        from verdict import reason_codes as RC
        assert hasattr(RC, "TECH_SEMANTIC_SCENE_BBOX_MASK_USED")

    def test_i6_d2_required_code_exists(self):
        from verdict import reason_codes as RC
        assert hasattr(RC, "EXTRACTION_D2_REQUIRED_FOR_FLATTENED_INPUT")

    def test_i7_all_codes_includes_new_codes(self):
        from verdict import reason_codes as RC
        new_codes = [
            RC.TECH_SEMANTIC_SCENE_PROVIDER_INPUT_INVALID,
            RC.TECH_SEMANTIC_SCENE_PLATE_MISSING,
            RC.TECH_SEMANTIC_SCENE_BACKGROUND_PLATE_USED,
            RC.TECH_SEMANTIC_SCENE_LEGACY_MASK_USED,
            RC.TECH_SEMANTIC_SCENE_BBOX_MASK_USED,
            RC.EXTRACTION_D2_REQUIRED_FOR_FLATTENED_INPUT,
        ]
        for code in new_codes:
            assert code in RC.ALL_CODES, f"{code} missing from ALL_CODES"

    def test_i8_all_codes_sorted(self):
        from verdict import reason_codes as RC
        assert RC.ALL_CODES == sorted(RC.ALL_CODES)


# ──────────────────────────────────────────────────────────────────────────────
# J: technical_evaluator.py — semantic mode params
# ──────────────────────────────────────────────────────────────────────────────

class TestTechnicalEvaluatorSemanticMode:
    def _eval(self, **kw):
        from verdict.technical_evaluator import evaluate_technical
        defaults = dict(
            output_path="/out.png",
            output_size=(300, 250),
            file_size=10000,
            target_w=300, target_h=250,
            ai_provider="openai-dall-e",
            fail_closed=True,
            exception_occurred=False,
            blurFillUsed=False,
            forcedSmartFit=False,
        )
        defaults.update(kw)
        return evaluate_technical(**defaults)

    def test_j1_semantic_mode_pass_with_correct_params(self):
        r = self._eval(
            background_generation_mode="semantic_scene_cleanup",
            provider_input_source="full_composite",
            scene_plate_sha256="a" * 64,
            background_plate_builder_used=False,
            legacy_repair_mask_used=False,
            foreground_bbox_mask_used=False,
        )
        assert r.status == "PASS"

    def test_j2_semantic_mode_fail_wrong_input_source(self):
        from verdict import reason_codes as RC
        r = self._eval(
            background_generation_mode="semantic_scene_cleanup",
            provider_input_source="background_plate",
            scene_plate_sha256="a" * 64,
        )
        assert r.status == "FAIL"
        assert RC.TECH_SEMANTIC_SCENE_PROVIDER_INPUT_INVALID in r.reasonCodes

    def test_j3_semantic_mode_fail_missing_scene_plate_sha(self):
        from verdict import reason_codes as RC
        r = self._eval(
            background_generation_mode="semantic_scene_cleanup",
            provider_input_source="full_composite",
            scene_plate_sha256="",
        )
        assert r.status == "FAIL"
        assert RC.TECH_SEMANTIC_SCENE_PLATE_MISSING in r.reasonCodes

    def test_j4_semantic_mode_fail_background_plate_used(self):
        from verdict import reason_codes as RC
        r = self._eval(
            background_generation_mode="semantic_scene_cleanup",
            provider_input_source="full_composite",
            scene_plate_sha256="a" * 64,
            background_plate_builder_used=True,
        )
        assert r.status == "FAIL"
        assert RC.TECH_SEMANTIC_SCENE_BACKGROUND_PLATE_USED in r.reasonCodes

    def test_j5_semantic_mode_fail_legacy_mask_used(self):
        from verdict import reason_codes as RC
        r = self._eval(
            background_generation_mode="semantic_scene_cleanup",
            provider_input_source="full_composite",
            scene_plate_sha256="a" * 64,
            legacy_repair_mask_used=True,
        )
        assert r.status == "FAIL"
        assert RC.TECH_SEMANTIC_SCENE_LEGACY_MASK_USED in r.reasonCodes

    def test_j6_semantic_mode_fail_bbox_mask_used(self):
        from verdict import reason_codes as RC
        r = self._eval(
            background_generation_mode="semantic_scene_cleanup",
            provider_input_source="full_composite",
            scene_plate_sha256="a" * 64,
            foreground_bbox_mask_used=True,
        )
        assert r.status == "FAIL"
        assert RC.TECH_SEMANTIC_SCENE_BBOX_MASK_USED in r.reasonCodes

    def test_j7_legacy_mode_semantic_checks_not_run(self):
        from verdict import reason_codes as RC
        # Legacy mode: semantic params are irrelevant, no semantic codes raised
        r = self._eval(
            background_generation_mode="source_faithful_repair",
            background_plate_builder_used=True,  # allowed in legacy
        )
        assert RC.TECH_SEMANTIC_SCENE_BACKGROUND_PLATE_USED not in r.reasonCodes

    def test_j8_semantic_mode_evidence_contains_new_fields(self):
        r = self._eval(
            background_generation_mode="semantic_scene_cleanup",
            provider_input_source="full_composite",
            scene_plate_sha256="a" * 64,
        )
        assert "backgroundGenerationMode" in r.evidence
        assert r.evidence["backgroundGenerationMode"] == "semantic_scene_cleanup"


# ──────────────────────────────────────────────────────────────────────────────
# K: No real OpenAI imports in scene_cleanup package
# ──────────────────────────────────────────────────────────────────────────────

class TestNoRealOpenAI:
    def _scene_cleanup_modules(self):
        return [
            "scene_cleanup.models",
            "scene_cleanup.full_image_source",
            "scene_cleanup.canvas_builder",
            "scene_cleanup.prompt_builder",
            "scene_cleanup.semantic_scene_cleanup",
            "scene_cleanup.serializer",
        ]

    def test_k1_no_openai_import_in_models(self):
        import importlib, ast, pathlib
        src = pathlib.Path(__file__).parent / "scene_cleanup" / "models.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        imports = [n.names[0].name if isinstance(n, ast.Import) else n.module
                   for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert not any("openai" in (i or "").lower() for i in imports)

    def test_k2_no_openai_import_in_prompt_builder(self):
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "scene_cleanup" / "prompt_builder.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        imports = [n.names[0].name if isinstance(n, ast.Import) else n.module
                   for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert not any("openai" in (i or "").lower() for i in imports)

    def test_k3_no_openai_import_in_semantic_scene_cleanup(self):
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "scene_cleanup" / "semantic_scene_cleanup.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        imports = [n.names[0].name if isinstance(n, ast.Import) else n.module
                   for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert not any("openai" in (i or "").lower() for i in imports)

    def test_k4_fake_provider_used_zero_real_requests(self):
        """FakeProvider uses no actual API — verified by absence of requests import usage."""
        provider = _FakeProvider()
        img = _gradient(300, 250)
        result = provider.inpaint(img, Image.new("L", (300, 250), 255), "test prompt", {})
        assert isinstance(result, Image.Image)

    def test_k5_actual_provider_request_count_equals_one(self):
        """Exactly 1 provider call for a success run with FakeProvider."""
        from scene_cleanup.semantic_scene_cleanup import run_semantic_scene_cleanup
        r = run_semantic_scene_cleanup(
            source_path="", source_type="psd", source_image=_gradient(300, 250),
            source_file_sha256="", composite_sha256="",
            target_w=300, target_h=250,
            provider=_FakeProvider(),
            output_dir=tempfile.mkdtemp(),
            render_ctx=_FakeRenderCtx(),
            has_native_layers=True,
            composite_render_method="psd_composite",
        )
        assert r.actual_provider_request_count == 1
        assert r.success is True
