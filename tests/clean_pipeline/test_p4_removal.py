"""P4 tests: REMOVAL_MASK stage.

Tests:
  1. Normal: two ad-object masks → union + dilation → restoreMask is complement
  2. Failure: all-zero masks → REMOVAL_MASK_EMPTY
  3. Validator: removal + restore have no foreground overlap
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from worker.clean_pipeline.contracts import PipelineStatus, StageName
from worker.clean_pipeline.extraction.models import (
    ExtractedObject,
    ExtractionResult,
    SourceBBox,
)
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.removal.removal_mask_builder import build
from worker.clean_pipeline.removal.removal_mask_validator import validate

_W, _H = 200, 150


def _make_logger(tmp_path: Path, job_id: str = "test_job") -> PipelineLogger:
    return PipelineLogger(job_id, tmp_path / "pipeline.jsonl")


def _write_mask(path: Path, arr: np.ndarray) -> None:
    """Save uint8 numpy array as L-mode PNG."""
    Image.fromarray(arr.astype(np.uint8), "L").save(str(path))


def _make_extracted_object(
    obj_id: str,
    mask_path: str,
    source_bbox: SourceBBox,
    required: bool = True,
) -> ExtractedObject:
    return ExtractedObject(
        id=obj_id,
        role="product",
        required=required,
        movable=True,
        removable_from_scene=True,
        source_bbox=source_bbox,
        rgba_path=mask_path.replace(".mask.png", ".rgba.png"),
        mask_path=mask_path,
        meta_path=mask_path.replace(".mask.png", ".json"),
    )


# ── Normal: union of two masks + restoreMask complement ──────────────────────

def test_union_and_restore_complement(tmp_path):
    """removal + restore must sum to 255 everywhere (binary complement)."""
    obj_dir = tmp_path / "objs"
    obj_dir.mkdir()

    # Object 0: 40×40 white rectangle at (10, 10) in the 200×150 image
    mask0 = np.zeros((40, 40), dtype=np.uint8)
    mask0[:, :] = 255
    mask0_path = str(obj_dir / "obj0.mask.png")
    _write_mask(Path(mask0_path), mask0)

    # Object 1: 30×30 white rectangle at (120, 80)
    mask1 = np.zeros((30, 30), dtype=np.uint8)
    mask1[:, :] = 255
    mask1_path = str(obj_dir / "obj1.mask.png")
    _write_mask(Path(mask1_path), mask1)

    extraction = ExtractionResult(
        job_id="test_job",
        objects=[
            _make_extracted_object("obj0", mask0_path, SourceBBox(x=10, y=10, width=40, height=40)),
            _make_extracted_object("obj1", mask1_path, SourceBBox(x=120, y=80, width=30, height=30)),
        ],
    )

    lg = _make_logger(tmp_path)
    result, removal = build(
        extraction=extraction,
        image_width=_W,
        image_height=_H,
        output_dir=str(tmp_path / "output"),
        job_id="test_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.PASS, result.reasons
    assert result.stage == StageName.REMOVAL_MASK
    assert removal is not None

    # Files must exist
    assert Path(removal.removal_mask_path).exists()
    assert Path(removal.restore_mask_path).exists()
    assert Path(removal.removal_json_path).exists()

    rem_arr = np.array(Image.open(removal.removal_mask_path).convert("L"), dtype=np.int16)
    res_arr = np.array(Image.open(removal.restore_mask_path).convert("L"), dtype=np.int16)

    # Complement invariant: removal + restore == 255 for every pixel
    assert np.all(rem_arr + res_arr == 255), (
        f"removal + restore != 255 for {np.count_nonzero((rem_arr + res_arr) != 255)} pixels"
    )

    # Object 0 region must be covered by removal mask (center of the region, away from edges)
    # 3px dilation means the interior of the 40×40 block should be solidly 255
    interior_rem = rem_arr[15:40, 15:45]  # interior of obj0 in full-image coords
    assert interior_rem.min() > 128, "interior of obj0 region should be in removal mask"

    # Object 1 region must also be covered
    interior_rem2 = rem_arr[85:105, 125:145]
    assert interior_rem2.min() > 128, "interior of obj1 region should be in removal mask"

    # Validate with validator
    is_valid, code, reason = validate(removal)
    assert is_valid, f"Validator failed: {code} — {reason}"


# ── Failure: all-zero masks → REMOVAL_MASK_EMPTY ─────────────────────────────

def test_zero_masks_removal_mask_empty(tmp_path):
    """Objects with all-zero masks must result in REMOVAL_MASK_EMPTY."""
    obj_dir = tmp_path / "objs"
    obj_dir.mkdir()

    # All-zero mask for each object
    for name in ("objA.mask.png", "objB.mask.png"):
        zero = np.zeros((50, 50), dtype=np.uint8)
        _write_mask(obj_dir / name, zero)

    extraction = ExtractionResult(
        job_id="fail_job",
        objects=[
            _make_extracted_object(
                "objA", str(obj_dir / "objA.mask.png"),
                SourceBBox(x=0, y=0, width=50, height=50),
                required=False,
            ),
            _make_extracted_object(
                "objB", str(obj_dir / "objB.mask.png"),
                SourceBBox(x=60, y=60, width=50, height=50),
                required=False,
            ),
        ],
    )

    lg = _make_logger(tmp_path, "fail_job")
    result, removal = build(
        extraction=extraction,
        image_width=_W,
        image_height=_H,
        output_dir=str(tmp_path / "output"),
        job_id="fail_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert removal is None
    assert any("REMOVAL_MASK_EMPTY" in r for r in result.reasons)


# ── Validator catches overlap (synthetic bug scenario) ────────────────────────

def test_validator_catches_mask_overlap(tmp_path):
    """Manually write overlapping removal+restore files; validator must return MASK_OVERLAP."""
    from worker.clean_pipeline.removal.models import RemovalMaskResult

    stage_dir = tmp_path / "output" / "test_job" / "clean_v1" / "04_removal"
    stage_dir.mkdir(parents=True)

    # Create a removal mask where a region is white
    rem_arr = np.zeros((_H, _W), dtype=np.uint8)
    rem_arr[10:60, 10:80] = 255
    rem_path = stage_dir / "removal_mask.png"
    _write_mask(rem_path, rem_arr)

    # Bug: restore mask is NOT the complement — it also has white in the same region
    # (simulates a code bug where restore_mask = removal_mask by mistake)
    res_path = stage_dir / "restore_mask.png"
    _write_mask(res_path, rem_arr)  # same as removal → overlap

    removal_json_path = stage_dir / "removal.json"
    removal_json_path.write_text("{}", encoding="utf-8")

    fake_result = RemovalMaskResult(
        job_id="test_job",
        image_width=_W,
        image_height=_H,
        source_object_count=1,
        dilation_px=3,
        removal_mask_path=str(rem_path),
        restore_mask_path=str(res_path),
        removal_json_path=str(removal_json_path),
    )

    is_valid, code, reason = validate(fake_result)

    assert not is_valid
    assert code == "MASK_OVERLAP"
    assert "foreground pixel" in reason
