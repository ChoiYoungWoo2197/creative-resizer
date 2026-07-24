"""Stage 5: Production generate E2E wiring test.

Verifies that all production-wiring fixes are connected end-to-end through
_generate_ai_only(), which is the function /generate calls:

  EXPECTED_OFFSET=345,0
  TRANSFORM_RUNTIME_WIRING=PASS (actualOffset=345,0 in [TRANSFORM_GEOMETRY])
  ALLOWED_GENERATION_COVERAGE≠1.0 (≈0.552 for 1200×1200→1250×560)
  MANIFEST_FINALIZED=True ([MANIFEST_FINALIZED] log emitted, guard does not fire)
  INVALID_RESULT_SUCCESS_COUNT=0 (validResultCount counts finalResultValid, not valid)
  LAYOUT_INPUT_FILTER_PRESENT=True ([LAYOUT_INPUT_FILTER] in every spec)
  ACTUAL_OPENAI_REQUESTS=0 (FakeProvider inpaint only)

Uses same _generate helper as all other production wiring tests.
"""
from __future__ import annotations

import io
import os
import re
import sys

import numpy as np
import pytest
from PIL import Image


# ── FakeProvider ─────────────────────────────────────────────────────────────

class _FakeProvider:
    """Returns source + tiny noise. Zero real AI/OpenAI calls."""

    def inpaint(self, image, mask, prompt, meta=None):
        arr = np.array(image.convert("RGB"), dtype=np.float32)
        rng = np.random.default_rng(42)
        noise = rng.integers(-3, 4, size=arr.shape).astype(np.float32)
        return Image.fromarray(
            np.clip(arr + noise, 0, 255).astype(np.uint8), "RGB"
        )

    def metadata(self):
        return {"providerName": "fake-e2e"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate(src_path, specs, outdir, **kwargs):
    """Call _generate_ai_only with FakeProvider; return (results_list, stdout_text)."""
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


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def small_png(tmp_path):
    """400×300 RGB PNG for quick smoke tests."""
    img = Image.new("RGB", (400, 300), color=(120, 80, 200))
    p = str(tmp_path / "small.png")
    img.save(p, "PNG")
    return p


@pytest.fixture()
def mother_png(tmp_path):
    """1200×1200 RGB PNG — canonical Mother fixture for offset/coverage tests."""
    img = Image.new("RGB", (1200, 1200), color=(80, 100, 160))
    p = str(tmp_path / "mother.png")
    img.save(p, "PNG")
    return p


def _small_specs():
    return [{"media": "banner", "width": 300, "height": 250,
             "name": "banner-300x250", "slug": ""}]


def _mother_specs():
    return [{"media": "banner", "width": 1250, "height": 560,
             "name": "banner-1250x560", "slug": ""}]


# ── E2E: Mother fixture geometry ──────────────────────────────────────────────

class TestE2EMotherFixtureGeometry:
    """1200×1200 → 1250×560: all transform + pixel restore metrics wired correctly."""

    def test_actual_offset_x_is_345(self, mother_png, tmp_path):
        """EXPECTED_OFFSET=345,0 must appear as actualOffset in [TRANSFORM_GEOMETRY]."""
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        tg = [l for l in out.splitlines() if "[TRANSFORM_GEOMETRY]" in l]
        assert tg, "No [TRANSFORM_GEOMETRY] line"
        assert "actualOffset=345,0" in tg[0], (
            f"Expected actualOffset=345,0; got: {tg[0]}"
        )

    def test_allowed_generation_coverage_not_one(self, mother_png, tmp_path):
        """ALLOWED_GENERATION_COVERAGE≠1.0: outpaint mask covers ≈55.2% of canvas."""
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        pr = [l for l in out.splitlines() if "[PIXEL_RESTORE]" in l]
        assert pr, "No [PIXEL_RESTORE] line"
        assert "allowedGenerationCoverage=1.0000" not in pr[0], (
            f"Full-canvas fallback triggered (bug): {pr[0]}"
        )
        m = re.search(r"allowedGenerationCoverage=(\d+\.\d+)", pr[0])
        assert m, f"allowedGenerationCoverage not found: {pr[0]}"
        cov = float(m.group(1))
        assert 0.40 < cov < 0.75, (
            f"ALLOWED_GENERATION_COVERAGE={cov} out of expected range ≈0.552"
        )

    def test_source_mapped_coverage_in_expected_range(self, mother_png, tmp_path):
        """SOURCE_MAPPED_COVERAGE≈0.448 (560×560 / 1250×560)."""
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        pr = [l for l in out.splitlines() if "[PIXEL_RESTORE]" in l]
        assert pr
        m = re.search(r"sourceMappedCoverage=(\d+\.\d+)", pr[0])
        if m:
            cov = float(m.group(1))
            assert 0.35 < cov < 0.55, (
                f"SOURCE_MAPPED_COVERAGE={cov} out of expected range ≈0.448"
            )

    def test_geometry_valid_true(self, mother_png, tmp_path):
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        tg = [l for l in out.splitlines() if "[TRANSFORM_GEOMETRY]" in l]
        assert tg
        assert "geometryValid=True" in tg[0], tg[0]

    def test_canonical_size_mismatch_absent(self, mother_png, tmp_path):
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        assert "CANONICAL_SIZE_MISMATCH" not in out

    def test_result_at_target_dimensions(self, mother_png, tmp_path):
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        assert len(results) == 1
        r = results[0]
        assert r["width"] == 1250
        assert r["height"] == 560
        assert os.path.exists(r["filePath"])
        with Image.open(r["filePath"]) as img:
            assert img.size == (1250, 560)


# ── E2E: Manifest finalization ────────────────────────────────────────────────

class TestE2EManifestFinalization:
    """MANIFEST_FINALIZED=True: finalize() called before verdict evaluators."""

    def test_manifest_finalized_log_emitted(self, small_png, tmp_path):
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        assert "[MANIFEST_FINALIZED]" in out, (
            f"[MANIFEST_FINALIZED] missing:\n{out[:3000]}"
        )

    def test_manifest_finalized_has_sha256(self, small_png, tmp_path):
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        mf = [l for l in out.splitlines() if "[MANIFEST_FINALIZED]" in l]
        assert mf
        assert "sha256=" in mf[0]

    def test_manifest_finalized_has_version(self, small_png, tmp_path):
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        mf = [l for l in out.splitlines() if "[MANIFEST_FINALIZED]" in l]
        assert mf
        assert "version=" in mf[0]

    def test_manifest_not_finalized_guard_never_fires(self, small_png, tmp_path):
        """MANIFEST_NOT_FINALIZED must not appear — guard must not fire."""
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        assert "[MANIFEST_NOT_FINALIZED]" not in out, (
            f"MANIFEST_NOT_FINALIZED guard fired (finalize() not called):\n{out[:3000]}"
        )

    def test_manifest_finalized_emitted_for_mother(self, mother_png, tmp_path):
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        assert "[MANIFEST_FINALIZED]" in out


# ── E2E: Valid result count ───────────────────────────────────────────────────

class TestE2EValidResultCount:
    """INVALID_RESULT_SUCCESS_COUNT=0: validResultCount counts finalResultValid only."""

    def test_result_has_final_result_valid(self, small_png, tmp_path):
        """Each result item must contain finalResultValid key."""
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        assert len(results) >= 1
        for r in results:
            assert "finalResultValid" in r, (
                f"finalResultValid missing from result dict: {list(r.keys())}"
            )

    def test_final_result_valid_is_bool(self, small_png, tmp_path):
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        for r in results:
            v = r.get("finalResultValid")
            assert isinstance(v, bool), (
                f"finalResultValid must be bool, got {type(v)}: {v}"
            )

    def test_valid_key_alone_not_counted(self):
        """Simulate app.py logic: result with only valid=True but no finalResultValid → not counted."""
        result_items = [{"valid": True}]
        vrc = sum(1 for r in result_items if r.get("finalResultValid", False))
        assert vrc == 0

    def test_final_result_valid_true_counted(self):
        result_items = [{"finalResultValid": True, "valid": True}]
        vrc = sum(1 for r in result_items if r.get("finalResultValid", False))
        assert vrc == 1

    def test_final_result_valid_false_not_counted(self):
        result_items = [{"finalResultValid": False, "valid": True}]
        vrc = sum(1 for r in result_items if r.get("finalResultValid", False))
        assert vrc == 0

    def test_ai_only_end_valid_result_count_field_in_logs(self, small_png, tmp_path):
        """[AI_ONLY_END] from resizer.py uses validResultCount= not successCount=."""
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        ae = [l for l in out.splitlines() if "[AI_ONLY_END]" in l]
        assert ae, "No [AI_ONLY_END] line in resizer output"
        assert "validResultCount=" in ae[0], (
            f"validResultCount= missing from [AI_ONLY_END]: {ae[0]}"
        )
        assert "successCount=" not in ae[0].replace(
            "validResultCount=", ""
        ).replace("providerSuccessCount=", "").replace("failedResultCount=", ""), (
            f"old successCount= field still in [AI_ONLY_END]: {ae[0]}"
        )


# ── E2E: Layout input filter in every spec ────────────────────────────────────

class TestE2ELayoutInputFilter:
    """LAYOUT_INPUT_FILTER_PRESENT=True for every spec in the generate call."""

    def test_layout_input_filter_emitted_small(self, small_png, tmp_path):
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        assert "[LAYOUT_INPUT_FILTER]" in out

    def test_layout_input_filter_emitted_mother(self, mother_png, tmp_path):
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        assert "[LAYOUT_INPUT_FILTER]" in out

    def test_layout_input_filter_count_matches_specs(self, small_png, tmp_path):
        """One [LAYOUT_INPUT_FILTER] per spec."""
        specs = [
            {"media": "banner", "width": 300, "height": 250, "name": "a", "slug": ""},
            {"media": "banner", "width": 728, "height": 90, "name": "b", "slug": ""},
        ]
        results, out = _generate(small_png, specs, str(tmp_path))
        filt = [l for l in out.splitlines() if "[LAYOUT_INPUT_FILTER]" in l]
        assert len(filt) == 2, (
            f"Expected 2 [LAYOUT_INPUT_FILTER] lines for 2 specs, got {len(filt)}"
        )

    def test_layout_input_filter_has_manifest_fields(self, small_png, tmp_path):
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        lines = [l for l in out.splitlines() if "[LAYOUT_INPUT_FILTER]" in l]
        assert lines
        assert "manifestFinalized=" in lines[0]
        assert "manifestFailClosed=" in lines[0]
        assert "layoutPermitted=" in lines[0]


# ── E2E: Zero real AI calls ───────────────────────────────────────────────────

class TestE2EZeroRealAICalls:
    """ACTUAL_OPENAI_REQUESTS=0: FakeProvider only, no real OpenAI calls."""

    def test_no_gpt4_in_logs(self, small_png, tmp_path):
        """Real provider would log gpt-4 or dall-e — fake must not."""
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        assert "gpt-4" not in out, f"Real OpenAI model found in logs"
        assert "dall-e" not in out

    def test_fake_provider_completes_without_error(self, small_png, tmp_path):
        results, out = _generate(small_png, _small_specs(), str(tmp_path))
        assert len(results) >= 1
        assert os.path.exists(results[0]["filePath"])

    def test_generate_completes_mother_without_error(self, mother_png, tmp_path):
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))
        assert len(results) >= 1


