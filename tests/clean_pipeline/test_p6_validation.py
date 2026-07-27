"""P6 tests: SCENE_VALIDATION stage.

Tests:
  1. Normal: valid AI response → PASS
  2. Failure: adObjectsRemaining=true → AD_OBJECTS_REMAINING FAIL
  3. Deterministic: solid-color scene_plate → SOLID_COLOR_IMAGE FAIL
  4. Deterministic: restore pixels mismatch → RESTORE_PIXELS_MISMATCH FAIL
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from worker.clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject
from worker.clean_pipeline.contracts import PipelineStatus, StageName
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.scene.models import ScenePlateResult
from worker.clean_pipeline.validation.scene_validator import validate

_TW, _TH = 200, 150


def _make_logger(tmp_path: Path, job_id: str = "test_job") -> PipelineLogger:
    return PipelineLogger(job_id, tmp_path / "pipeline.jsonl")


def _make_manifest() -> SceneManifest:
    return SceneManifest(
        job_id="test_job",
        source_sha256="abc",
        image_width=200,
        image_height=150,
        model="gpt-4o",
        objects=[
            SceneObject(
                id="obj_0",
                role="product",
                required=True,
                movable=True,
                removable_from_scene=True,
                bbox=BBox(x=10, y=10, width=80, height=60),
                polygon=[[10, 10], [90, 10], [90, 70], [10, 70]],
                confidence=0.9,
            )
        ],
    )


def _save_image(path: Path, arr: np.ndarray) -> None:
    Image.fromarray(arr.astype(np.uint8), "RGB").save(str(path))


def _build_scene_plate_result(tmp_path: Path) -> tuple[ScenePlateResult, str]:
    """Create synthetic scene plate files that pass deterministic validation."""
    d = tmp_path / "files"
    d.mkdir()

    # Source projection: random-looking (non-solid) pixels
    np.random.seed(42)
    src_arr = np.random.randint(30, 200, (_TH, _TW, 3), dtype=np.uint8)
    src_path = d / "source_projection.png"
    _save_image(src_path, src_arr)

    # Restore mask: bottom half = white (restore region)
    restore_arr = np.zeros((_TH, _TW), dtype=np.uint8)
    restore_arr[_TH // 2:, :] = 255
    restore_path = d / "projected_restore_mask.png"
    Image.fromarray(restore_arr, "L").save(str(restore_path))

    # Scene plate: same as src in restore region (bottom half), different in removal region
    scene_arr = np.random.randint(50, 220, (_TH, _TW, 3), dtype=np.uint8)
    scene_arr[_TH // 2:, :] = src_arr[_TH // 2:, :]  # restore region matches source
    scene_path = d / "scene_plate.png"
    _save_image(scene_path, scene_arr)

    # Canonical
    canonical_arr = np.random.randint(30, 200, (_TH, _TW, 3), dtype=np.uint8)
    canonical_path = d / "canonical.png"
    _save_image(canonical_path, canonical_arr)

    plate = ScenePlateResult(
        job_id="test_job",
        target_width=_TW,
        target_height=_TH,
        source_projection_path=str(src_path),
        projected_removal_mask_path=str(d / "removal_mask.png"),  # not used in validation
        projected_restore_mask_path=str(restore_path),
        ai_cleanup_path=str(d / "ai_cleanup.png"),                # not used in validation
        scene_plate_path=str(scene_path),
        scene_json_path="",
        api_call_count=1,
    )
    return plate, str(canonical_path)


def _mock_openai(response_json: dict):
    """Mock openai.OpenAI to return the given dict as GPT-4o response."""
    content = json.dumps(response_json)
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=content))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    return mock_client


_PASS_RESPONSE = {
    "adObjectsRemaining": False,
    "remainingObjectIds": [],
    "protectedSubjectPreserved": True,
    "visibleSeamDetected": False,
    "duplicatedFragmentsDetected": False,
    "newlyGeneratedTextDetected": False,
    "sceneNaturalnessScore": 0.87,
    "pass": True,
    "reasons": ["Scene looks natural", "No ad objects visible"],
}

_AD_REMAINING_RESPONSE = {
    **_PASS_RESPONSE,
    "adObjectsRemaining": True,
    "remainingObjectIds": ["obj_0"],
    "pass": False,
    "reasons": ["Product bottle still visible in top-left"],
}


# ── Normal: valid AI response → PASS ─────────────────────────────────────────

def test_valid_ai_response_passes(tmp_path):
    plate, canonical = _build_scene_plate_result(tmp_path)
    manifest = _make_manifest()
    lg = _make_logger(tmp_path)

    with patch("openai.OpenAI", return_value=_mock_openai(_PASS_RESPONSE)):
        result, validation = validate(
            scene_plate_result=plate,
            canonical_path=canonical,
            manifest=manifest,
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="test_job",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.PASS, result.reasons
    assert result.stage == StageName.SCENE_VALIDATION
    assert validation is not None
    assert validation.passed is True
    assert validation.fail_codes == []
    assert validation.ai is not None
    assert validation.ai.api_call_count == 1
    assert validation.ai.scene_naturalness_score == pytest.approx(0.87)
    assert Path(validation.validation_json_path).exists()


# ── Failure: adObjectsRemaining=true → AD_OBJECTS_REMAINING ──────────────────

def test_ad_objects_remaining_fails(tmp_path):
    plate, canonical = _build_scene_plate_result(tmp_path)
    manifest = _make_manifest()
    lg = _make_logger(tmp_path, "fail_job")

    with patch("openai.OpenAI", return_value=_mock_openai(_AD_REMAINING_RESPONSE)):
        result, validation = validate(
            scene_plate_result=plate,
            canonical_path=canonical,
            manifest=manifest,
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="fail_job",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert validation is not None
    assert "AD_OBJECTS_REMAINING" in validation.fail_codes
    assert any("AD_OBJECTS_REMAINING" in r for r in result.reasons)


# ── Deterministic: solid color scene plate → SOLID_COLOR_IMAGE ───────────────

def test_solid_color_scene_plate_fails_deterministic(tmp_path):
    plate, canonical = _build_scene_plate_result(tmp_path)
    manifest = _make_manifest()

    # Overwrite scene_plate with solid color
    solid = Image.new("RGB", (_TW, _TH), (128, 100, 90))
    solid.save(plate.scene_plate_path)

    lg = _make_logger(tmp_path, "solid_job")

    # No API mock needed: deterministic check fires before AI call
    result, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=manifest,
        api_key="sk-test",
        output_dir=str(tmp_path / "output"),
        job_id="solid_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert validation is not None
    assert "SOLID_COLOR_IMAGE" in validation.fail_codes


# ── Deterministic: restore pixels mismatch ────────────────────────────────────

def test_restore_pixels_mismatch_fails_deterministic(tmp_path):
    plate, canonical = _build_scene_plate_result(tmp_path)
    manifest = _make_manifest()

    # Corrupt scene_plate: overwrite restore region with wrong pixels
    scene_arr = np.array(Image.open(plate.scene_plate_path).convert("RGB"), dtype=np.uint8)
    scene_arr[_TH // 2:, :] = 0   # zero out restore region (mismatch)
    Image.fromarray(scene_arr, "RGB").save(plate.scene_plate_path)

    lg = _make_logger(tmp_path, "mismatch_job")

    result, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=manifest,
        api_key="sk-test",
        output_dir=str(tmp_path / "output"),
        job_id="mismatch_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert "RESTORE_PIXELS_MISMATCH" in validation.fail_codes
