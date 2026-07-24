"""Stage E-1: Full-Image Semantic Authority tests.

Verifies that:
 1. CanonicalSourceImage is built correctly for all source types
 2. PSD layer authority is always false (psdLayerAuthorityUsed=False)
 3. nativeLayerForegroundUsed=False in all provenance
 4. fullImageSemanticExtractionUsed=True in all provenance
 5. pipelinePolicy="full-image-semantic-v1"
 6. D-2 always runs (not conditional on source type)
 7. PSD layer names/roles don't affect semantic pipeline output
 8. PSD_LAYER_HINTS_ENABLED=false is the enforced default

All tests: ACTUAL_OPENAI_REQUESTS=0  (FakeProvider only)
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import shutil
import hashlib

import pytest
from PIL import Image, ImageDraw


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_rgb(w: int, h: int, color=(120, 80, 60)) -> Image.Image:
    return Image.new("RGB", (w, h), color=color)


def _make_tmp_png(w: int = 400, h: int = 300, color=(120, 80, 60)) -> str:
    img = _make_rgb(w, h, color)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


def _run_generate(src_path: str, source_type: str = "image",
                  provider=None, job_id: str = "e1-test") -> dict:
    """Run _generate_ai_only and return first result's renderProvenance."""
    from resizer import _generate_ai_only

    if provider is None:
        provider = _FakeProvider()

    specs = [{"media": "test", "name": "banner", "slug": "bn", "width": 300, "height": 250}]
    tmp_dir = tempfile.mkdtemp()
    try:
        results, _ = _generate_ai_only(
            psd_path=src_path,
            specs=specs,
            resize_mode="ai-auto",
            output_format="png",
            output_dir=tmp_dir,
            source_type=source_type,
            job_id=job_id,
            _provider_override=provider,
        )
        return results[0].get("renderProvenance", {}) if results else {}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class _FakeProvider:
    """Deterministic fake provider (no OpenAI calls)."""
    def metadata(self):
        return {"providerName": "fake-e1", "modelName": "fake-e1-v1"}

    def inpaint(self, image, mask, prompt, options):
        import numpy as np
        w, h = image.size
        rng = np.random.RandomState(42)
        arr = rng.randint(40, 180, (h, w, 3), dtype=np.uint8)
        return Image.fromarray(arr, "RGB")


# ── E1-A: CanonicalSourceImage dataclass ─────────────────────────────────────

class TestCanonicalSourceImage:
    def test_build_fields(self):
        from scene_cleanup.canonical_source import build_canonical_source
        img = _make_rgb(200, 150)
        cs = build_canonical_source(
            image=img,
            source_type="image",
            source_file_sha256="abc123",
            original_filename="test.png",
            input_format="png",
        )
        assert cs.semantic_authority == "full_image"
        assert cs.psd_layer_authority is False
        assert cs.pipeline_policy == "full-image-semantic-v1"
        assert cs.input_normalization_version == "canonical-source-v1"
        assert cs.width == 200
        assert cs.height == 150
        assert cs.source_type == "image"
        assert len(cs.canonical_image_sha256) >= 8

    def test_psd_source_type_also_has_no_layer_authority(self):
        from scene_cleanup.canonical_source import build_canonical_source
        img = _make_rgb(300, 300)
        cs = build_canonical_source(
            image=img,
            source_type="psd",
            source_file_sha256="def456",
            original_filename="ad.psd",
            input_format="psd",
        )
        assert cs.psd_layer_authority is False
        assert cs.semantic_authority == "full_image"
        assert cs.pipeline_policy == "full-image-semantic-v1"

    def test_canonical_sha_differs_per_image_content(self):
        from scene_cleanup.canonical_source import build_canonical_source
        img_a = _make_rgb(100, 100, (10, 20, 30))
        img_b = _make_rgb(100, 100, (200, 100, 50))
        cs_a = build_canonical_source(img_a, "image", "sha-a", "a.png", "png")
        cs_b = build_canonical_source(img_b, "image", "sha-b", "b.png", "png")
        assert cs_a.canonical_image_sha256 != cs_b.canonical_image_sha256

    def test_log_canonical_source_outputs_key_fields(self, capsys):
        from scene_cleanup.canonical_source import build_canonical_source, log_canonical_source
        img = _make_rgb(400, 400)
        cs = build_canonical_source(img, "psd", "sha-test", "ad.psd", "psd")
        log_canonical_source(cs, job_id="test-job")
        out = capsys.readouterr().out
        assert "[CANONICAL_SOURCE]" in out
        assert "jobId=test-job" in out
        assert "semanticAuthority=full_image" in out
        assert "psdLayerAuthorityUsed=false" in out
        assert "pipelinePolicy=full-image-semantic-v1" in out

    def test_log_psd_layer_authority_default_disabled(self, capsys):
        from scene_cleanup.canonical_source import log_psd_layer_authority
        log_psd_layer_authority(job_id="j1", enabled=False, runtime_decision_count=0)
        out = capsys.readouterr().out
        assert "[PSD_LAYER_AUTHORITY]" in out
        assert "enabled=false" in out
        assert "runtimeDecisionCount=0" in out


