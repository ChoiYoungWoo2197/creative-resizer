"""P3 tests: OBJECT_EXTRACTION stage.

Tests:
  1. Normal: polygon exterior alpha=0, required RGBA generated
  2. Failure: required object with degenerate polygon (2 pts) → REQUIRED_MASK_EMPTY
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from worker.clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject
from worker.clean_pipeline.contracts import PipelineStatus, StageName
from worker.clean_pipeline.extraction.extraction_validator import validate as validate_extraction
from worker.clean_pipeline.extraction.object_extractor import extract
from worker.clean_pipeline.pipeline_logger import PipelineLogger

_W, _H = 200, 150


def _make_canonical(tmp_path: Path, w: int = _W, h: int = _H) -> str:
    arr = np.random.randint(30, 220, (h, w, 3), dtype=np.uint8)
    p = tmp_path / "canonical.png"
    Image.fromarray(arr, "RGB").save(str(p))
    return str(p)


def _make_logger(tmp_path: Path, job_id: str = "test_job") -> PipelineLogger:
    return PipelineLogger(job_id, tmp_path / "pipeline.jsonl")


def _make_manifest(objects: list[SceneObject]) -> SceneManifest:
    return SceneManifest(
        job_id="test_job",
        source_sha256="deadbeef",
        image_width=_W,
        image_height=_H,
        model="gpt-4o",
        objects=objects,
    )


# ── Normal: required product object → RGBA with correct alpha ────────────────

def test_normal_extraction_rgba_alpha_matches_mask(tmp_path):
    """Extracted RGBA alpha channel must equal the polygon mask (no exterior leak)."""
    canonical = _make_canonical(tmp_path)
    lg = _make_logger(tmp_path)

    # Rectangle polygon inside the image
    polygon = [[20, 10], [120, 10], [120, 100], [20, 100]]

    obj = SceneObject(
        id="obj_0",
        role="product",
        required=True,
        movable=True,
        removable_from_scene=True,
        bbox=BBox(x=20, y=10, width=100, height=90),
        polygon=polygon,
        confidence=0.95,
    )
    manifest = _make_manifest([obj])

    result, extraction = extract(
        canonical_path=canonical,
        manifest=manifest,
        output_dir=str(tmp_path / "output"),
        job_id="test_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.PASS, result.reasons
    assert result.stage == StageName.OBJECT_EXTRACTION
    assert extraction is not None
    assert len(extraction.objects) == 1

    ext_obj = extraction.objects[0]
    assert ext_obj.id == "obj_0"

    # RGBA must exist
    assert Path(ext_obj.rgba_path).exists()
    assert Path(ext_obj.mask_path).exists()

    # Load RGBA and mask, compare alpha channels
    rgba_arr = np.array(Image.open(ext_obj.rgba_path).convert("RGBA"))
    mask_arr = np.array(Image.open(ext_obj.mask_path).convert("L"))

    alpha = rgba_arr[:, :, 3]

    # Alpha must equal the mask — no exterior pixels with non-zero alpha
    assert np.array_equal(alpha, mask_arr), (
        f"RGBA alpha does not match mask: "
        f"max diff={np.abs(alpha.astype(int) - mask_arr.astype(int)).max()}"
    )

    # Exterior (fully outside polygon) must have alpha=0
    # The crop is the bounding rect of the polygon.
    # For a rectangular polygon, corners of the crop are inside the polygon —
    # but we verify no pixel in the mask is > 0 outside the polygon area.
    # Here just verify the source_bbox matches the polygon bounds.
    sb = ext_obj.source_bbox
    # The polygon spans x=[20,120], y=[10,100]
    # LANCZOS 2× supersample causes ~3px bleed beyond polygon boundary
    assert abs(sb.x - 20) <= 4
    assert abs(sb.y - 10) <= 4
    assert 90 <= sb.width <= 112
    assert 80 <= sb.height <= 102


# ── Failure: required object with degenerate polygon → REQUIRED_MASK_EMPTY ───

def test_required_degenerate_polygon_fails(tmp_path):
    """A required object whose polygon has only 2 points produces an empty mask → FAIL."""
    canonical = _make_canonical(tmp_path)
    lg = _make_logger(tmp_path, "fail_job")

    # 2-point polygon cannot fill any area
    degenerate_polygon = [[10, 10], [50, 10]]

    obj = SceneObject(
        id="req_0",
        role="product",
        required=True,
        movable=True,
        removable_from_scene=True,
        bbox=BBox(x=10, y=10, width=40, height=1),
        polygon=degenerate_polygon,
        confidence=0.9,
    )
    manifest = _make_manifest([obj])

    result, extraction = extract(
        canonical_path=canonical,
        manifest=manifest,
        output_dir=str(tmp_path / "output"),
        job_id="fail_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert extraction is None
    assert any("REQUIRED_MASK_EMPTY" in r for r in result.reasons)


# ── Validator: required object not extracted → REQUIRED_OBJECT_NOT_EXTRACTED ──

def test_validator_catches_missing_required_object(tmp_path):
    """extraction_validator detects a required manifest object absent from result."""
    from worker.clean_pipeline.extraction.models import ExtractionResult

    obj = SceneObject(
        id="missing_0",
        role="logo",
        required=True,
        movable=True,
        removable_from_scene=True,
        bbox=BBox(x=0, y=0, width=10, height=10),
        polygon=[[0, 0], [10, 0], [10, 10], [0, 10]],
        confidence=0.8,
    )
    manifest = _make_manifest([obj])
    # Result with no extracted objects — missing_0 was skipped/failed before reaching validator
    result = ExtractionResult(job_id="test_job", objects=[], protected=[])

    is_valid, code, reason = validate_extraction(manifest, result)

    assert not is_valid
    assert code == "REQUIRED_OBJECT_NOT_EXTRACTED"
    assert "missing_0" in reason
