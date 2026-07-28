"""E2E bridge contract tests.

Verifies the full round-trip contract from raw request JSON → adapt_request →
orchestrate → adapt_response, covering:

1. jobId round-trip: result.job_id == request.job_id
2. PASS: output_paths[0] exists on disk
3. PASS adapt_response: fileName=basename(path), fileSize>0, valid=True
4. FAIL adapt_response: valid=False, filePath="", fileName=""
5. width/height from spec carried through to adapt_response item
6. slug/media/name from spec propagated correctly
7. pipelineVersion always "clean_v1" on both PASS and FAIL
8. fallbackUsed always False on both PASS and FAIL
9. FAIL item: failedStage, failureCode, error are populated
10. missing_ratio_types contains slug on FAIL, empty on PASS

Uses the bridge adapters directly; does NOT import Flask.
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from clean_pipeline.bridge.request_adapter import adapt_request
from clean_pipeline.bridge.response_adapter import adapt_response
from clean_pipeline.contracts import (
    CleanPipelineRequest,
    CleanPipelineResult,
    PipelineStatus,
    StageName,
    StageResult,
    TargetSpec,
)
from clean_pipeline.orchestrator import run as orchestrate


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_png(path: Path, w: int = 320, h: int = 240) -> str:
    arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(str(path))
    return str(path)


def _b64_png(w: int = 1024, h: int = 1024) -> str:
    arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _chat_mock(content: str) -> MagicMock:
    msg = MagicMock(); msg.content = content
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    return resp


def _images_mock(w: int = 1024, h: int = 1024) -> MagicMock:
    item = MagicMock(); item.b64_json = _b64_png(w, h)
    resp = MagicMock(); resp.data = [item]
    return resp


def _manifest_json(w: int, h: int) -> str:
    return json.dumps({
        "jobId": "job", "sourceSha256": "abc",
        "imageWidth": w, "imageHeight": h, "model": "gpt-4o", "apiCallCount": 1,
        "objects": [{
            "id": "prod_0", "role": "product",
            "required": True, "movable": True, "removableFromScene": True,
            "bbox": {"x": 30, "y": 30, "width": 60, "height": 50},
            "polygon": [[30, 30], [90, 30], [90, 80], [30, 80]],
            "confidence": 0.92,
            "groupId": None, "zIndex": 1, "textContent": "", "description": "",
        }],
    })


def _empty_manifest_json(w: int, h: int) -> str:
    d = json.loads(_manifest_json(w, h))
    d["objects"] = []
    return json.dumps(d)


def _validation_pass_json() -> str:
    return json.dumps({
        "adObjectsRemaining": False, "remainingObjectIds": [],
        "protectedSubjectPreserved": True, "visibleSeamDetected": False,
        "duplicatedFragmentsDetected": False, "newlyGeneratedTextDetected": False,
        "sceneNaturalnessScore": 0.88, "pass": True, "reasons": ["OK"],
    })


_TW, _TH = 300, 200
_SAFE = dict(safe_left=20, safe_top=15, safe_right=20, safe_bottom=15)

_SPECS_RAW = [{
    "width": _TW, "height": _TH,
    "media": "naver", "slug": "naver_300x200", "name": "Naver Standard",
    "safeLeft": 20, "safeTop": 15, "safeRight": 20, "safeBottom": 15,
}]


def _run_pass_pipeline(src: str, output_dir: str, job_id: str) -> CleanPipelineResult:
    request = CleanPipelineRequest(
        job_id=job_id,
        source_path=src,
        target_specs=[TargetSpec(width=_TW, height=_TH, **_SAFE)],
        output_directory=output_dir,
    )
    with patch("openai.OpenAI") as MockOpenAI:
        mc = MockOpenAI.return_value
        mc.chat.completions.create.side_effect = [
            _chat_mock(_manifest_json(_TW, _TH)),
            _chat_mock(_validation_pass_json()),
        ]
        # P5 (DALL-E inpaint) always returns 1024×1024; scene plate generator validates this
        mc.images.edit.return_value = _images_mock()
        return orchestrate(request, api_key="sk-fake")


def _run_fail_pipeline(src: str, output_dir: str, job_id: str) -> CleanPipelineResult:
    """Force P2 failure via empty manifest → SCENE_ANALYSIS FAIL."""
    request = CleanPipelineRequest(
        job_id=job_id,
        source_path=src,
        target_specs=[TargetSpec(width=_TW, height=_TH, **_SAFE)],
        output_directory=output_dir,
    )
    with patch("openai.OpenAI") as MockOpenAI:
        mc = MockOpenAI.return_value
        mc.chat.completions.create.return_value = _chat_mock(_empty_manifest_json(_TW, _TH))
        return orchestrate(request, api_key="sk-fake")


# ── 1. jobId round-trip ───────────────────────────────────────────────────────


def test_job_id_round_trip_on_pass(tmp_path):
    """result.job_id must equal the request job_id on PASS."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "my_unique_job_id")
    assert result.job_id == "my_unique_job_id"


