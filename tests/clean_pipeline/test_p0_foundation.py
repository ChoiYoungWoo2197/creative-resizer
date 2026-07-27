"""P0 foundation: 3 tests only. contracts + boundary + logger flush."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from worker.clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from worker.clean_pipeline.pipeline_logger import PipelineLogger

_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


def _load_boundary_checker():
    spec = importlib.util.spec_from_file_location(
        "check_clean_pipeline_boundary",
        _SCRIPTS_DIR / "check_clean_pipeline_boundary.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Test 1: FAIL without reason is a contract violation ───────────────────────


def test_fail_stage_result_without_reason_raises():
    with pytest.raises(ValueError, match="at least one reason"):
        StageResult(
            stage=StageName.SOURCE_PREPARATION,
            status=PipelineStatus.FAIL,
            reasons=[],
        )


# ── Test 2: Boundary check detects forbidden import ───────────────────────────


def test_boundary_check_fails_on_forbidden_string(tmp_path):
    checker = _load_boundary_checker()
    bad = tmp_path / "contaminated.py"
    bad.write_text("import worker.resizer\n", encoding="utf-8")
    violations = checker.check_file(bad)
    assert len(violations) > 0, "Expected at least one violation for 'worker.resizer'"
    assert any("worker.resizer" in v[1] for v in violations)


# ── Test 3: Logger flushes immediately (readable before close) ─────────────────


def test_jsonl_log_flushed_immediately(tmp_path):
    log = tmp_path / "pipeline.jsonl"
    lg = PipelineLogger("job_p0", log)
    lg.stage_start("SOURCE_PREPARATION", "loading source")

    # buffering=1 → line-buffered; file has content before close()
    content = log.read_text(encoding="utf-8")
    assert "stage_start" in content

    rec = json.loads(content.strip())
    assert rec["pipelineVersion"] == "clean_v1"
    assert "+" in rec["timestamp"] or rec["timestamp"].endswith("Z")

    lg.close()
