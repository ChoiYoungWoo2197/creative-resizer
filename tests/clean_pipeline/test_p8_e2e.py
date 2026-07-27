"""P8 end-to-end tests: FINAL_VALIDATION + orchestrator.

Tests:
  1. Normal: all stages PASS → result.png created, CleanPipelineResult.status == PASS
  2. Failure: empty manifest (SCENE_ANALYSIS FAIL) → no result.png, stages after P2 not executed
"""
from __future__ import annotations

import base64
import io
import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from worker.clean_pipeline.contracts import (
    CleanPipelineRequest,
    PipelineStatus,
    StageName,
    TargetSpec,
)
from worker.clean_pipeline.orchestrator import run as orchestrate

# ── Constants ──────────────────────────────────────────────────────────────────

_TW, _TH = 300, 200
_SAFE = dict(safe_left=20, safe_top=15, safe_right=20, safe_bottom=15)


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_png(path: Path, w: int, h: int, color=(80, 120, 160)) -> str:
    img = Image.fromarray(
        np.random.randint(30, 220, (h, w, 3), dtype=np.uint8), "RGB"
    )
    img.save(str(path), "PNG")
    return str(path)


def _b64_png(w: int, h: int, color=(80, 120, 160)) -> str:
    arr = np.random.randint(30, 220, (h, w, 3), dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _chat_response(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _images_response(b64: str) -> MagicMock:
    item = MagicMock()
    item.b64_json = b64
    resp = MagicMock()
    resp.data = [item]
    return resp


def _manifest_json(image_w: int, image_h: int) -> str:
    return json.dumps({
        "jobId": "e2e_job",
        "sourceSha256": "abc123",
        "imageWidth": image_w,
        "imageHeight": image_h,
        "model": "gpt-4o",
        "apiCallCount": 1,
        "objects": [
            {
                "id": "prod_0",
                "role": "product",
                "required": True,
                "movable": True,
                "removableFromScene": True,
                "bbox": {"x": 30, "y": 30, "width": 60, "height": 50},
                "polygon": [[30, 30], [90, 30], [90, 80], [30, 80]],
                "confidence": 0.92,
                "groupId": None,
                "zIndex": 1,
                "textContent": "",
                "description": "Product image",
            },
        ],
    })


def _empty_manifest_json(image_w: int, image_h: int) -> str:
    return json.dumps({
        "jobId": "e2e_fail_job",
        "sourceSha256": "abc",
        "imageWidth": image_w,
        "imageHeight": image_h,
        "model": "gpt-4o",
        "apiCallCount": 1,
        "objects": [],
    })


def _scene_validation_pass_json() -> str:
    return json.dumps({
        "adObjectsRemaining": False,
        "remainingObjectIds": [],
        "protectedSubjectPreserved": True,
        "visibleSeamDetected": False,
        "duplicatedFragmentsDetected": False,
        "newlyGeneratedTextDetected": False,
        "sceneNaturalnessScore": 0.88,
        "pass": True,
        "reasons": ["Scene looks natural"],
    })


# ── Test 1: Full PASS ──────────────────────────────────────────────────────────


def test_all_stages_pass_produces_result_png(tmp_path):
    """Normal path: all 8 stages PASS → result.png created, status == PASS."""
    src_path = _make_png(tmp_path / "ad.png", w=_TW, h=_TH)
    output_dir = str(tmp_path / "output")
    job_id = "pass_job"

    request = CleanPipelineRequest(
        job_id=job_id,
        source_path=src_path,
        target_specs=[TargetSpec(width=_TW, height=_TH, **_SAFE)],
        output_directory=output_dir,
    )

    p2_resp = _chat_response(_manifest_json(_TW, _TH))
    p5_b64 = _b64_png(1024, 1024)
    p5_resp = _images_response(p5_b64)
    p6_resp = _chat_response(_scene_validation_pass_json())

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MockOpenAI.return_value
        mock_client.chat.completions.create.side_effect = [p2_resp, p6_resp]
        mock_client.images.edit.return_value = p5_resp

        result = orchestrate(request, api_key="sk-fake")

    assert result.status == PipelineStatus.PASS, (
        f"Expected PASS, got FAIL: {result.failure_code} — {result.failure_message}\n"
        + "\n".join(f"  {sr.stage.value}: {sr.status.value} {sr.reasons}" for sr in result.stage_results)
    )
    assert result.output_paths, "output_paths must not be empty on PASS"
    result_png = Path(result.output_paths[0])
    assert result_png.exists(), f"result.png not found: {result_png}"

    img = Image.open(str(result_png))
    assert img.size == (_TW, _TH), f"result.png size {img.size} != ({_TW}, {_TH})"

    # All 8 stages recorded
    stage_names = {sr.stage for sr in result.stage_results}
    for expected in StageName:
        assert expected in stage_names, f"Stage {expected.value} missing from stage_results"

    # All PASS
    for sr in result.stage_results:
        assert sr.status == PipelineStatus.PASS, \
            f"Stage {sr.stage.value} is {sr.status.value}: {sr.reasons}"


# ── Test 2: SCENE_ANALYSIS FAIL → stop immediately ────────────────────────────


def test_empty_manifest_fails_at_scene_analysis(tmp_path):
    """Empty manifest → SCENE_ANALYSIS FAIL; P5 images.edit must never be called."""
    src_path = _make_png(tmp_path / "ad.png", w=_TW, h=_TH)
    output_dir = str(tmp_path / "output")
    job_id = "fail_job"

    request = CleanPipelineRequest(
        job_id=job_id,
        source_path=src_path,
        target_specs=[TargetSpec(width=_TW, height=_TH, **_SAFE)],
        output_directory=output_dir,
    )

    p2_empty_resp = _chat_response(_empty_manifest_json(_TW, _TH))

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MockOpenAI.return_value
        mock_client.chat.completions.create.return_value = p2_empty_resp

        result = orchestrate(request, api_key="sk-fake")

    assert result.status == PipelineStatus.FAIL

    # P2 stage must be FAIL
    p2_sr = next(
        (sr for sr in result.stage_results if sr.stage == StageName.SCENE_ANALYSIS),
        None,
    )
    assert p2_sr is not None, "SCENE_ANALYSIS stage not in stage_results"
    assert p2_sr.status == PipelineStatus.FAIL

    # Stages after P2 must not appear
    executed_stages = {sr.stage for sr in result.stage_results}
    for later_stage in (
        StageName.OBJECT_EXTRACTION,
        StageName.REMOVAL_MASK,
        StageName.SCENE_GENERATION,
        StageName.SCENE_VALIDATION,
        StageName.LAYOUT,
        StageName.FINAL_VALIDATION,
    ):
        assert later_stage not in executed_stages, \
            f"Stage {later_stage.value} should not execute after SCENE_ANALYSIS FAIL"

    # P5 (DALL-E inpaint) must never be called
    mock_client.images.edit.assert_not_called()

    # No result.png
    result_png = (
        Path(output_dir) / job_id / "clean_v1" / "08_final" / "result.png"
    )
    assert not result_png.exists(), "result.png must not exist when pipeline fails"