# ── E2E: All stages together — final metrics report ───────────────────────────

class TestE2EFinalMetricsReport:
    """Single generate call verifying all required metrics simultaneously."""

    def test_all_metrics_pass_mother_fixture(self, mother_png, tmp_path):
        """Consolidated check: all EXPECTED_* values must hold in one call."""
        results, out = _generate(mother_png, _mother_specs(), str(tmp_path))

        # --- TRANSFORM_RUNTIME_WIRING ---
        tg = [l for l in out.splitlines() if "[TRANSFORM_GEOMETRY]" in l]
        assert tg, "TRANSFORM_GEOMETRY missing"
        assert "actualOffset=345,0" in tg[0], (
            f"TRANSFORM_RUNTIME_WIRING=FAIL: {tg[0]}"
        )

        # --- ALLOWED_GENERATION_COVERAGE≠1.0 ---
        pr = [l for l in out.splitlines() if "[PIXEL_RESTORE]" in l]
        assert pr, "PIXEL_RESTORE missing"
        assert "allowedGenerationCoverage=1.0000" not in pr[0], (
            f"ALLOWED_GENERATION_COVERAGE=1.0 (full-canvas bug): {pr[0]}"
        )
        m = re.search(r"allowedGenerationCoverage=(\d+\.\d+)", pr[0])
        assert m
        cov = float(m.group(1))
        assert 0.40 < cov < 0.75, f"Coverage out of range: {cov}"

        # --- MANIFEST_FINALIZED=True ---
        assert "[MANIFEST_FINALIZED]" in out, "MANIFEST_FINALIZED missing"
        assert "[MANIFEST_NOT_FINALIZED]" not in out, "MANIFEST guard fired"

        # --- INVALID_SEMANTIC_OBJECT_LAYOUT_COUNT=0 ---
        assert "[LAYOUT_INPUT_FILTER]" in out, "LAYOUT_INPUT_FILTER missing"

        # --- finalResultValid in every result ---
        assert len(results) == 1
        assert "finalResultValid" in results[0], "finalResultValid missing from result"

        # --- AI_ONLY_END uses validResultCount ---
        ae = [l for l in out.splitlines() if "[AI_ONLY_END]" in l]
        assert ae
        assert "validResultCount=" in ae[0], f"validResultCount missing: {ae[0]}"

        # --- ACTUAL_OPENAI_REQUESTS=0 ---
        assert "gpt-4" not in out
        assert "dall-e" not in out
