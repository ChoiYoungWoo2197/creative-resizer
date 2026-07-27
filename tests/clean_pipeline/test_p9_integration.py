"""P9 integration tests: clean_pipeline bridge adapters.

Tests verify:
  1. adapt_request: /generate JSON → CleanPipelineRequest (spec conversion, safeZone)
  2. adapt_response PASS: PASS result → valid=True, renderSource=clean_pipeline, fallbackUsed=False
  3. adapt_response FAIL: FAIL result → valid=False, failedStage/failureCode populated, slug in missing
  4. adapt_response empty specs: graceful fallback slug
"""
from __future__ import annotations

import pytest

from worker.clean_pipeline.bridge.request_adapter import adapt_request
from worker.clean_pipeline.bridge.response_adapter import adapt_response
from worker.clean_pipeline.contracts import (
    CleanPipelineRequest,
    CleanPipelineResult,
    PipelineStatus,
    StageName,
    StageResult,
    TargetSpec,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _pass_result(job_id: str, file_path: str) -> CleanPipelineResult:
    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.PASS,
        output_paths=[file_path],
    )


def _fail_result(job_id: str, stage: StageName, code: str, msg: str) -> CleanPipelineResult:
    failed_sr = StageResult(
        stage=stage,
        status=PipelineStatus.FAIL,
        reasons=[code],
    )
    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.FAIL,
        stage_results=[failed_sr],
        failure_code=code,
        failure_message=msg,
    )


_SPECS_RAW = [
    {"width": 300, "height": 250, "media": "naver", "slug": "naver_300x250", "name": "Test"}
]


# ── Test 1: adapt_request basic ────────────────────────────────────────────────


def test_adapt_request_basic(tmp_path):
    """Spec width/height and psdPath are correctly mapped to CleanPipelineRequest."""
    source = tmp_path / "ad.png"
    source.write_bytes(b"fake")

    data = {
        "psdPath": str(source),
        "specs": _SPECS_RAW,
        "jobId": "job_basic",
        "pipelineVersion": "clean_v1",
        "resizeMode": "smart-fit",          # must be ignored
        "smartFitStrength": "balanced",     # must be ignored
        "focalPosition": "top-left",        # must be ignored
    }

    req = adapt_request(data, "job_basic", str(tmp_path / "out"))

    assert isinstance(req, CleanPipelineRequest)
    assert req.source_path == str(source)
    assert req.job_id == "job_basic"
    assert len(req.target_specs) == 1
    spec = req.target_specs[0]
    assert spec.width == 300
    assert spec.height == 250
    assert spec.safe_top == 0
    assert spec.safe_left == 0


# ── Test 2: adapt_request with nested safeZone dict ───────────────────────────


def test_adapt_request_safe_zone(tmp_path):
    """safeZone dict is unpacked into safe_top/right/bottom/left."""
    source = tmp_path / "ad.png"
    source.write_bytes(b"fake")

    specs = [{
        "width": 640,
        "height": 480,
        "slug": "kakao_640x480",
        "safeZone": {"top": 10, "right": 20, "bottom": 15, "left": 5},
    }]

    data = {"psdPath": str(source), "specs": specs}
    req = adapt_request(data, "job_sz", str(tmp_path / "out"))

    s = req.target_specs[0]
    assert s.safe_top == 10
    assert s.safe_right == 20
    assert s.safe_bottom == 15
    assert s.safe_left == 5


# ── Test 3: adapt_response PASS ───────────────────────────────────────────────


def test_adapt_response_pass(tmp_path):
    """PASS result → valid=True, clean_pipeline fields set, no missing slugs."""
    result_png = str(tmp_path / "result.png")

    result_items, missing = adapt_response(
        _pass_result("job_ok", result_png), _SPECS_RAW
    )

    assert len(result_items) == 1
    item = result_items[0]
    assert item["valid"] is True
    assert item["filePath"] == result_png
    assert item["renderSource"] == "clean_pipeline"
    assert item["fallbackUsed"] is False
    assert item["pipelineVersion"] == "clean_v1"
    assert missing == []


# ── Test 4: adapt_response FAIL ───────────────────────────────────────────────


def test_adapt_response_fail():
    """FAIL result → valid=False, failedStage/failureCode populated, slug in missing."""
    result_items, missing = adapt_response(
        _fail_result(
            "job_fail",
            StageName.SCENE_VALIDATION,
            "AD_OBJECTS_REMAINING",
            "Scene validation failed: objects still visible",
        ),
        _SPECS_RAW,
    )

    assert len(result_items) == 1
    item = result_items[0]
    assert item["valid"] is False
    assert item["filePath"] == ""
    assert item["renderSource"] == "clean_pipeline"
    assert item["fallbackUsed"] is False
    assert item["pipelineVersion"] == "clean_v1"
    assert item["failedStage"] == "SCENE_VALIDATION"
    assert item["failureCode"] == "AD_OBJECTS_REMAINING"
    assert "naver_300x250" in missing