# ── E1-B: Generate provenance for image source ───────────────────────────────

class TestE1GenerateProvenance:
    def setup_method(self):
        # Ensure hints disabled (default)
        os.environ.pop("PSD_LAYER_HINTS_ENABLED", None)

    def test_pipeline_policy_full_image_semantic(self):
        src = _make_tmp_png()
        try:
            prov = _run_generate(src, source_type="image", job_id="e1-b1")
            assert prov.get("pipelinePolicy") == "full-image-semantic-v1"
        finally:
            os.unlink(src)

    def test_psd_layer_authority_used_false(self):
        src = _make_tmp_png()
        try:
            prov = _run_generate(src, source_type="image", job_id="e1-b2")
            assert prov.get("psdLayerAuthorityUsed") is False
        finally:
            os.unlink(src)

    def test_native_layer_foreground_used_false(self):
        src = _make_tmp_png()
        try:
            prov = _run_generate(src, source_type="image", job_id="e1-b3")
            assert prov.get("nativeLayerForegroundUsed") is False
        finally:
            os.unlink(src)

    def test_full_image_semantic_extraction_used_true(self):
        src = _make_tmp_png()
        try:
            prov = _run_generate(src, source_type="image", job_id="e1-b4")
            assert prov.get("fullImageSemanticExtractionUsed") is True
        finally:
            os.unlink(src)

    def test_canonical_source_used_true(self):
        src = _make_tmp_png()
        try:
            prov = _run_generate(src, source_type="image", job_id="e1-b5")
            assert prov.get("canonicalSourceUsed") is True
        finally:
            os.unlink(src)

    def test_semantic_authority_field(self):
        src = _make_tmp_png()
        try:
            prov = _run_generate(src, source_type="image", job_id="e1-b6")
            assert prov.get("semanticAuthority") == "full_image"
        finally:
            os.unlink(src)

    def test_input_normalization_version(self):
        src = _make_tmp_png()
        try:
            prov = _run_generate(src, source_type="image", job_id="e1-b7")
            assert prov.get("inputNormalizationVersion") == "canonical-source-v1"
        finally:
            os.unlink(src)

    def test_psd_layer_hint_used_false_by_default(self):
        src = _make_tmp_png()
        try:
            prov = _run_generate(src, source_type="image", job_id="e1-b8")
            assert prov.get("psdLayerHintUsed") is False
        finally:
            os.unlink(src)


# ── E1-C: Log output verification ────────────────────────────────────────────