def test_job_id_round_trip_on_fail(tmp_path):
    """result.job_id must equal the request job_id on FAIL."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "fail_job_abc")
    assert result.job_id == "fail_job_abc"


# ── 2. PASS: output_paths[0] exists on disk ───────────────────────────────────


def test_pass_result_path_exists_on_disk(tmp_path):
    """On PASS, result.output_paths[0] must point to a real file on disk."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "path_check_job")

    assert result.status == PipelineStatus.PASS
    assert len(result.output_paths) == 1
    assert Path(result.output_paths[0]).exists(), (
        f"output file not found on disk: {result.output_paths[0]}"
    )


def test_fail_output_paths_is_empty(tmp_path):
    """On FAIL, result.output_paths must be empty."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "empty_paths_job")
    assert result.status == PipelineStatus.FAIL
    assert result.output_paths == []


# ── 3. adapt_response PASS contract ──────────────────────────────────────────


def test_adapt_response_pass_file_name_is_basename(tmp_path):
    """PASS item: fileName must equal os.path.basename(filePath)."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "fname_job")
    assert result.status == PipelineStatus.PASS

    items, missing = adapt_response(result, _SPECS_RAW)
    assert len(items) == 1
    item = items[0]
    assert item["fileName"] == os.path.basename(item["filePath"])


def test_adapt_response_pass_file_size_is_positive(tmp_path):
    """PASS item: fileSize must be > 0 (real PNG file written to disk)."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "fsize_job")
    assert result.status == PipelineStatus.PASS

    items, missing = adapt_response(result, _SPECS_RAW)
    item = items[0]
    assert item["fileSize"] is not None
    assert item["fileSize"] > 0, f"fileSize should be > 0, got {item['fileSize']}"


def test_adapt_response_pass_valid_is_true(tmp_path):
    """PASS item: valid=True."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "valid_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["valid"] is True


def test_adapt_response_pass_no_missing_slugs(tmp_path):
    """PASS: missing_ratio_types must be empty."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "slug_pass_job")
    _, missing = adapt_response(result, _SPECS_RAW)
    assert missing == []


# ── 4. adapt_response FAIL contract ──────────────────────────────────────────


def test_adapt_response_fail_valid_is_false(tmp_path):
    """FAIL item: valid=False."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "vfail_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["valid"] is False


def test_adapt_response_fail_filepath_is_empty_string(tmp_path):
    """FAIL item: filePath must be empty string."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "fp_fail_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["filePath"] == ""


def test_adapt_response_fail_filename_is_empty_string(tmp_path):
    """FAIL item: fileName must be empty string."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "fn_fail_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["fileName"] == ""


