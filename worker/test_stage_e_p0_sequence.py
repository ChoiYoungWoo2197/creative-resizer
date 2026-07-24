"""Stage E P0-A: Pipeline sequence enforcement tests.

Verifies:
  1. PipelineSequenceTracker records sizes correctly
  2. validate_sequence() PASS when all sizes match canonical
  3. validate_sequence() FAIL when analysis/extraction size != canonical
  4. validate_sequence() FAIL when stages not recorded
  5. Target transform recorded after analysis/extraction
  6. build_provenance_fields() returns correct contract fields
  7. _generate_ai_only provenance includes P0-A fields
  8. foregroundExtractionSource == "canonical_original"
  9. log_pipeline_sequence emits [PIPELINE_SEQUENCE]
 10. Analysis/extraction sizes == canonical for PNG/JPG inputs

All tests: ACTUAL_OPENAI_REQUESTS=0
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

def _make_tmp_png(w=400, h=300) -> str:
    img = Image.new("RGB", (w, h), color=(80, 120, 160))
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


class _FakeProvider:
    def metadata(self):
        return {"providerName": "fake-p0a", "modelName": "fake-p0a-v1"}

    def inpaint(self, image, mask, prompt, options):
        w, h = image.size
        arr = np.random.RandomState(42).randint(40, 180, (h, w, 3), dtype=np.uint8)
        return Image.fromarray(arr, "RGB")


def _run(src_path, source_type="image", job_id="p0a-test"):
    from resizer import _generate_ai_only
    specs = [{"media": "t", "name": "x", "slug": "x", "width": 300, "height": 250}]
    tmp = tempfile.mkdtemp()
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        os.environ.pop("VISUAL_VERDICT_ENABLED", None)
        os.environ.pop("PSD_LAYER_HINTS_ENABLED", None)
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


# ── P0-A-1: Unit tests for PipelineSequenceTracker ──────────────────────────

class TestPipelineSequenceTracker:
    def test_record_canonical_sets_size(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        tracker = PipelineSequenceTracker()
        tracker.record_canonical(400, 300)
        assert tracker.canonical_size == (400, 300)

    def test_record_semantic_analysis_sets_flag(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        tracker = PipelineSequenceTracker()
        tracker.record_canonical(400, 300)
        tracker.record_semantic_analysis(400, 300)
        assert tracker._semantic_analysis_recorded is True
        assert tracker.semantic_analysis_size == (400, 300)

    def test_record_foreground_extraction_sets_flag(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        tracker = PipelineSequenceTracker()
        tracker.record_canonical(400, 300)
        tracker.record_foreground_extraction(400, 300)
        assert tracker._foreground_extraction_recorded is True
        assert tracker.foreground_extraction_size == (400, 300)

    def test_record_target_transform_sets_flag(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        tracker = PipelineSequenceTracker()
        tracker.record_target_transform(300, 250)
        assert tracker._target_transform_applied is True
        assert tracker.target_size == (300, 250)

    def test_default_extraction_source_is_canonical(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        tracker = PipelineSequenceTracker()
        assert tracker.foreground_extraction_source == "canonical_original"


# ── P0-A-2: validate_sequence pass/fail ──────────────────────────────────────

class TestValidateSequence:
    def _valid_tracker(self, cw=400, ch=300, tw=300, th=250):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        t = PipelineSequenceTracker()
        t.record_canonical(cw, ch)
        t.record_semantic_analysis(cw, ch)
        t.record_foreground_extraction(cw, ch)
        return t

    def test_valid_sequence_no_violations(self):
        t = self._valid_tracker()
        violations = t.validate_sequence()
        assert violations == [], f"Expected no violations, got: {violations}"

    def test_analysis_size_mismatch_is_violation(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(300, 250)  # wrong size — target size used
        t.record_foreground_extraction(400, 300)
        violations = t.validate_sequence()
        assert any("ANALYSIS_SIZE_MISMATCH" in v for v in violations)

    def test_extraction_size_mismatch_is_violation(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(400, 300)
        t.record_foreground_extraction(300, 250)  # wrong size
        violations = t.validate_sequence()
        assert any("EXTRACTION_SIZE_MISMATCH" in v for v in violations)

    def test_no_canonical_is_violation(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        t = PipelineSequenceTracker()
        violations = t.validate_sequence()
        assert any("CANONICAL_SIZE_NOT_RECORDED" in v for v in violations)

    def test_analysis_not_recorded_is_violation(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_foreground_extraction(400, 300)
        violations = t.validate_sequence()
        assert any("SEMANTIC_ANALYSIS_NOT_RECORDED" in v for v in violations)

    def test_extraction_not_recorded_is_violation(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(400, 300)
        violations = t.validate_sequence()
        assert any("FOREGROUND_EXTRACTION_NOT_RECORDED" in v for v in violations)

    def test_wrong_extraction_source_is_violation(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(400, 300)
        t.record_foreground_extraction(400, 300)
        t.foreground_extraction_source = "scene_plate"  # invalid
        violations = t.validate_sequence()
        assert any("EXTRACTION_SOURCE_INVALID" in v for v in violations)


# ── P0-A-3: validate_sequence helper function ────────────────────────────────

class TestValidateSequenceHelper:
    def test_returns_true_for_valid(self):
        from scene_cleanup.pipeline_sequence import (
            PipelineSequenceTracker, validate_sequence
        )
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(400, 300)
        t.record_foreground_extraction(400, 300)
        passed, violations = validate_sequence(t)
        assert passed is True
        assert violations == []

    def test_returns_false_for_invalid(self):
        from scene_cleanup.pipeline_sequence import (
            PipelineSequenceTracker, validate_sequence
        )
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        # No analysis recorded
        t.record_foreground_extraction(400, 300)
        passed, violations = validate_sequence(t)
        assert passed is False
        assert len(violations) > 0


# ── P0-A-4: build_provenance_fields ─────────────────────────────────────────

class TestBuildProvenanceFields:
    def _make_valid_tracker(self):
        from scene_cleanup.pipeline_sequence import PipelineSequenceTracker
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(400, 300)
        t.record_foreground_extraction(400, 300)
        return t

    def test_extraction_source_field(self):
        from scene_cleanup.pipeline_sequence import build_provenance_fields
        t = self._make_valid_tracker()
        prov = build_provenance_fields(t)
        assert prov["foregroundExtractionSource"] == "canonical_original"

    def test_analysis_size_field(self):
        from scene_cleanup.pipeline_sequence import build_provenance_fields
        t = self._make_valid_tracker()
        prov = build_provenance_fields(t)
        assert prov["semanticAnalysisSourceSize"] == "400x300"

    def test_extraction_size_field(self):
        from scene_cleanup.pipeline_sequence import build_provenance_fields
        t = self._make_valid_tracker()
        prov = build_provenance_fields(t)
        assert prov["foregroundExtractionSourceSize"] == "400x300"

    def test_pipeline_sequence_valid_true(self):
        from scene_cleanup.pipeline_sequence import build_provenance_fields
        t = self._make_valid_tracker()
        prov = build_provenance_fields(t)
        assert prov["pipelineSequenceValid"] is True

    def test_pipeline_sequence_valid_false_on_violation(self):
        from scene_cleanup.pipeline_sequence import (
            PipelineSequenceTracker, build_provenance_fields
        )
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        # Analysis with wrong size
        t.record_semantic_analysis(300, 250)
        t.record_foreground_extraction(400, 300)
        prov = build_provenance_fields(t)
        assert prov["pipelineSequenceValid"] is False

    def test_analysis_before_target_transform_true(self):
        from scene_cleanup.pipeline_sequence import build_provenance_fields
        t = self._make_valid_tracker()
        # Target NOT yet recorded → analysisBeforeTargetTransform = True
        prov = build_provenance_fields(t)
        assert prov["analysisBeforeTargetTransform"] is True

    def test_extraction_before_target_transform_true(self):
        from scene_cleanup.pipeline_sequence import build_provenance_fields
        t = self._make_valid_tracker()
        prov = build_provenance_fields(t)
        assert prov["extractionBeforeTargetTransform"] is True


# ── P0-A-5: log_pipeline_sequence emits correct log ─────────────────────────

class TestLogPipelineSequence:
    def test_log_emitted(self, capsys):
        from scene_cleanup.pipeline_sequence import (
            PipelineSequenceTracker, log_pipeline_sequence
        )
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(400, 300)
        t.record_foreground_extraction(400, 300)
        log_pipeline_sequence(t, job_id="p0a-log-test")
        out = capsys.readouterr().out
        assert "[PIPELINE_SEQUENCE]" in out
        assert "jobId=p0a-log-test" in out
        assert "passed=True" in out

    def test_log_shows_violation_on_fail(self, capsys):
        from scene_cleanup.pipeline_sequence import (
            PipelineSequenceTracker, log_pipeline_sequence
        )
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(300, 250)  # wrong
        t.record_foreground_extraction(400, 300)
        log_pipeline_sequence(t, job_id="p0a-fail-log")
        out = capsys.readouterr().out
        assert "[PIPELINE_SEQUENCE]" in out
        assert "passed=False" in out
        assert "ANALYSIS_SIZE_MISMATCH" in out

    def test_log_extraction_source(self, capsys):
        from scene_cleanup.pipeline_sequence import (
            PipelineSequenceTracker, log_pipeline_sequence
        )
        t = PipelineSequenceTracker()
        t.record_canonical(400, 300)
        t.record_semantic_analysis(400, 300)
        t.record_foreground_extraction(400, 300)
        log_pipeline_sequence(t, job_id="p0a-src-log")
        out = capsys.readouterr().out
        assert "foregroundExtractionSource='canonical_original'" in out


# ── P0-A-6: resizer _generate_ai_only provenance contract ───────────────────

class TestGenerateAiOnlyProvenance:
    def test_p0a_pipeline_sequence_log_in_output(self):
        src = _make_tmp_png()
        try:
            results, log = _run(src, job_id="p0a-r1")
            assert "[PIPELINE_SEQUENCE]" in log, "Missing [PIPELINE_SEQUENCE] log"
        finally:
            os.unlink(src)

    def test_foreground_extraction_source_canonical(self):
        src = _make_tmp_png()
        try:
            results, _ = _run(src, job_id="p0a-r2")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("foregroundExtractionSource") == "canonical_original"
        finally:
            os.unlink(src)

    def test_analysis_before_target_transform(self):
        src = _make_tmp_png()
        try:
            results, _ = _run(src, job_id="p0a-r3")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("analysisBeforeTargetTransform") is True
        finally:
            os.unlink(src)

    def test_extraction_before_target_transform(self):
        src = _make_tmp_png()
        try:
            results, _ = _run(src, job_id="p0a-r4")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("extractionBeforeTargetTransform") is True
        finally:
            os.unlink(src)

    def test_pipeline_sequence_valid_in_provenance(self):
        src = _make_tmp_png()
        try:
            results, _ = _run(src, job_id="p0a-r5")
            prov = results[0].get("renderProvenance", {})
            assert prov.get("pipelineSequenceValid") is True
        finally:
            os.unlink(src)

    def test_semantic_analysis_source_size_matches_canonical(self):
        w, h = 400, 300
        src = _make_tmp_png(w=w, h=h)
        try:
            results, _ = _run(src, job_id="p0a-r6")
            prov = results[0].get("renderProvenance", {})
            analysis_size = prov.get("semanticAnalysisSourceSize", "")
            assert analysis_size == f"{w}x{h}", (
                f"Expected {w}x{h} got {analysis_size}"
            )
        finally:
            os.unlink(src)

    def test_foreground_extraction_source_size_matches_canonical(self):
        w, h = 400, 300
        src = _make_tmp_png(w=w, h=h)
        try:
            results, _ = _run(src, job_id="p0a-r7")
            prov = results[0].get("renderProvenance", {})
            extraction_size = prov.get("foregroundExtractionSourceSize", "")
            assert extraction_size == f"{w}x{h}", (
                f"Expected {w}x{h} got {extraction_size}"
            )
        finally:
            os.unlink(src)
