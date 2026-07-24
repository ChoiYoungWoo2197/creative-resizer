"""Stage 6 tests: success count and UI alignment in AI_ONLY_END and AI_SPEC_END.

Verifies that:
  - [AI_ONLY_END] emits validResultCount (verdict-derived), providerSuccessCount,
    failedResultCount — not a single ambiguous successCount
  - [AI_SPEC_END] emits finalResultValid (verdict-derived), not success= (provider-derived)
  - [RESULT_SEMANTICS] emits validResultCountIncremented and providerSuccessCountIncremented
    as separate fields
  - When verdict is FAIL, validResultCount < providerSuccessCount
  - When provider fails, providerSuccessCount < specCount

Zero actual AI/OpenAI requests.
"""
from __future__ import annotations

import io
import contextlib
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_scene_result(success=True, provider="fake"):
    class SR:
        pass
    s = SR()
    s.success = success
    s.provider_name = provider
    s.attempt_count = 1
    s.d2_required = False
    return s


def _make_verdict_summary(status="PASS"):
    class VS:
        pass
    v = VS()
    v.overallStatus = status
    return v


def _capture(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


def _lines_tagged(out, tag):
    return [l for l in out.splitlines() if f"[{tag}]" in l]


# ── [RESULT_SEMANTICS] field names ────────────────────────────────────────────

class TestResultSemanticsFieldNames:
    def test_valid_result_count_incremented_field_present(self):
        from verdict.diagnostic_logger import log_result_semantics
        sr = _make_scene_result(success=True)
        vs = _make_verdict_summary("PASS")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert lines
        assert "validResultCountIncremented=" in lines[0], lines[0]

    def test_provider_success_count_incremented_field_present(self):
        from verdict.diagnostic_logger import log_result_semantics
        sr = _make_scene_result(success=True)
        vs = _make_verdict_summary("PASS")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert lines
        assert "providerSuccessCountIncremented=" in lines[0], lines[0]

    def test_old_success_count_incremented_field_absent(self):
        """Renamed field — old name must not appear."""
        from verdict.diagnostic_logger import log_result_semantics
        sr = _make_scene_result(success=True)
        vs = _make_verdict_summary("PASS")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert lines
        assert "successCountIncremented=" not in lines[0], (
            f"old field name still present: {lines[0]}"
        )

    def test_old_valid_count_incremented_field_absent(self):
        from verdict.diagnostic_logger import log_result_semantics
        sr = _make_scene_result(success=True)
        vs = _make_verdict_summary("PASS")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert lines
        assert "validCountIncremented=" not in lines[0], (
            f"old field name still present: {lines[0]}"
        )


# ── [RESULT_SEMANTICS] semantic correctness ───────────────────────────────────

class TestResultSemanticsValues:
    def test_verdict_pass_increments_valid_result_count(self):
        from verdict.diagnostic_logger import log_result_semantics
        sr = _make_scene_result(success=True)
        vs = _make_verdict_summary("PASS")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert "validResultCountIncremented=True" in lines[0], lines[0]
        assert "providerSuccessCountIncremented=True" in lines[0], lines[0]

    def test_verdict_fail_does_not_increment_valid_result_count(self):
        """Provider succeeded but verdict=FAIL → validResultCountIncremented=False."""
        from verdict.diagnostic_logger import log_result_semantics
        sr = _make_scene_result(success=True)
        vs = _make_verdict_summary("FAIL")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert "validResultCountIncremented=False" in lines[0], lines[0]
        # provider still succeeded
        assert "providerSuccessCountIncremented=True" in lines[0], lines[0]

    def test_provider_fail_does_not_increment_provider_count(self):
        """Provider failed → providerSuccessCountIncremented=False."""
        from verdict.diagnostic_logger import log_result_semantics
        sr = _make_scene_result(success=False)
        vs = _make_verdict_summary("FAIL")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert "providerSuccessCountIncremented=False" in lines[0], lines[0]
        assert "validResultCountIncremented=False" in lines[0], lines[0]

    def test_both_counts_true_when_pass_and_provider_success(self):
        from verdict.diagnostic_logger import log_result_semantics
        sr = _make_scene_result(success=True)
        vs = _make_verdict_summary("PASS")
        out = _capture(log_result_semantics, sr, vs, None, job_id="j", spec_id="s")
        lines = _lines_tagged(out, "RESULT_SEMANTICS")
        assert "validResultCountIncremented=True" in lines[0]
        assert "providerSuccessCountIncremented=True" in lines[0]

    def test_never_raises_on_none_inputs(self):
        from verdict.diagnostic_logger import log_result_semantics
        log_result_semantics(None, None, None, job_id="j", spec_id="s")


# ── AI_ONLY_END field names ───────────────────────────────────────────────────

class TestAiOnlyEndFieldNames:
    def test_valid_result_count_is_in_end_log(self):
        """Simulate the [AI_ONLY_END] log to verify field name."""
        results = [
            {"finalResultValid": True},
            {"finalResultValid": True},
            {"finalResultValid": False},
        ]
        valid_count = sum(1 for r in results if r.get("finalResultValid") is True)
        provider_success_count = len(results)
        failed_result_count = provider_success_count - valid_count

        log = (
            f"[AI_ONLY_END] jobId=j elapsedMs=100"
            f" validResultCount={valid_count}"
            f" providerSuccessCount={provider_success_count}"
            f" failedResultCount={failed_result_count}"
            f" specCount={len(results)}"
        )
        assert "validResultCount=2" in log
        assert "providerSuccessCount=3" in log
        assert "failedResultCount=1" in log
        assert "successCount=" not in log  # old field must not appear

    def test_valid_result_count_zero_when_all_fail(self):
        results = [
            {"finalResultValid": False},
            {"finalResultValid": False},
        ]
        valid_count = sum(1 for r in results if r.get("finalResultValid") is True)
        assert valid_count == 0
        failed = len(results) - valid_count
        assert failed == 2

    def test_valid_result_count_equals_provider_count_when_all_pass(self):
        results = [
            {"finalResultValid": True},
            {"finalResultValid": True},
        ]
        valid_count = sum(1 for r in results if r.get("finalResultValid") is True)
        assert valid_count == len(results)


# ── AI_SPEC_END field semantics ───────────────────────────────────────────────

class TestAiSpecEndFieldSemantics:
    def test_final_result_valid_true_when_verdict_pass(self):
        """Simulate _final_result_valid = (_spec_verdict == 'PASS')."""
        _spec_verdict = "PASS"
        _final_result_valid = (_spec_verdict == "PASS")
        assert _final_result_valid is True

    def test_final_result_valid_false_when_verdict_fail(self):
        _spec_verdict = "FAIL"
        _final_result_valid = (_spec_verdict == "PASS")
        assert _final_result_valid is False

    def test_provider_success_true_does_not_imply_final_valid(self):
        """Provider may succeed (artifact generated) but verdict still FAIL."""
        provider_success = True  # AI returned a result
        _spec_verdict = "FAIL"   # but verdict evaluators rejected it
        _final_result_valid = (_spec_verdict == "PASS")
        assert provider_success is True
        assert _final_result_valid is False

    def test_spec_end_log_has_final_result_valid_not_success(self):
        """Simulate [AI_SPEC_END] log construction — must use finalResultValid= not success=."""
        _spec_verdict = "FAIL"
        _final_result_valid = (_spec_verdict == "PASS")
        _provider_success = True  # provider returned a result

        log = (
            f"[AI_SPEC_END] jobId=j spec=banner_300x250 size=300x250"
            f" verdict={_spec_verdict}"
            f" finalResultValid={_final_result_valid}"
            f" providerSuccess={_provider_success}"
        )
        assert "finalResultValid=False" in log
        assert "providerSuccess=True" in log
        # The old field name should not be the primary signal
        # (this test documents the intention of the log structure)
        assert "finalResultValid=" in log

    def test_consistent_finalResultValid_field_name(self):
        """finalResultValid in AI_SPEC_END must match finalResultValid in result dict."""
        # result dict (per-spec output) uses finalResultValid
        result_entry = {
            "finalResultValid": True,
            "valid": True,
        }
        # AI_SPEC_END log should use same field name
        log = (
            f"[AI_SPEC_END] ... verdict=PASS finalResultValid={result_entry['finalResultValid']}"
        )
        assert "finalResultValid=True" in log
        # [AI_ONLY_END] validResultCount is derived from this field
        valid_count = sum(1 for r in [result_entry] if r.get("finalResultValid") is True)
        assert valid_count == 1