def test_adapt_response_fail_missing_slug_in_missing_list(tmp_path):
    """FAIL: missing_ratio_types must contain the slug."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "slug_fail_job")
    _, missing = adapt_response(result, _SPECS_RAW)
    assert "naver_300x200" in missing


def test_adapt_response_fail_populated_error_fields(tmp_path):
    """FAIL item: failedStage, failureCode, error must all be non-empty."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "errfields_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    item = items[0]
    assert item.get("failedStage"), f"failedStage should be set, got {item.get('failedStage')!r}"
    assert item.get("failureCode"), f"failureCode should be set, got {item.get('failureCode')!r}"
    assert item.get("error"), f"error should be set, got {item.get('error')!r}"


# ── 5. Spec dimensions propagated ────────────────────────────────────────────


def test_width_height_propagated_to_pass_item(tmp_path):
    """adapt_response PASS must carry width/height from the spec."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "dims_pass_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["width"] == _TW
    assert items[0]["height"] == _TH


def test_width_height_propagated_to_fail_item(tmp_path):
    """adapt_response FAIL must carry width/height from the spec."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "dims_fail_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["width"] == _TW
    assert items[0]["height"] == _TH


# ── 6. slug / media / name propagated ────────────────────────────────────────


def test_slug_media_name_on_pass(tmp_path):
    """adapt_response PASS must carry slug, media, name from spec."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "meta_pass_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["slug"] == "naver_300x200"
    assert items[0]["media"] == "naver"
    assert items[0]["name"] == "Naver Standard"


def test_slug_media_name_on_fail(tmp_path):
    """adapt_response FAIL must also carry slug, media, name from spec."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "meta_fail_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["slug"] == "naver_300x200"
    assert items[0]["media"] == "naver"
    assert items[0]["name"] == "Naver Standard"


# ── 7. pipelineVersion always "clean_v1" ─────────────────────────────────────


def test_pipeline_version_always_clean_v1_on_pass(tmp_path):
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "pv_pass_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["pipelineVersion"] == "clean_v1"


def test_pipeline_version_always_clean_v1_on_fail(tmp_path):
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "pv_fail_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["pipelineVersion"] == "clean_v1"


# ── 8. fallbackUsed always False ──────────────────────────────────────────────


def test_fallback_used_always_false_on_pass(tmp_path):
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_pass_pipeline(src, str(tmp_path / "out"), "fb_pass_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["fallbackUsed"] is False


def test_fallback_used_always_false_on_fail(tmp_path):
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    result = _run_fail_pipeline(src, str(tmp_path / "out"), "fb_fail_job")
    items, _ = adapt_response(result, _SPECS_RAW)
    assert items[0]["fallbackUsed"] is False


# ── 9. adapt_request → orchestrate round-trip ─────────────────────────────────


def test_adapt_request_jobid_flows_to_result(tmp_path):
    """Job IDs created by adapt_request must flow through to CleanPipelineResult."""
    src = _make_png(tmp_path / "ad.png", _TW, _TH)
    data = {
        "psdPath": src,
        "specs": _SPECS_RAW,
        # legacy keys — must be ignored
        "resizeMode": "smart-fit",
        "smartFitStrength": "fill",
        "focalPosition": "center",
        "psdMode": "artboard-first",
    }

    job_id = "e2e_job_999"
    request = adapt_request(data, job_id, str(tmp_path / "out"))

    with patch("openai.OpenAI") as MockOpenAI:
        mc = MockOpenAI.return_value
        mc.chat.completions.create.side_effect = [
            _chat_mock(_manifest_json(_TW, _TH)),
            _chat_mock(_validation_pass_json()),
        ]
        mc.images.edit.return_value = _images_mock()
        result = orchestrate(request, api_key="sk-fake")

    assert result.job_id == job_id
    assert result.status == PipelineStatus.PASS
    items, missing = adapt_response(result, _SPECS_RAW)
    assert items[0]["pipelineVersion"] == "clean_v1"
    assert items[0]["valid"] is True
    assert missing == []
