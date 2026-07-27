"""P2 tests: SCENE_ANALYSIS stage — fixed JSON mock, empty objects FAIL."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from worker.clean_pipeline.contracts import PipelineStatus, StageName
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.analysis.openai_analyzer import analyze

_W, _H = 200, 150

_VALID_RESPONSE = json.dumps({
    "objects": [
        {
            "id": "obj_0",
            "role": "product",
            "required": True,
            "movable": True,
            "removableFromScene": True,
            "bbox": {"x": 10, "y": 10, "width": 80, "height": 100},
            "polygon": [[10, 10], [90, 10], [90, 110], [10, 110]],
            "confidence": 0.93,
            "groupId": None,
            "zIndex": 1,
            "textContent": "",
            "description": "Main product bottle",
        },
        {
            "id": "obj_1",
            "role": "cta_group",
            "required": True,
            "movable": True,
            "removableFromScene": True,
            "bbox": {"x": 10, "y": 120, "width": 100, "height": 20},
            "polygon": [[10, 120], [110, 120], [110, 140], [10, 140]],
            "confidence": 0.88,
            "groupId": None,
            "zIndex": 2,
            "textContent": "지금 구매",
            "description": "CTA button",
        },
    ]
})

_EMPTY_OBJECTS_RESPONSE = json.dumps({"objects": []})


def _make_canonical(tmp_path: Path) -> str:
    arr = np.random.randint(30, 220, (_H, _W, 3), dtype=np.uint8)
    p = tmp_path / "canonical.png"
    Image.fromarray(arr, "RGB").save(str(p))
    return str(p)


def _make_logger(tmp_path: Path) -> PipelineLogger:
    return PipelineLogger("test_job", tmp_path / "pipeline.jsonl")


def _mock_openai(content: str):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=content))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    return mock_client


# ── Normal: fixed JSON → valid manifest ───────────────────────────────────────


def test_valid_manifest_from_fixed_response(tmp_path):
    canonical = _make_canonical(tmp_path)
    lg = _make_logger(tmp_path)

    with patch("openai.OpenAI", return_value=_mock_openai(_VALID_RESPONSE)):
        result, manifest = analyze(
            canonical_path=canonical,
            image_width=_W,
            image_height=_H,
            source_sha256="abc123",
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="job_p2",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.PASS
    assert result.stage == StageName.SCENE_ANALYSIS
    assert manifest is not None
    assert len(manifest.objects) == 2
    assert manifest.api_call_count == 1
    assert manifest.objects[0].role == "product"
    assert manifest.objects[1].role == "cta_group"
    assert manifest.objects[0].removable_from_scene is True


# ── Failure: objects=[] → MANIFEST_EMPTY ──────────────────────────────────────


def test_empty_objects_returns_manifest_empty(tmp_path):
    canonical = _make_canonical(tmp_path)
    lg = _make_logger(tmp_path)

    with patch("openai.OpenAI", return_value=_mock_openai(_EMPTY_OBJECTS_RESPONSE)):
        result, manifest = analyze(
            canonical_path=canonical,
            image_width=_W,
            image_height=_H,
            source_sha256="abc123",
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="job_p2_fail",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert manifest is None
    assert any("MANIFEST_EMPTY" in r for r in result.reasons)
