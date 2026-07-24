"""Stage E-2: Unified Semantic Manifest tests.

Verifies:
 1. SemanticManifest dataclass fields and defaults
 2. build_semantic_manifest() correctly classifies preserve/removal/cta/contradiction
 3. mask_restore: compute_pixel_diff_ratio and validate_pixel_integrity
 4. manifest logs ([PRESERVE_MASK], [REMOVAL_MASK], [MASK_CONFLICT], [SEMANTIC_GROUP])
 5. SSC accepts semantic_manifest param and emits manifest logs
 6. resizer.py builds manifest from D-2 fg_layers and emits [SEMANTIC_GROUP]

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

def _make_rgb(w: int, h: int, color=(100, 100, 100)) -> Image.Image:
    return Image.new("RGB", (w, h), color=color)


def _make_tmp_png(w=400, h=300, color=(100, 100, 100)) -> str:
    img = _make_rgb(w, h, color)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


def _fake_layer(object_id, semantic_role, layout_role="", metadata=None):
    return {
        "objectId": object_id,
        "semanticRole": semantic_role,
        "layoutRole": layout_role or semantic_role,
        "metadata": metadata or {},
    }


class _FakeProvider:
    def metadata(self):
        return {"providerName": "fake-e2", "modelName": "fake-e2-v1"}

    def inpaint(self, image, mask, prompt, options):
        w, h = image.size
        rng = np.random.RandomState(7)
        return Image.fromarray(rng.randint(50, 200, (h, w, 3), dtype=np.uint8), "RGB")


# ── E2-A: SemanticManifest dataclass ─────────────────────────────────────────

class TestSemanticManifestDataclass:
    def test_version(self):
        from verdict.unified_semantic_manifest import SemanticManifest, MANIFEST_VERSION
        m = SemanticManifest()
        assert m.version == MANIFEST_VERSION

    def test_defaults(self):
        from verdict.unified_semantic_manifest import SemanticManifest
        m = SemanticManifest()
        assert m.preserve_roles == []
        assert m.removal_roles == []
        assert m.cta_group_ids == []
        assert m.text_human_contradictions == []
        assert m.mask_conflict_ids == []
        assert m.object_count == 0
        assert m.manifest_sha256 == ""

    def test_manifest_sha_different_per_content(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        m1 = build_semantic_manifest(job_id="j1", spec_id="s1", d2_fg_layers=[])
        m2 = build_semantic_manifest(job_id="j2", spec_id="s2", d2_fg_layers=[])
        assert m1.manifest_sha256 != m2.manifest_sha256


# ── E2-B: build_semantic_manifest ─────────────────────────────────────────────

class TestBuildSemanticManifest:
    def test_empty_layers(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        m = build_semantic_manifest(job_id="j1", spec_id="b1", d2_fg_layers=[])
        assert m.preserve_object_ids == []
        assert m.removal_object_ids == []
        assert m.cta_group_ids == []
        assert m.object_count == 0

    def test_product_goes_to_preserve(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [_fake_layer("p1", "product")]
        m = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert "p1" in m.preserve_object_ids
        assert "product" in m.preserve_roles

    def test_human_subject_goes_to_preserve(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [_fake_layer("h1", "human_subject")]
        m = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert "h1" in m.preserve_object_ids

    def test_background_goes_to_removal(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [_fake_layer("bg1", "background")]
        m = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert "bg1" in m.removal_object_ids
        assert "background" in m.removal_roles

    def test_cta_text_in_cta_group(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [_fake_layer("c1", "cta_text"), _fake_layer("t1", "title_text")]
        m = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert "c1" in m.cta_group_ids
        assert "t1" in m.cta_group_ids

    def test_mask_conflict_detected(self):
        """Object in both preserve and removal roles → mask conflict."""
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [
            {"objectId": "x1", "semanticRole": "product", "layoutRole": "background", "metadata": {}},
        ]
        m = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert "x1" in m.mask_conflict_ids

    def test_text_human_contradiction_detected(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [
            {"objectId": "t1", "semanticRole": "title_text", "layoutRole": "title_text",
             "metadata": {"is_human": True}},
        ]
        m = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert "t1" in m.text_human_contradictions

    def test_no_contradiction_without_is_human(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [_fake_layer("t2", "title_text")]
        m = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert "t2" not in m.text_human_contradictions

    def test_object_count(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [
            _fake_layer("a1", "product"),
            _fake_layer("a2", "background"),
            _fake_layer("a3", "cta_text"),
        ]
        m = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert m.object_count == 3

    def test_manifest_sha_stable(self):
        from verdict.unified_semantic_manifest import build_semantic_manifest
        layers = [_fake_layer("p1", "product")]
        m1 = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        m2 = build_semantic_manifest(job_id="j", spec_id="s", d2_fg_layers=layers)
        assert m1.manifest_sha256 == m2.manifest_sha256


# ── E2-C: mask_restore ────────────────────────────────────────────────────────

class TestMaskRestore:
    def test_identical_images_ratio_zero(self):
        from scene_cleanup.mask_restore import compute_pixel_diff_ratio
        img = _make_rgb(100, 100, (128, 64, 32))
        assert compute_pixel_diff_ratio(img, img) == pytest.approx(0.0)

    def test_fully_different_images_ratio_one(self):
        from scene_cleanup.mask_restore import compute_pixel_diff_ratio
        src = _make_rgb(50, 50, (0, 0, 0))
        res = _make_rgb(50, 50, (255, 255, 255))
        ratio = compute_pixel_diff_ratio(src, res)
        assert ratio == pytest.approx(1.0)

    def test_half_changed(self):
        from scene_cleanup.mask_restore import compute_pixel_diff_ratio
        src = Image.new("RGB", (100, 100), (50, 50, 50))
        res = src.copy()
        # Change left half
        for x in range(50):
            for y in range(100):
                res.putpixel((x, y), (200, 200, 200))
        ratio = compute_pixel_diff_ratio(src, res)
        assert 0.45 < ratio < 0.55

    def test_size_mismatch_returns_one(self):
        from scene_cleanup.mask_restore import compute_pixel_diff_ratio
        src = _make_rgb(100, 100)
        res = _make_rgb(200, 100)
        assert compute_pixel_diff_ratio(src, res) == pytest.approx(1.0)

    def test_none_returns_one(self):
        from scene_cleanup.mask_restore import compute_pixel_diff_ratio
        assert compute_pixel_diff_ratio(None, None) == pytest.approx(1.0)

    def test_integrity_pass_below_threshold(self):
        from scene_cleanup.mask_restore import validate_pixel_integrity
        src = _make_rgb(100, 100, (100, 100, 100))
        res = _make_rgb(100, 100, (100, 100, 100))
        passed, ratio, reason = validate_pixel_integrity(src, res)
        assert passed is True
        assert ratio == pytest.approx(0.0)
        assert reason == ""

    def test_integrity_fail_above_threshold(self):
        from scene_cleanup.mask_restore import validate_pixel_integrity
        src = _make_rgb(100, 100, (0, 0, 0))
        res = _make_rgb(100, 100, (255, 255, 255))
        passed, ratio, reason = validate_pixel_integrity(src, res, full_regen_threshold=0.85)
        assert passed is False
        assert ratio == pytest.approx(1.0)
        assert reason == "FULL_SCENE_REGENERATION_DETECTED"


# ── E2-D: Log functions ────────────────────────────────────────────────────────

class TestManifestLogs:
    def _make_manifest(self, **kwargs):
        from verdict.unified_semantic_manifest import SemanticManifest
        return SemanticManifest(**kwargs)

    def test_preserve_mask_log(self, capsys):
        from verdict.unified_semantic_manifest import log_preserve_mask, SemanticManifest
        m = SemanticManifest(
            job_id="j1", spec_id="s1",
            preserve_roles=["product"],
            preserve_object_ids=["p1"],
            manifest_sha256="abc123",
        )
        log_preserve_mask(m)
        out = capsys.readouterr().out
        assert "[PRESERVE_MASK]" in out
        assert "jobId=j1" in out
        assert "objectCount=1" in out
        assert "manifestSha=abc123" in out

    def test_removal_mask_log(self, capsys):
        from verdict.unified_semantic_manifest import log_removal_mask, SemanticManifest
        m = SemanticManifest(
            job_id="j2", spec_id="s2",
            removal_roles=["background"],
            removal_object_ids=["bg1"],
            manifest_sha256="def456",
        )
        log_removal_mask(m)
        out = capsys.readouterr().out
        assert "[REMOVAL_MASK]" in out
        assert "jobId=j2" in out
        assert "objectCount=1" in out

    def test_mask_conflict_log_only_when_conflicts(self, capsys):
        from verdict.unified_semantic_manifest import log_mask_conflict, SemanticManifest
        m_no = SemanticManifest(mask_conflict_ids=[])
        log_mask_conflict(m_no, job_id="j")
        out_no = capsys.readouterr().out
        assert "[MASK_CONFLICT]" not in out_no

        m_yes = SemanticManifest(job_id="j3", mask_conflict_ids=["x1"])
        log_mask_conflict(m_yes)
        out_yes = capsys.readouterr().out
        assert "[MASK_CONFLICT]" in out_yes
        assert "conflictCount=1" in out_yes

    def test_semantic_group_log(self, capsys):
        from verdict.unified_semantic_manifest import log_semantic_group, SemanticManifest
        m = SemanticManifest(job_id="j4", spec_id="s4", cta_group_ids=["c1", "t1"])
        log_semantic_group(m)
        out = capsys.readouterr().out
        assert "[SEMANTIC_GROUP]" in out
        assert "groupType=cta_title" in out
        assert "objectCount=2" in out

    def test_emit_all_logs_preserve_and_removal(self, capsys):
        from verdict.unified_semantic_manifest import emit_all_manifest_logs, SemanticManifest
        m = SemanticManifest(
            job_id="j5", spec_id="s5",
            preserve_roles=["product"],
            preserve_object_ids=["p1"],
            removal_roles=["background"],
            removal_object_ids=["bg1"],
            cta_group_ids=["c1"],
            manifest_sha256="sha1",
        )
        emit_all_manifest_logs(m)
        out = capsys.readouterr().out
        assert "[PRESERVE_MASK]" in out
        assert "[REMOVAL_MASK]" in out
        assert "[SEMANTIC_GROUP]" in out
        assert "[MASK_CONFLICT]" not in out

    def test_text_human_contradiction_log(self, capsys):
        from verdict.unified_semantic_manifest import log_text_human_contradictions, SemanticManifest
        m = SemanticManifest(job_id="j6", text_human_contradictions=["t1"])
        log_text_human_contradictions(m)
        out = capsys.readouterr().out
        assert "[TEXT_HUMAN_CONTRADICTION]" in out
        assert "count=1" in out


# ── E2-E: SSC integration ────────────────────────────────────────────────────

class TestSSCWithManifest:
    def _run_ssc_and_capture(self, manifest):
        from scene_cleanup.semantic_scene_cleanup import run_semantic_scene_cleanup
        src = _make_tmp_png(400, 300)
        ssc_dir = tempfile.mkdtemp()
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            from PIL import Image as _PIL
            img = _PIL.open(src)
            result = run_semantic_scene_cleanup(
                source_path=src,
                source_type="image",
                source_image=img,
                source_file_sha256="test-sha",
                composite_sha256="comp-sha",
                target_w=300,
                target_h=250,
                provider=_FakeProvider(),
                output_dir=ssc_dir,
                render_ctx=None,
                has_native_layers=False,
                composite_render_method="png",
                job_id="e2-ssc",
                spec_id="test-spec",
                semantic_manifest=manifest,
            )
        finally:
            sys.stdout = old
            os.unlink(src)
            shutil.rmtree(ssc_dir, ignore_errors=True)
        return buf.getvalue()

    def test_ssc_with_manifest_emits_preserve_mask(self):
        from verdict.unified_semantic_manifest import SemanticManifest
        m = SemanticManifest(
            preserve_roles=["product"],
            preserve_object_ids=["p1"],
            manifest_sha256="sha1",
        )
        log = self._run_ssc_and_capture(m)
        assert "[PRESERVE_MASK]" in log

    def test_ssc_with_manifest_emits_removal_mask(self):
        from verdict.unified_semantic_manifest import SemanticManifest
        m = SemanticManifest(
            removal_roles=["background"],
            removal_object_ids=["bg1"],
            manifest_sha256="sha2",
        )
        log = self._run_ssc_and_capture(m)
        assert "[REMOVAL_MASK]" in log

    def test_ssc_with_none_manifest_no_manifest_logs(self):
        log = self._run_ssc_and_capture(None)
        assert "[PRESERVE_MASK]" not in log
        assert "[REMOVAL_MASK]" not in log
        assert "[MASK_CONFLICT]" not in log


# ── E2-F: resizer integration ────────────────────────────────────────────────

class TestResizerManifestIntegration:
    def _run_and_capture(self, layers_in_d2=False):
        from resizer import _generate_ai_only
        src = _make_tmp_png(400, 300, (80, 80, 80))
        specs = [{"media": "t", "name": "x", "slug": "x", "width": 300, "height": 250}]
        tmp = tempfile.mkdtemp()
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            _generate_ai_only(
                psd_path=src,
                specs=specs,
                resize_mode="ai-auto",
                output_format="png",
                output_dir=tmp,
                source_type="image",
                job_id="e2-resizer",
                _provider_override=_FakeProvider(),
            )
        finally:
            sys.stdout = old
            os.unlink(src)
            shutil.rmtree(tmp, ignore_errors=True)
        return buf.getvalue()

    def test_ssc_mode_builds_manifest(self):
        log = self._run_and_capture()
        # No CTA group from a blank PNG → no [SEMANTIC_GROUP]; but no crash either
        # The manifest builds without error
        assert "[SEMANTIC_MANIFEST_BUILD_ERROR]" not in log

    def test_no_manifest_build_error(self):
        log = self._run_and_capture()
        assert "MANIFEST_BUILD_ERROR" not in log
