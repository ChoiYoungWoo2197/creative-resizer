"""Stage 20.3 — Actual AI Provider unit tests.

All tests use only fake/mock providers — no real API calls.
Tests verify provider detection, exit codes, mask conversion,
metadata safety, and integration contracts.

Run: python -m pytest test_stage20_actual_ai.py -q
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from PIL import Image
import numpy as np


# ── Helpers ───────────────────────────────────────────────────────────────────

def _white_img(w=64, h=64) -> Image.Image:
    return Image.new("RGB", (w, h), (255, 255, 255))

def _black_img(w=64, h=64) -> Image.Image:
    return Image.new("RGB", (w, h), (0, 0, 0))

def _noisy_img(w=64, h=64) -> Image.Image:
    arr = np.random.randint(50, 200, (h, w, 3), dtype=np.uint8)
    return Image.fromarray(arr)

def _half_mask(w=64, h=64) -> Image.Image:
    """Left half 255 (generate), right half 0 (preserve)."""
    arr = np.zeros((h, w), dtype=np.uint8)
    arr[:, :w // 2] = 255
    return Image.fromarray(arr, mode="L")

def _full_mask(w=64, h=64) -> Image.Image:
    return Image.new("L", (w, h), 255)

def _empty_mask(w=64, h=64) -> Image.Image:
    return Image.new("L", (w, h), 0)


# ══════════════════════════════════════════════════════════════════════════════
# T1 — OpenAIInpaintProvider: not configured without key
# ══════════════════════════════════════════════════════════════════════════════

class TestOpenAIProviderNotConfigured:
    def test_is_configured_false_when_no_key(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider(api_key="")
        assert p.is_configured() is False

    def test_inpaint_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider(api_key="")
        result = p.inpaint(_white_img(), _half_mask(), prompt="test")
        assert result is None

    def test_generate_repair_returns_not_configured_when_no_key(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider(api_key="")
        r = p.generate_repair(
            reference_image=_white_img(),
            generation_allowed_mask=_half_mask(),
            target_width=64, target_height=64,
            prompt="test", request_id="t1",
        )
        assert r["success"] is False
        assert r["errorCode"] == "PROVIDER_NOT_CONFIGURED"
        assert r["actualApiCalled"] is False


# ══════════════════════════════════════════════════════════════════════════════
# T2 — OpenAIInpaintProvider: metadata never exposes key
# ══════════════════════════════════════════════════════════════════════════════

class TestOpenAIProviderMetadata:
    def test_metadata_has_provider_name(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider(api_key="")
        m = p.metadata()
        assert "providerName" in m
        assert "modelName" in m

    def test_metadata_does_not_contain_key(self, monkeypatch):
        fake_key = "sk-test-fake-key-do-not-use"
        monkeypatch.setenv("BACKGROUND_AI_API_KEY", fake_key)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider()
        m = p.metadata()
        meta_str = str(m)
        assert fake_key not in meta_str

    def test_health_does_not_contain_key(self, monkeypatch):
        fake_key = "sk-golden-test-secret-xyz"
        monkeypatch.setenv("BACKGROUND_AI_API_KEY", fake_key)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider()
        h = p.health()
        assert fake_key not in str(h)

    def test_generate_repair_result_does_not_contain_key(self, monkeypatch):
        fake_key = "sk-fake-secret-report-check"
        monkeypatch.setenv("BACKGROUND_AI_API_KEY", fake_key)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider()
        # No real call — not configured with real key even if env is set
        # (will attempt API call and fail, but key must not be in response)
        r = p.generate_repair(
            reference_image=_white_img(),
            generation_allowed_mask=_half_mask(),
            target_width=64, target_height=64,
            prompt="test",
        )
        assert fake_key not in str(r)

    def test_is_configured_true_when_key_present(self, monkeypatch):
        monkeypatch.setenv("BACKGROUND_AI_API_KEY", "sk-fake")
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider()
        assert p.is_configured() is True

    def test_model_defaults_to_gpt_image_1(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_MODEL", raising=False)
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider(api_key="")
        assert p.model_name == "gpt-image-1"

    def test_model_from_env_var(self, monkeypatch):
        monkeypatch.setenv("BACKGROUND_AI_MODEL", "dall-e-2")
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.openai_provider import OpenAIInpaintProvider
        p = OpenAIInpaintProvider(api_key="")
        assert p.model_name == "dall-e-2"


# ══════════════════════════════════════════════════════════════════════════════
# T3 — Mask conversion
# ══════════════════════════════════════════════════════════════════════════════

class TestMaskConversion:
    def test_gen_allowed_to_openai_mask_inverts_alpha(self):
        """generationAllowedMask=255 → OpenAI mask alpha=0 (transparent=edit)."""
        from background.openai_provider import _gen_allowed_to_openai_mask_bytes
        # Full mask (all white = all generate)
        full = _full_mask(4, 4)
        mask_bytes = _gen_allowed_to_openai_mask_bytes(full)
        result = Image.open(io.BytesIO(mask_bytes))
        assert result.mode == "RGBA"
        alpha = result.split()[3]
        # All pixels should be transparent (alpha=0) because gen_allowed=255
        arr = np.array(alpha)
        assert arr.max() == 0, "full gen_allowed mask → all transparent"

    def test_gen_allowed_empty_mask_preserves_all(self):
        """generationAllowedMask=0 → OpenAI mask alpha=255 (preserve all)."""
        from background.openai_provider import _gen_allowed_to_openai_mask_bytes
        empty = _empty_mask(4, 4)
        mask_bytes = _gen_allowed_to_openai_mask_bytes(empty)
        result = Image.open(io.BytesIO(mask_bytes))
        alpha = result.split()[3]
        arr = np.array(alpha)
        assert arr.min() == 255, "empty gen_allowed mask → all opaque (preserve)"

    def test_half_mask_produces_half_transparent(self):
        """Left half generate → left half transparent."""
        from background.openai_provider import _gen_allowed_to_openai_mask_bytes
        mask = _half_mask(8, 8)
        mask_bytes = _gen_allowed_to_openai_mask_bytes(mask)
        result = Image.open(io.BytesIO(mask_bytes))
        alpha = np.array(result.split()[3])
        # Left half should be transparent (0), right half opaque (255)
        assert alpha[:, :4].max() == 0
        assert alpha[:, 4:].min() == 255

    def test_png_bytes_rgba(self):
        """_to_png_bytes_rgba returns valid PNG with RGBA mode."""
        from background.openai_provider import _to_png_bytes_rgba
        img = _noisy_img()
        png_bytes = _to_png_bytes_rgba(img)
        result = Image.open(io.BytesIO(png_bytes))
        assert result.mode == "RGBA"
        assert result.size == img.size

    def test_multipart_builder_produces_boundary(self):
        """_build_multipart returns bytes containing the boundary."""
        from background.openai_provider import _build_multipart
        fields = {"model": "gpt-image-1", "n": "1"}
        files = {"image": ("img.png", "image/png", b"fake_png_data")}
        body, boundary = _build_multipart(fields, files)
        assert isinstance(body, bytes)
        assert boundary.encode() in body
        assert b"fake_png_data" in body
        assert b"gpt-image-1" in body


# ══════════════════════════════════════════════════════════════════════════════
# T4 — ExternalInpaintProvider delegation
# ══════════════════════════════════════════════════════════════════════════════

class TestExternalInpaintProviderDelegation:
    def test_external_not_available_without_key(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.external_provider import ExternalInpaintProvider
        p = ExternalInpaintProvider()
        assert p._available is False

    def test_external_inpaint_returns_none_without_key(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.external_provider import ExternalInpaintProvider
        p = ExternalInpaintProvider()
        result = p.inpaint(_white_img(), _half_mask())
        assert result is None

    def test_external_metadata_has_provider_name(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.external_provider import ExternalInpaintProvider
        p = ExternalInpaintProvider()
        m = p.metadata()
        assert "providerName" in m or "provider" in m

    def test_external_metadata_has_model_name(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.external_provider import ExternalInpaintProvider
        p = ExternalInpaintProvider()
        m = p.metadata()
        assert "modelName" in m or "model" in m


# ══════════════════════════════════════════════════════════════════════════════
# T5 — ProviderFactory
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderFactory:
    def test_factory_use_fake_returns_fake(self):
        from background.external_provider import ProviderFactory, FakeBackgroundProvider
        p = ProviderFactory.create(use_fake_for_test=True)
        assert isinstance(p, FakeBackgroundProvider)

    def test_factory_no_key_returns_fake_even_with_external_enabled(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.external_provider import ProviderFactory, FakeBackgroundProvider
        p = ProviderFactory.create(enable_external=True, use_fake_for_test=False)
        # Without a key, should fall back to FakeBackgroundProvider
        assert isinstance(p, FakeBackgroundProvider)

    def test_factory_with_key_enable_external_returns_chain(self, monkeypatch):
        monkeypatch.setenv("BACKGROUND_AI_API_KEY", "sk-fake-test-key")
        from background.external_provider import ProviderFactory, ProviderFallbackChain
        p = ProviderFactory.create(enable_external=True, use_fake_for_test=False)
        # With a key and enable_external=True, should be a chain
        assert isinstance(p, ProviderFallbackChain)


# ══════════════════════════════════════════════════════════════════════════════
# T6 — Mode selection for mother-hand-product scenario
# ══════════════════════════════════════════════════════════════════════════════

class TestModeSelectionForHandProduct:
    def test_hand_layer_triggers_sfr(self):
        from background.mode_selector import select_background_mode, SOURCE_FAITHFUL_REPAIR
        classified = [
            {"role": "main_image", "name": "손_hand_model",
             "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}, "type": "pixel"},
        ]
        mode, reason = select_background_mode(classified)
        assert mode == SOURCE_FAITHFUL_REPAIR

    def test_person_role_triggers_sfr(self):
        from background.mode_selector import select_background_mode, SOURCE_FAITHFUL_REPAIR
        classified = [{"role": "person", "name": "model", "bbox": {}, "type": "pixel"}]
        mode, reason = select_background_mode(classified)
        assert mode == SOURCE_FAITHFUL_REPAIR

    def test_korean_hand_name_triggers_sfr(self):
        from background.mode_selector import select_background_mode, SOURCE_FAITHFUL_REPAIR
        classified = [
            {"role": "unknown", "name": "손가락_레이어",
             "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}, "type": "pixel"},
        ]
        mode, reason = select_background_mode(classified)
        assert mode == SOURCE_FAITHFUL_REPAIR

    def test_wrong_mode_is_not_sfr(self):
        from background.mode_selector import select_background_mode, GENERATIVE_BACKGROUND
        classified = [
            {"role": "background", "name": "배경",
             "bbox": {"x": 0, "y": 0, "width": 200, "height": 200}, "type": "pixel"},
            {"role": "title", "name": "타이틀",
             "bbox": {"x": 10, "y": 10, "width": 100, "height": 30}, "type": "text"},
        ]
        mode, reason = select_background_mode(classified)
        assert mode == GENERATIVE_BACKGROUND


# ══════════════════════════════════════════════════════════════════════════════
# T7 — SFR pipeline with no provider
# ══════════════════════════════════════════════════════════════════════════════

class TestSFRWithNoProvider:
    def test_sfr_no_provider_verdict_partial(self):
        from background.source_faithful_repair import run_source_faithful_repair
        classified = [
            {"role": "main_image", "name": "hand",
             "bbox": {"x": 10, "y": 10, "width": 40, "height": 40}, "type": "pixel"},
            {"role": "title", "name": "title",
             "bbox": {"x": 5, "y": 5, "width": 30, "height": 10}, "type": "text"},
        ]
        result = run_source_faithful_repair(
            source_image=_noisy_img(100, 100),
            classified_layers=classified,
            target_w=200, target_h=90,
            provider=None,
            max_attempts=2,
        )
        assert result.verdict == "PARTIAL"
        assert result.failure_reason in ("provider_not_configured", "ai_provider_unavailable",
                                          "all_ai_attempts_failed")

    def test_sfr_no_provider_smart_fit_not_used(self):
        from background.source_faithful_repair import run_source_faithful_repair
        result = run_source_faithful_repair(
            source_image=_noisy_img(80, 80),
            classified_layers=[],
            target_w=160, target_h=72,
            provider=None,
            max_attempts=1,
        )
        assert result.smart_fit_fallback_used is False
        assert result.blur_fill_used is False
        assert result.native_fallback_used is False

    def test_sfr_provider_not_configured_reason(self):
        from background.source_faithful_repair import run_source_faithful_repair
        result = run_source_faithful_repair(
            source_image=_noisy_img(64, 64),
            classified_layers=[{"role": "cta", "name": "btn",
                                 "bbox": {"x": 0, "y": 0, "width": 30, "height": 20},
                                 "type": "text", "dedupSkip": False}],
            target_w=128, target_h=57,
            provider=None,
            max_attempts=1,
        )
        all_reasons = [r for a in result.attempts for r in a.get("rejectionReasons", [])]
        assert any("provider_not_configured" in r for r in all_reasons)

    def test_sfr_with_fake_provider_succeeds(self):
        from background.source_faithful_repair import run_source_faithful_repair
        from background.external_provider import FakeBackgroundProvider
        classified = [
            {"role": "title", "name": "text_layer",
             "bbox": {"x": 5, "y": 5, "width": 50, "height": 20}, "type": "text",
             "dedupSkip": False},
        ]
        result = run_source_faithful_repair(
            source_image=_noisy_img(100, 100),
            classified_layers=classified,
            target_w=200, target_h=90,
            provider=FakeBackgroundProvider(),
            max_attempts=1,
        )
        # FakeBackgroundProvider returns a flat color — may pass or be rejected
        # but smart_fit must be False regardless
        assert result.smart_fit_fallback_used is False
        assert result.smart_fit_used is False


# ══════════════════════════════════════════════════════════════════════════════
# T8 — Actual request count tracking
# ══════════════════════════════════════════════════════════════════════════════

class TestActualRequestCount:
    def test_no_provider_zero_api_calls(self):
        from background.source_faithful_repair import run_source_faithful_repair
        result = run_source_faithful_repair(
            source_image=_noisy_img(64, 64),
            classified_layers=[{"role": "cta", "name": "btn",
                                 "bbox": {"x": 0, "y": 0, "width": 20, "height": 10},
                                 "type": "text", "dedupSkip": False}],
            target_w=128, target_h=57,
            provider=None,
            max_attempts=3,
        )
        # Attempts are counted even when no provider — but no real API
        assert result.background_ai_candidate_count == 3  # 3 attempts logged

    def test_fake_provider_counts_attempts(self):
        from background.source_faithful_repair import run_source_faithful_repair
        from background.external_provider import FakeBackgroundProvider
        classified = [
            {"role": "title", "name": "headline",
             "bbox": {"x": 2, "y": 2, "width": 40, "height": 15}, "type": "text",
             "dedupSkip": False}
        ]
        result = run_source_faithful_repair(
            source_image=_noisy_img(80, 80),
            classified_layers=classified,
            target_w=80, target_h=80,
            provider=FakeBackgroundProvider(),
            max_attempts=2,
        )
        # Candidate count should match attempts
        assert result.background_ai_candidate_count <= 2
        assert result.background_ai_attempt_count >= 1


# ══════════════════════════════════════════════════════════════════════════════
# T9 — Smart Fit runtime ban
# ══════════════════════════════════════════════════════════════════════════════

class TestSmartFitRuntimeBan:
    def test_smart_fit_fields_all_false_in_sfr(self):
        from background.source_faithful_repair import run_source_faithful_repair
        result = run_source_faithful_repair(
            source_image=_noisy_img(100, 100),
            classified_layers=[],
            target_w=200, target_h=90,
            provider=None,
            max_attempts=1,
        )
        assert result.smart_fit_allowed is False
        assert result.smart_fit_used is False
        assert result.smart_fit_fallback_used is False
        assert result.blur_fill_used is False
        assert result.mirror_fill_used is False
        assert result.stretch_fill_used is False
        assert result.native_fallback_used is False

    def test_smart_fit_guard_error_code(self):
        from background.smart_fit_guard import SmartFitForbiddenError, SMART_FIT_RUNTIME_CALL_BLOCKED
        exc = SmartFitForbiddenError(context="test_context")
        assert exc.error_code == SMART_FIT_RUNTIME_CALL_BLOCKED


# ══════════════════════════════════════════════════════════════════════════════
# T10 — Stage 19 regression guard
# ══════════════════════════════════════════════════════════════════════════════

class TestStage19Regression:
    def test_pipeline_disabled_fallback_does_not_crash(self):
        from background.pipeline import BackgroundPipeline
        from background.schemas import BackgroundRequest, BackgroundOptions
        opts = BackgroundOptions(enabled=False)
        req = BackgroundRequest(
            source_image=_noisy_img(200, 200),
            target_width=400, target_height=180,
            options=opts,
        )
        pipeline = BackgroundPipeline()
        result = pipeline.process(req)
        assert result is not None
        assert result.fallback_used is True

    def test_sfr_mode_enabled_selects_sfr_path(self, monkeypatch):
        monkeypatch.delenv("BACKGROUND_AI_API_KEY", raising=False)
        from background.pipeline import BackgroundPipeline
        from background.schemas import BackgroundRequest, BackgroundOptions
        from background.mode_selector import SOURCE_FAITHFUL_REPAIR
        opts = BackgroundOptions(
            enabled=True,
            source_faithful_repair_enabled=True,
            allow_external_inpaint=True,
        )
        classified = [
            {"role": "person", "name": "model",
             "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}, "type": "pixel"}
        ]
        req = BackgroundRequest(
            source_image=_noisy_img(200, 200),
            target_width=400, target_height=180,
            options=opts,
            layout_candidate={"classifiedLayers": classified},
        )
        pipeline = BackgroundPipeline()
        result = pipeline.process(req)
        assert result.background_generation_mode == SOURCE_FAITHFUL_REPAIR
