"""Stage E-3: No legacy fallback tests.

Verifies that:
 1. BACKGROUND_GENERATION_MODE=source_faithful_repair → CONFIG_LEGACY_PIPELINE_FORBIDDEN
 2. [FORBIDDEN_FALLBACK_GUARD] log emitted before error
 3. Error message contains 'source_faithful_repair' and 'CONFIG_LEGACY_PIPELINE_FORBIDDEN'
 4. Semantic (default) mode still succeeds after a forbidden attempt
 5. An arbitrary invalid mode still hits the generic INVALID_BACKGROUND_GENERATION_MODE error
 6. env var always cleaned in finally blocks

All tests: ACTUAL_OPENAI_REQUESTS=0  (FakeProvider only)
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import shutil

import pytest
from PIL import Image
import numpy as np


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tmp_png(w=400, h=300, color=(80, 80, 80)) -> str:
    img = Image.new("RGB", (w, h), color=color)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


class _FakeProvider:
    def metadata(self):
        return {"providerName": "fake-e3", "modelName": "fake-e3-v1"}

    def inpaint(self, image, mask, prompt, options):
        w, h = image.size
        arr = np.random.RandomState(42).randint(40, 180, (h, w, 3), dtype=np.uint8)
        return Image.fromarray(arr, "RGB")


def _run(src, env_val=None, *, job_id="e3-test") -> tuple[list, str]:
    """Run _generate_ai_only with optional BACKGROUND_GENERATION_MODE.

    Returns (results, log_str). Raises whatever _generate_ai_only raises.
    """
    from resizer import _generate_ai_only
    specs = [{"media": "t", "name": "x", "slug": "x", "width": 300, "height": 250}]
    tmp = tempfile.mkdtemp()
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        if env_val is not None:
            os.environ["BACKGROUND_GENERATION_MODE"] = env_val
        else:
            os.environ.pop("BACKGROUND_GENERATION_MODE", None)
        results, _ = _generate_ai_only(
            psd_path=src,
            specs=specs,
            resize_mode="ai-auto",
            output_format="png",
            output_dir=tmp,
            source_type="image",
            job_id=job_id,
            _provider_override=_FakeProvider(),
        )
        return results, buf.getvalue()
    finally:
        sys.stdout = old
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("BACKGROUND_GENERATION_MODE", None)


# ── E3-A: SFR forbidden ───────────────────────────────────────────────────────

class TestSFRForbidden:
    def test_sfr_raises_config_legacy_error(self):
        src = _make_tmp_png()
        try:
            with pytest.raises(RuntimeError) as exc_info:
                _run(src, "source_faithful_repair", job_id="e3-a1")
            assert "CONFIG_LEGACY_PIPELINE_FORBIDDEN" in str(exc_info.value)
        finally:
            os.unlink(src)

    def test_sfr_error_message_mentions_source_faithful_repair(self):
        src = _make_tmp_png()
        try:
            with pytest.raises(RuntimeError) as exc_info:
                _run(src, "source_faithful_repair", job_id="e3-a2")
            assert "source_faithful_repair" in str(exc_info.value)
        finally:
            os.unlink(src)

    def test_sfr_emits_forbidden_fallback_guard_log(self):
        src = _make_tmp_png()
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            from resizer import _generate_ai_only
            specs = [{"media": "t", "name": "x", "slug": "x", "width": 300, "height": 250}]
            tmp = tempfile.mkdtemp()
            os.environ["BACKGROUND_GENERATION_MODE"] = "source_faithful_repair"
            try:
                _generate_ai_only(
                    psd_path=src, specs=specs, resize_mode="ai-auto",
                    output_format="png", output_dir=tmp,
                    source_type="image", job_id="e3-a3",
                    _provider_override=_FakeProvider(),
                )
            except RuntimeError:
                pass
            finally:
                os.environ.pop("BACKGROUND_GENERATION_MODE", None)
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            sys.stdout = old
            os.unlink(src)
        log = buf.getvalue()
        assert "[FORBIDDEN_FALLBACK_GUARD]" in log

    def test_sfr_guard_log_contains_policy(self):
        src = _make_tmp_png()
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            from resizer import _generate_ai_only
            specs = [{"media": "t", "name": "x", "slug": "x", "width": 300, "height": 250}]
            tmp = tempfile.mkdtemp()
            os.environ["BACKGROUND_GENERATION_MODE"] = "source_faithful_repair"
            try:
                _generate_ai_only(
                    psd_path=src, specs=specs, resize_mode="ai-auto",
                    output_format="png", output_dir=tmp,
                    source_type="image", job_id="e3-a4",
                    _provider_override=_FakeProvider(),
                )
            except RuntimeError:
                pass
            finally:
                os.environ.pop("BACKGROUND_GENERATION_MODE", None)
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            sys.stdout = old
            os.unlink(src)
        log = buf.getvalue()
        assert "CONFIG_LEGACY_PIPELINE_FORBIDDEN" in log

    def test_sfr_is_runtime_error_not_other(self):
        src = _make_tmp_png()
        try:
            try:
                _run(src, "source_faithful_repair", job_id="e3-a5")
                assert False, "Should have raised"
            except RuntimeError:
                pass  # correct
            except Exception as e:
                assert False, f"Wrong exception type: {type(e).__name__}: {e}"
        finally:
            os.unlink(src)


# ── E3-B: Semantic still works ────────────────────────────────────────────────

class TestSemanticStillWorks:
    def test_default_mode_succeeds(self):
        src = _make_tmp_png()
        try:
            results, _ = _run(src, job_id="e3-b1")
            assert len(results) == 1
            prov = results[0].get("renderProvenance", {})
            assert prov.get("backgroundGenerationMode") == "semantic_scene_cleanup"
        finally:
            os.unlink(src)

    def test_semantic_explicit_mode_succeeds(self):
        src = _make_tmp_png()
        try:
            results, _ = _run(src, "semantic_scene_cleanup", job_id="e3-b2")
            assert len(results) == 1
        finally:
            os.unlink(src)

    def test_semantic_after_forbidden_attempt_succeeds(self):
        src = _make_tmp_png()
        try:
            # First: forbidden → error (env var cleaned in finally)
            with pytest.raises(RuntimeError, match="CONFIG_LEGACY_PIPELINE_FORBIDDEN"):
                _run(src, "source_faithful_repair", job_id="e3-b3-sfr")
            # Confirm env var was cleaned
            assert os.environ.get("BACKGROUND_GENERATION_MODE") is None
            # Second: semantic → success
            results, _ = _run(src, job_id="e3-b3-semantic")
            assert len(results) == 1
        finally:
            os.unlink(src)


# ── E3-C: Env var isolation ───────────────────────────────────────────────────

class TestEnvVarIsolation:
    def test_env_var_cleared_even_on_forbidden_error(self):
        src = _make_tmp_png()
        try:
            os.environ["BACKGROUND_GENERATION_MODE"] = "source_faithful_repair"
            try:
                _run(src, job_id="e3-c1")
            except RuntimeError:
                pass
            # _run's finally always pops the var
            assert os.environ.get("BACKGROUND_GENERATION_MODE") is None
        finally:
            os.unlink(src)
            os.environ.pop("BACKGROUND_GENERATION_MODE", None)

    def test_sfr_env_not_inherited_by_next_call(self):
        src = _make_tmp_png()
        try:
            # Call 1: forbidden
            with pytest.raises(RuntimeError):
                _run(src, "source_faithful_repair", job_id="e3-c2a")
            # Call 2: should see default (no env)
            assert os.environ.get("BACKGROUND_GENERATION_MODE") is None
            results, _ = _run(src, job_id="e3-c2b")
            prov = results[0].get("renderProvenance", {}) if results else {}
            assert prov.get("backgroundGenerationMode") == "semantic_scene_cleanup"
        finally:
            os.unlink(src)


# ── E3-D: Generic invalid mode still hits INVALID_BACKGROUND_GENERATION_MODE ──

class TestInvalidModeHandling:
    def test_garbage_mode_raises_invalid_not_legacy(self):
        src = _make_tmp_png()
        try:
            with pytest.raises(RuntimeError) as exc_info:
                _run(src, "garbage_mode_xyz", job_id="e3-d1")
            err = str(exc_info.value)
            assert "INVALID_BACKGROUND_GENERATION_MODE" in err
            assert "CONFIG_LEGACY_PIPELINE_FORBIDDEN" not in err
        finally:
            os.unlink(src)