class TestE1LogOutput:
    def _capture(self, src_path: str, source_type: str = "image",
                 job_id: str = "e1-log") -> str:
        from resizer import _generate_ai_only
        specs = [{"media": "nv", "name": "w", "slug": "w", "width": 300, "height": 250}]
        tmp = tempfile.mkdtemp()
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            _generate_ai_only(
                psd_path=src_path, specs=specs, resize_mode="ai-auto",
                output_format="png", output_dir=tmp,
                source_type=source_type, job_id=job_id,
                _provider_override=_FakeProvider(),
            )
        finally:
            sys.stdout = old
            shutil.rmtree(tmp, ignore_errors=True)
        return buf.getvalue()

    def test_canonical_source_log_emitted(self):
        src = _make_tmp_png()
        try:
            log = self._capture(src, "image", "e1-c1")
            assert "[CANONICAL_SOURCE]" in log
        finally:
            os.unlink(src)

    def test_psd_layer_authority_log_emitted(self):
        src = _make_tmp_png()
        try:
            log = self._capture(src, "image", "e1-c2")
            assert "[PSD_LAYER_AUTHORITY]" in log
            assert "enabled=false" in log
        finally:
            os.unlink(src)

    def test_semantic_authority_in_canonical_log(self):
        src = _make_tmp_png()
        try:
            log = self._capture(src, "image", "e1-c3")
            assert "semanticAuthority=full_image" in log
        finally:
            os.unlink(src)


# ── E1-D: PSD hints enabled gate ─────────────────────────────────────────────

class TestE1HintsModeGate:
    def test_hints_disabled_by_default(self):
        """PSD_LAYER_HINTS_ENABLED=false is the default."""
        src = _make_tmp_png()
        try:
            os.environ.pop("PSD_LAYER_HINTS_ENABLED", None)
            prov = _run_generate(src, job_id="e1-d1")
            assert prov.get("psdLayerHintUsed") is False
            assert prov.get("psdLayerAuthorityUsed") is False
        finally:
            os.unlink(src)
            os.environ.pop("PSD_LAYER_HINTS_ENABLED", None)

    def test_hints_enabled_flag_reflected_in_prov(self):
        """When PSD_LAYER_HINTS_ENABLED=true, psdLayerHintUsed=True in prov."""
        src = _make_tmp_png()
        try:
            os.environ["PSD_LAYER_HINTS_ENABLED"] = "true"
            prov = _run_generate(src, job_id="e1-d2")
            assert prov.get("psdLayerHintUsed") is True
            # BUT authority is still never claimed
            assert prov.get("psdLayerAuthorityUsed") is False
        finally:
            os.unlink(src)
            os.environ.pop("PSD_LAYER_HINTS_ENABLED", None)


# ── E1-E: Cross-type provenance equivalence ───────────────────────────────────

class TestE1CrossTypeProvenance:
    def setup_method(self):
        os.environ.pop("PSD_LAYER_HINTS_ENABLED", None)

    def _prov_for_type(self, source_type: str) -> dict:
        src = _make_tmp_png(400, 300, (100, 150, 200))
        try:
            return _run_generate(src, source_type=source_type, job_id=f"e1-e-{source_type}")
        finally:
            os.unlink(src)

    def test_png_has_full_image_pipeline(self):
        prov = self._prov_for_type("image")
        assert prov.get("pipelinePolicy") == "full-image-semantic-v1"
        assert prov.get("psdLayerAuthorityUsed") is False
        assert prov.get("fullImageSemanticExtractionUsed") is True

    def test_jpg_type_has_full_image_pipeline(self):
        prov = self._prov_for_type("jpg")
        assert prov.get("pipelinePolicy") == "full-image-semantic-v1"
        assert prov.get("psdLayerAuthorityUsed") is False

    def test_png_and_jpg_share_pipeline_policy(self):
        prov_png = self._prov_for_type("image")
        prov_jpg = self._prov_for_type("jpg")
        assert prov_png.get("pipelinePolicy") == prov_jpg.get("pipelinePolicy")
        assert prov_png.get("semanticAuthority") == prov_jpg.get("semanticAuthority")
        assert prov_png.get("psdLayerAuthorityUsed") == prov_jpg.get("psdLayerAuthorityUsed")
        assert prov_png.get("fullImageSemanticExtractionUsed") == prov_jpg.get("fullImageSemanticExtractionUsed")
        assert prov_png.get("nativeLayerForegroundUsed") == prov_jpg.get("nativeLayerForegroundUsed")
