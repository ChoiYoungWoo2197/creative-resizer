"""P6 scene validation policy tests.

Verifies the current bypass policy and the full AI-enabled path:

Bypass policy (_AI_VALIDATION_ENABLED = False):
  - Validator is always called (no short-circuit before it)
  - Deterministic checks always fire
  - AI is NOT called (apiCallCount = 0)
  - validation.ai is None
  - validation.passed = True only when deterministic checks pass
  - The result does NOT claim AI-quality PASS (ai field is None = bypass)

AI-enabled path (tested via monkeypatch):
  - AD_OBJECTS_REMAINING → FAIL
  - VISIBLE_SEAM_DETECTED → FAIL
  - LOW_NATURALNESS_SCORE → FAIL
  - All clean → PASS with naturalness score recorded

Deterministic failures fire regardless of _AI_VALIDATION_ENABLED:
  - SOLID_COLOR_IMAGE
  - RESTORE_PIXELS_MISMATCH
  - TARGET_SIZE_MISMATCH
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject
from clean_pipeline.contracts import PipelineStatus, StageName
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.scene.models import ScenePlateResult
from clean_pipeline.validation.scene_validator import validate

_TW, _TH = 200, 150


def _make_logger(tmp_path: Path, job_id: str = "test_job") -> PipelineLogger:
    return PipelineLogger(job_id, tmp_path / "pipeline.jsonl")


def _make_manifest() -> SceneManifest:
    return SceneManifest(
        job_id="test_job",
        source_sha256="abc",
        image_width=_TW,
        image_height=_TH,
        model="gpt-4o",
        objects=[
            SceneObject(
                id="obj_0", role="product", required=True, movable=True,
                removable_from_scene=True,
                bbox=BBox(x=10, y=10, width=80, height=60),
                polygon=[[10, 10], [90, 10], [90, 70], [10, 70]],
                confidence=0.9,
            )
        ],
    )


def _build_valid_plate(tmp_path: Path) -> tuple[ScenePlateResult, str]:
    """Create synthetic plate files that pass all deterministic checks."""
    d = tmp_path / "files"
    d.mkdir(exist_ok=True)

    np.random.seed(42)
    src_arr = np.random.randint(30, 200, (_TH, _TW, 3), dtype=np.uint8)
    src_path = d / "source_projection.png"
    Image.fromarray(src_arr, "RGB").save(str(src_path))

    restore_arr = np.zeros((_TH, _TW), dtype=np.uint8)
    restore_arr[_TH // 2:, :] = 255
    restore_path = d / "projected_restore_mask.png"
    Image.fromarray(restore_arr, "L").save(str(restore_path))

    scene_arr = np.random.randint(50, 220, (_TH, _TW, 3), dtype=np.uint8)
    scene_arr[_TH // 2:, :] = src_arr[_TH // 2:, :]   # restore region matches source
    scene_path = d / "scene_plate.png"
    Image.fromarray(scene_arr, "RGB").save(str(scene_path))

    canonical_arr = np.random.randint(30, 200, (_TH, _TW, 3), dtype=np.uint8)
    canonical_path = d / "canonical.png"
    Image.fromarray(canonical_arr, "RGB").save(str(canonical_path))

    plate = ScenePlateResult(
        job_id="test_job",
        target_width=_TW,
        target_height=_TH,
        source_projection_path=str(src_path),
        projected_removal_mask_path=str(d / "removal_mask.png"),
        projected_restore_mask_path=str(restore_path),
        ai_cleanup_path=str(d / "ai_cleanup.png"),
        scene_plate_path=str(scene_path),
        scene_json_path="",
        api_call_count=1,
    )
    return plate, str(canonical_path)


def _mock_openai(response_json: dict):
    content = json.dumps(response_json)
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=content))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    return mock_client


_PASS_AI_RESPONSE = {
    "adObjectsRemaining": False,
    "remainingObjectIds": [],
    "protectedSubjectPreserved": True,
    "visibleSeamDetected": False,
    "duplicatedFragmentsDetected": False,
    "newlyGeneratedTextDetected": False,
    "sceneNaturalnessScore": 0.87,
    "pass": True,
    "reasons": ["Scene looks natural"],
}


# ── Bypass policy ─────────────────────────────────────────────────────────────


def test_bypass_ai_is_none(tmp_path):
    """With _AI_VALIDATION_ENABLED=False, validation.ai must be None (bypass recorded)."""
    plate, canonical = _build_valid_plate(tmp_path)
    lg = _make_logger(tmp_path)

    result, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=_make_manifest(),
        api_key="sk-test",
        output_dir=str(tmp_path / "output"),
        job_id="test_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.PASS
    assert validation.ai is None, "AI field must be None when validation is bypassed"


def test_bypass_api_call_count_is_zero(tmp_path):
    """With bypass, no OpenAI API call is made — apiCallCount in metrics must be 0."""
    plate, canonical = _build_valid_plate(tmp_path)
    lg = _make_logger(tmp_path)

    result, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=_make_manifest(),
        api_key="sk-test",
        output_dir=str(tmp_path / "output"),
        job_id="test_job",
        logger=lg,
    )
    lg.close()

    assert result.metrics.get("apiCallCount") == 0


def test_bypass_pass_not_disguised_as_quality_pass(tmp_path):
    """Bypass PASS must not look like an AI quality PASS: ai=None, naturalness=1.0 (default)."""
    plate, canonical = _build_valid_plate(tmp_path)
    lg = _make_logger(tmp_path)

    result, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=_make_manifest(),
        api_key="sk-test",
        output_dir=str(tmp_path / "output"),
        job_id="test_job",
        logger=lg,
    )
    lg.close()

    assert validation.ai is None
    # naturalness defaults to 1.0 — this is the "bypass" default, not an AI score
    assert result.metrics.get("naturalness") == 1.0
    # validation.json must exist and record the bypass state
    assert Path(validation.validation_json_path).exists()


def test_bypass_validation_json_exists(tmp_path):
    """validation.json is written to disk even when AI is bypassed."""
    plate, canonical = _build_valid_plate(tmp_path)
    lg = _make_logger(tmp_path)

    _, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=_make_manifest(),
        api_key="sk-test",
        output_dir=str(tmp_path / "output"),
        job_id="test_job",
        logger=lg,
    )
    lg.close()

    assert validation.validation_json_path
    json_path = Path(validation.validation_json_path)
    assert json_path.exists()
    # Must be parseable JSON
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


# ── Deterministic checks fire regardless of bypass ────────────────────────────


def test_solid_color_plate_fails_deterministic_even_with_bypass(tmp_path):
    """SOLID_COLOR_IMAGE deterministic check fires regardless of _AI_VALIDATION_ENABLED."""
    plate, canonical = _build_valid_plate(tmp_path)

    # Overwrite scene_plate with solid color → SOLID_COLOR_IMAGE
    Image.new("RGB", (_TW, _TH), (128, 100, 90)).save(plate.scene_plate_path)

    lg = _make_logger(tmp_path, "solid_job")
    result, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=_make_manifest(),
        api_key="sk-test",
        output_dir=str(tmp_path / "output"),
        job_id="solid_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert "SOLID_COLOR_IMAGE" in validation.fail_codes


def test_restore_mismatch_fails_deterministic_even_with_bypass(tmp_path):
    """RESTORE_PIXELS_MISMATCH fires regardless of _AI_VALIDATION_ENABLED."""
    plate, canonical = _build_valid_plate(tmp_path)

    scene_arr = np.array(Image.open(plate.scene_plate_path).convert("RGB"), dtype=np.uint8)
    scene_arr[_TH // 2:, :] = 0   # corrupt restore region
    Image.fromarray(scene_arr, "RGB").save(plate.scene_plate_path)

    lg = _make_logger(tmp_path, "mismatch_job")
    result, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=_make_manifest(),
        api_key="sk-test",
        output_dir=str(tmp_path / "output"),
        job_id="mismatch_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert "RESTORE_PIXELS_MISMATCH" in validation.fail_codes


# ── AI-enabled path (monkeypatched) ──────────────────────────────────────────


def test_ai_pass_when_enabled(tmp_path, monkeypatch):
    """When AI is enabled and response is clean, validator PASS with naturalness recorded."""
    import clean_pipeline.validation.scene_validator as sv
    monkeypatch.setattr(sv, "_AI_VALIDATION_ENABLED", True)

    plate, canonical = _build_valid_plate(tmp_path)
    lg = _make_logger(tmp_path, "ai_pass_job")

    with patch("openai.OpenAI", return_value=_mock_openai(_PASS_AI_RESPONSE)):
        result, validation = validate(
            scene_plate_result=plate,
            canonical_path=canonical,
            manifest=_make_manifest(),
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="ai_pass_job",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.PASS
    assert validation.ai is not None
    assert validation.ai.api_call_count == 1
    assert validation.ai.scene_naturalness_score == pytest.approx(0.87)


@pytest.mark.parametrize("ai_override,expected_code", [
    ({"adObjectsRemaining": True, "remainingObjectIds": ["obj_0"]}, "AD_OBJECTS_REMAINING"),
    ({"visibleSeamDetected": True}, "VISIBLE_SEAM_DETECTED"),
    ({"sceneNaturalnessScore": 0.3}, "LOW_NATURALNESS_SCORE"),
    ({"protectedSubjectPreserved": False}, "PROTECTED_SUBJECT_DAMAGED"),
])
def test_ai_fail_codes_when_enabled(tmp_path, monkeypatch, ai_override, expected_code):
    """Each AI fail condition produces the correct fail code when AI is enabled."""
    import clean_pipeline.validation.scene_validator as sv
    monkeypatch.setattr(sv, "_AI_VALIDATION_ENABLED", True)

    response = {**_PASS_AI_RESPONSE, "pass": False, **ai_override}
    plate, canonical = _build_valid_plate(tmp_path)
    lg = _make_logger(tmp_path, f"ai_fail_{expected_code[:8]}")

    with patch("openai.OpenAI", return_value=_mock_openai(response)):
        result, validation = validate(
            scene_plate_result=plate,
            canonical_path=canonical,
            manifest=_make_manifest(),
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id=f"ai_fail_{expected_code[:8]}",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert expected_code in validation.fail_codes


def test_no_api_key_fails_when_ai_enabled(tmp_path, monkeypatch):
    """With AI enabled and no api_key, validator must fail with NO_API_KEY."""
    import clean_pipeline.validation.scene_validator as sv
    monkeypatch.setattr(sv, "_AI_VALIDATION_ENABLED", True)

    plate, canonical = _build_valid_plate(tmp_path)
    lg = _make_logger(tmp_path, "nokey_job")

    result, validation = validate(
        scene_plate_result=plate,
        canonical_path=canonical,
        manifest=_make_manifest(),
        api_key="",   # no key
        output_dir=str(tmp_path / "output"),
        job_id="nokey_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert any("NO_API_KEY" in r or "api_key" in r.lower() for r in result.reasons)
