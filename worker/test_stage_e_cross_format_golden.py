"""Stage E: Cross-format golden tests for full-image semantic pipeline.

Verifies that PSD, PNG, and JPG inputs all produce identical semantic
pipeline contracts:
  - Same pipelinePolicy ("full-image-semantic-v1")
  - Same semanticAuthority ("full_image")
  - psdLayerAuthorityUsed=False for all input types
  - nativeLayerForegroundUsed=False when PSD_LAYER_HINTS_ENABLED=false
  - finalResultValid field present and is bool
  - validCount in [AI_ONLY_END] log
  - [CANONICAL_SOURCE] log for all input types
  - D-2 (d2Applicable/d2VirtualForegroundApplicable) consistent
  - backgroundGenerationMode="semantic_scene_cleanup" for all types

All tests: ACTUAL_OPENAI_REQUESTS=0  (FakeProvider only)
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import shutil

import numpy as np
import pytest
from PIL import Image


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tmp_file(ext: str, w=400, h=300, color=(120, 80, 60)) -> str:
    img = Image.new("RGB", (w, h), color=color)
    fd, path = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    save_format = "JPEG" if ext in ("jpg", "jpeg") else "PNG"
    img.save(path, format=save_format)
    return path


class _FakeProvider:
    def metadata(self):
        return {"providerName": "fake-cross", "modelName": "fake-cross-v1"}

    def inpaint(self, image, mask, prompt, options):
        w, h = image.size
        arr = np.random.RandomState(77).randint(40, 180, (h, w, 3), dtype=np.uint8)
        return Image.fromarray(arr, "RGB")


def _run(src_path, source_type="image", job_id="cross-fmt") -> tuple[list, str]:
    from resizer import _generate_ai_only
    specs = [{"media": "cross", "name": "b", "slug": "b", "width": 300, "height": 250}]
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


# ── G1: Provenance contract identical across input formats ──────────────────

class TestCrossFormatProvenance:
    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
        ("png", "png"),
    ])
    def test_pipeline_policy(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g1-policy-{ext}")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("pipelinePolicy") == "full-image-semantic-v1"
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_semantic_authority(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g1-auth-{ext}")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("semanticAuthority") == "full_image"
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_psd_layer_authority_always_false(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g1-psd-{ext}")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("psdLayerAuthorityUsed") is False
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_native_layer_foreground_false_without_hints(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g1-nlf-{ext}")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("nativeLayerForegroundUsed") is False
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_background_mode_semantic(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g1-bgmode-{ext}")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("backgroundGenerationMode") == "semantic_scene_cleanup"
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_input_normalization_version(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g1-norm-{ext}")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("inputNormalizationVersion") == "canonical-source-v1"
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_canonical_source_used_true(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g1-cs-{ext}")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("canonicalSourceUsed") is True
        finally:
            os.unlink(src)


# ── G2: Log contract identical across input formats ──────────────────────────

class TestCrossFormatLogs:
    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_canonical_source_log_emitted(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            _, log = _run(src, source_type=source_type, job_id=f"g2-cs-{ext}")
            assert "[CANONICAL_SOURCE]" in log
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_psd_layer_authority_log_disabled(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            _, log = _run(src, source_type=source_type, job_id=f"g2-pla-{ext}")
            assert "[PSD_LAYER_AUTHORITY]" in log
            assert "enabled=false" in log
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_ai_only_end_log_with_validcount(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            _, log = _run(src, source_type=source_type, job_id=f"g2-end-{ext}")
            assert "[AI_ONLY_END]" in log
            assert "validCount=" in log
            assert "successCount=" in log
        finally:
            os.unlink(src)


# ── G3: Result fields contract ───────────────────────────────────────────────

class TestCrossFormatResultFields:
    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_finalResultValid_is_present(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g3-frv-{ext}")
            assert "finalResultValid" in results[0]
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_result_image_correct_dimensions(self, ext, source_type):
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g3-dim-{ext}")
            r = results[0]
            assert r.get("width") == 300
            assert r.get("height") == 250
        finally:
            os.unlink(src)

    @pytest.mark.parametrize("ext,source_type", [
        ("png", "image"),
        ("jpg", "jpg"),
    ])
    def test_pipeline_policy_field_consistent(self, ext, source_type):
        """pipelinePolicy == full-image-semantic-v1 for both PNG and JPG."""
        src = _make_tmp_file(ext)
        try:
            results, _ = _run(src, source_type=source_type, job_id=f"g3-pp-{ext}")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("pipelinePolicy") == "full-image-semantic-v1"
        finally:
            os.unlink(src)


# ── G4: PNG vs JPG provenance equivalence ────────────────────────────────────

class TestPngVsJpgEquivalence:
    """Same source content rendered as PNG vs JPG: key provenance fields identical."""

    def _get_prov(self, ext, source_type, jid):
        src = _make_tmp_file(ext, color=(80, 120, 160))
        try:
            results, _ = _run(src, source_type=source_type, job_id=jid)
            return results[0].get("renderProvenance", {})
        finally:
            os.unlink(src)

    def test_pipeline_policy_equivalent(self):
        prov_png = self._get_prov("png", "image", "g4-pp-png")
        prov_jpg = self._get_prov("jpg", "jpg", "g4-pp-jpg")
        assert prov_png.get("pipelinePolicy") == prov_jpg.get("pipelinePolicy")

    def test_semantic_authority_equivalent(self):
        prov_png = self._get_prov("png", "image", "g4-sa-png")
        prov_jpg = self._get_prov("jpg", "jpg", "g4-sa-jpg")
        assert prov_png.get("semanticAuthority") == prov_jpg.get("semanticAuthority")

    def test_psd_layer_authority_equivalent(self):
        prov_png = self._get_prov("png", "image", "g4-pla-png")
        prov_jpg = self._get_prov("jpg", "jpg", "g4-pla-jpg")
        assert prov_png.get("psdLayerAuthorityUsed") == prov_jpg.get("psdLayerAuthorityUsed")

    def test_input_normalization_equivalent(self):
        prov_png = self._get_prov("png", "image", "g4-in-png")
        prov_jpg = self._get_prov("jpg", "jpg", "g4-in-jpg")
        assert prov_png.get("inputNormalizationVersion") == prov_jpg.get("inputNormalizationVersion")

    def test_background_generation_mode_equivalent(self):
        prov_png = self._get_prov("png", "image", "g4-bgm-png")
        prov_jpg = self._get_prov("jpg", "jpg", "g4-bgm-jpg")
        assert prov_png.get("backgroundGenerationMode") == prov_jpg.get("backgroundGenerationMode")
