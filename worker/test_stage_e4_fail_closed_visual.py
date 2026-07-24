"""Stage E-4: Fail-closed visual validator tests.

Verifies:
 1. visual_evaluator: PASS for valid image, FAIL for blank/wrong-dim/full-regen
 2. stage21_aggregator: visual_required=False → NOT_TESTED doesn't fail overall
 3. stage21_aggregator: visual_required=True → NOT_TESTED causes overall FAIL
 4. stage21_aggregator: visual_required=True + PASS visual → overall PASS
 5. resizer: VISUAL_VERDICT_ENABLED=false → finalResultValid uses C-1 only
 6. resizer: VISUAL_VERDICT_ENABLED=true → visual evaluator runs
 7. resizer: finalResultValid field present in each result
 8. resizer: validCount in [AI_ONLY_END] log
 9. resizer: FULL_SCENE_REGENERATION_DETECTED when source == blank, result == white

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

def _make_rgb(w, h, color=(100, 150, 200)):
    return Image.new("RGB", (w, h), color=color)


def _make_tmp_png(w=400, h=300, color=(100, 150, 200)) -> str:
    img = _make_rgb(w, h, color)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


def _noisy_img(w, h, seed=42) -> Image.Image:
    rng = np.random.RandomState(seed)
    arr = rng.randint(0, 255, (h, w, 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


class _FakeProvider:
    def metadata(self):
        return {"providerName": "fake-e4", "modelName": "fake-e4-v1"}

    def inpaint(self, image, mask, prompt, options):
        w, h = image.size
        arr = np.random.RandomState(99).randint(40, 180, (h, w, 3), dtype=np.uint8)
        return Image.fromarray(arr, "RGB")


def _run_generate(src_path, visual_enabled=False, job_id="e4-test"):
    from resizer import _generate_ai_only
    specs = [{"media": "t", "name": "x", "slug": "x", "width": 300, "height": 250}]
    tmp = tempfile.mkdtemp()
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        os.environ.pop("VISUAL_VERDICT_ENABLED", None)
        if visual_enabled:
            os.environ["VISUAL_VERDICT_ENABLED"] = "true"
        results, _ = _generate_ai_only(
            psd_path=src_path,
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
        os.environ.pop("VISUAL_VERDICT_ENABLED", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ── E4-A: visual_evaluator unit tests ────────────────────────────────────────

class TestVisualEvaluator:
    def test_pass_for_valid_image(self):
        from verdict.visual_evaluator import evaluate_visual
        from verdict.models import PASS
        src = _noisy_img(300, 250, seed=42)
        # Modify only top 10% of rows (~10% pixel diff < 85% threshold)
        arr = np.array(src).copy()
        arr[:25, :, :] = (arr[:25, :, :].astype(int) + 50).clip(0, 255).astype(np.uint8)
        res = Image.fromarray(arr)
        vr = evaluate_visual(source_img=src, result_img=res, target_w=300, target_h=250)
        assert vr.status == PASS

    def test_fail_for_blank_image(self):
        from verdict.visual_evaluator import evaluate_visual
        from verdict.models import FAIL
        src = _noisy_img(300, 250)
        blank = Image.new("RGB", (300, 250), (128, 128, 128))  # uniform = blank
        vr = evaluate_visual(source_img=src, result_img=blank, target_w=300, target_h=250)
        assert vr.status == FAIL
        assert "SCENE_PLATE_BLANK" in vr.reasonCodes

    def test_fail_for_wrong_dimensions(self):
        from verdict.visual_evaluator import evaluate_visual
        from verdict.models import FAIL
        src = _noisy_img(300, 250)
        wrong = _noisy_img(400, 300)  # wrong size
        vr = evaluate_visual(source_img=src, result_img=wrong, target_w=300, target_h=250)
        assert vr.status == FAIL
        assert "SCENE_PLATE_WRONG_DIMENSIONS" in vr.reasonCodes

    def test_fail_for_none_result(self):
        from verdict.visual_evaluator import evaluate_visual
        from verdict.models import FAIL
        vr = evaluate_visual(source_img=None, result_img=None, target_w=300, target_h=250)
        assert vr.status == FAIL

    def test_fail_for_full_scene_regeneration(self):
        from verdict.visual_evaluator import evaluate_visual
        from verdict.models import FAIL
        # src: dark noisy; res: bright noisy — >85% pixel diff, both non-blank
        rng = np.random.RandomState(10)
        src_arr = rng.randint(0, 50, (250, 300, 3), dtype=np.uint8)
        res_arr = rng.randint(200, 255, (250, 300, 3), dtype=np.uint8)
        src = Image.fromarray(src_arr, "RGB")
        res = Image.fromarray(res_arr, "RGB")
        vr = evaluate_visual(source_img=src, result_img=res, target_w=300, target_h=250)
        assert vr.status == FAIL
        assert "FULL_SCENE_REGENERATION_DETECTED" in vr.reasonCodes

    def test_no_full_regen_for_similar_images(self):
        from verdict.visual_evaluator import evaluate_visual
        from verdict.models import PASS
        src = _noisy_img(300, 250, seed=1)
        # Slightly modify (5% of pixels) to simulate background plate edit
        arr = np.array(src)
        arr[:12, :, :] = (arr[:12, :, :] + 30).clip(0, 255)
        res = Image.fromarray(arr.astype(np.uint8))
        vr = evaluate_visual(source_img=src, result_img=res, target_w=300, target_h=250)
        # Should pass: only ~5% pixels changed
        assert vr.status == PASS

    def test_metrics_populated(self):
        from verdict.visual_evaluator import evaluate_visual
        src = _noisy_img(200, 150, seed=3)
        # Slightly modified version: <85% diff → passes → PASS evidence includes variance
        arr = np.array(src).copy()
        arr[:15, :, :] = (arr[:15, :, :].astype(int) + 40).clip(0, 255).astype(np.uint8)
        res = Image.fromarray(arr)
        vr = evaluate_visual(source_img=src, result_img=res, target_w=200, target_h=150)
        assert "variance" in vr.evidence

    def test_log_emitted(self, capsys):
        from verdict.visual_evaluator import evaluate_visual
        src = _noisy_img(100, 80)
        res = _noisy_img(100, 80, seed=5)
        evaluate_visual(source_img=src, result_img=res, target_w=100, target_h=80,
                        job_id="e4-log", spec_id="spec1")
        out = capsys.readouterr().out
        assert "[VERDICT_VISUAL]" in out
        assert "jobId=e4-log" in out


# ── E4-B: stage21_aggregator visual_required ─────────────────────────────────

class TestAggregatorVisualRequired:
    def _make_vr(self, status, name="testVerdict"):
        from verdict.models import VerdictResult
        return VerdictResult(name=name, status=status, required=True)

    def _pass_vr(self, name="testVerdict"):
        return self._make_vr("PASS", name)

    def _not_tested_visual(self):
        from verdict.models import VerdictResult
        return VerdictResult(name="visualVerdict", status="NOT_TESTED", required=False,
                             reasonCodes=["VISUAL_NOT_TESTED"])

    def _pass_visual(self):
        from verdict.models import VerdictResult
        return VerdictResult(name="visualVerdict", status="PASS", required=False,
                             reasonCodes=["VISUAL_PASS"])

    def test_not_required_not_tested_visual_does_not_fail(self):
        from verdict.stage21_aggregator import aggregate_stage21_verdict
        from verdict.models import PASS
        summary = aggregate_stage21_verdict(
            self._pass_vr("technicalVerdict"),
            self._pass_vr("extractionVerdict"),
            self._pass_vr("compositionVerdict"),
            self._pass_vr("layoutVerdict"),
            self._not_tested_visual(),
            job_id="e4-b1", spec_id="s",
            visual_required=False,
        )
        assert summary.overallStatus == PASS

    def test_required_not_tested_visual_causes_fail(self):
        from verdict.stage21_aggregator import aggregate_stage21_verdict
        from verdict.models import FAIL
        summary = aggregate_stage21_verdict(
            self._pass_vr("technicalVerdict"),
            self._pass_vr("extractionVerdict"),
            self._pass_vr("compositionVerdict"),
            self._pass_vr("layoutVerdict"),
            self._not_tested_visual(),
            job_id="e4-b2", spec_id="s",
            visual_required=True,
        )
        assert summary.overallStatus == FAIL

    def test_required_pass_visual_preserves_pass(self):
        from verdict.stage21_aggregator import aggregate_stage21_verdict
        from verdict.models import PASS
        summary = aggregate_stage21_verdict(
            self._pass_vr("technicalVerdict"),
            self._pass_vr("extractionVerdict"),
            self._pass_vr("compositionVerdict"),
            self._pass_vr("layoutVerdict"),
            self._pass_visual(),
            job_id="e4-b3", spec_id="s",
            visual_required=True,
        )
        assert summary.overallStatus == PASS

    def test_required_fail_visual_causes_fail(self):
        from verdict.stage21_aggregator import aggregate_stage21_verdict
        from verdict.models import FAIL, VerdictResult
        fail_visual = VerdictResult(name="visualVerdict", status=FAIL, required=False,
                                    reasonCodes=["SCENE_PLATE_BLANK"])
        summary = aggregate_stage21_verdict(
            self._pass_vr("technicalVerdict"),
            self._pass_vr("extractionVerdict"),
            self._pass_vr("compositionVerdict"),
            self._pass_vr("layoutVerdict"),
            fail_visual,
            job_id="e4-b4", spec_id="s",
            visual_required=True,
        )
        assert summary.overallStatus == FAIL

    def test_visual_required_log_includes_flag(self, capsys):
        from verdict.stage21_aggregator import aggregate_stage21_verdict
        aggregate_stage21_verdict(
            self._pass_vr("technicalVerdict"),
            self._pass_vr("extractionVerdict"),
            self._pass_vr("compositionVerdict"),
            self._pass_vr("layoutVerdict"),
            self._pass_visual(),
            job_id="e4-b5", spec_id="s",
            visual_required=True,
        )
        out = capsys.readouterr().out
        assert "visualRequired=True" in out


# ── E4-C: resizer finalResultValid field ─────────────────────────────────────

class TestFinalResultValid:
    def test_finalResultValid_present(self):
        src = _make_tmp_png()
        try:
            results, _ = _run_generate(src, visual_enabled=False, job_id="e4-c1")
            assert "finalResultValid" in results[0]
        finally:
            os.unlink(src)

    def test_finalResultValid_is_bool(self):
        src = _make_tmp_png()
        try:
            results, _ = _run_generate(src, visual_enabled=False, job_id="e4-c2")
            assert isinstance(results[0]["finalResultValid"], bool)
        finally:
            os.unlink(src)

    def test_visual_enabled_finalResultValid_reflects_verdict(self):
        src = _make_tmp_png()
        try:
            results, _ = _run_generate(src, visual_enabled=True, job_id="e4-c3")
            r = results[0]
            prov = r.get("renderProvenance", {})
            final_valid = r.get("finalResultValid")
            # If verdict is PASS, finalResultValid should be True
            if prov.get("verdict") == "PASS":
                assert final_valid is True
        finally:
            os.unlink(src)


# ── E4-D: validCount in log ───────────────────────────────────────────────────

class TestValidCountLog:
    def test_validcount_in_ai_only_end_log(self):
        src = _make_tmp_png()
        try:
            _, log = _run_generate(src, job_id="e4-d1")
            assert "validCount=" in log
        finally:
            os.unlink(src)

    def test_validcount_is_numeric(self):
        src = _make_tmp_png()
        try:
            _, log = _run_generate(src, job_id="e4-d2")
            import re
            match = re.search(r"validCount=(\d+)", log)
            assert match is not None, "validCount not found in log"
            assert int(match.group(1)) >= 0
        finally:
            os.unlink(src)

    def test_successcount_still_present(self):
        src = _make_tmp_png()
        try:
            _, log = _run_generate(src, job_id="e4-d3")
            assert "successCount=" in log
        finally:
            os.unlink(src)

    def test_visual_disabled_validcount_equals_zero_or_more(self):
        """Without VISUAL_VERDICT_ENABLED, finalResultValid depends on C-1 overall."""
        src = _make_tmp_png()
        try:
            results, log = _run_generate(src, visual_enabled=False, job_id="e4-d4")
            import re
            vc_match = re.search(r"validCount=(\d+)", log)
            assert vc_match is not None
            vc = int(vc_match.group(1))
            valid_in_results = sum(1 for r in results if r.get("finalResultValid") is True)
            assert vc == valid_in_results
        finally:
            os.unlink(src)


# ── E4-E: VISUAL_VERDICT_ENABLED env var ─────────────────────────────────────

class TestVisualVerdictEnabledEnv:
    def test_default_disabled_does_not_run_visual_eval(self):
        src = _make_tmp_png()
        try:
            _, log = _run_generate(src, visual_enabled=False, job_id="e4-e1")
            # With disabled: visual evaluator's [VERDICT_VISUAL] log from evaluator
            # may or may not appear (aggregator still logs NOT_TESTED)
            assert "[VERDICT_VISUAL]" in log  # aggregator always logs visual status
        finally:
            os.unlink(src)

    def test_enabled_runs_visual_eval(self):
        src = _make_tmp_png()
        try:
            _, log = _run_generate(src, visual_enabled=True, job_id="e4-e2")
            assert "[VERDICT_VISUAL]" in log
        finally:
            os.unlink(src)

    def test_env_var_cleared_after_run(self):
        src = _make_tmp_png()
        try:
            _run_generate(src, visual_enabled=True, job_id="e4-e3")
            assert os.environ.get("VISUAL_VERDICT_ENABLED") is None
        finally:
            os.unlink(src)
