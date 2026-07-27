"""P7 tests: LAYOUT stage.

Tests:
  1. Normal: required objects placed within safe zone; same input → same result
  2. Failure: required object too large for safe zone → NO_VALID_CANDIDATES
  3. SafeZone: geometry correctness
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from worker.clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject
from worker.clean_pipeline.contracts import PipelineStatus, StageName
from worker.clean_pipeline.extraction.models import (
    ExtractedObject,
    ExtractionResult,
    SourceBBox,
)
from worker.clean_pipeline.layout.layout_validator import run, validate
from worker.clean_pipeline.layout.models import SafeZone
from worker.clean_pipeline.layout.safe_zone import compute as compute_sz, contains
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.scene.models import ScenePlateResult

_TW, _TH = 300, 200   # target canvas


def _make_logger(tmp_path: Path, job_id: str = "test_job") -> PipelineLogger:
    return PipelineLogger(job_id, tmp_path / "pipeline.jsonl")


def _make_rgba(path: Path, w: int = 80, h: int = 60, color=(200, 100, 50, 200)) -> str:
    arr = np.full((h, w, 4), color, dtype=np.uint8)
    Image.fromarray(arr, "RGBA").save(str(path))
    return str(path)


def _make_scene_plate(path: Path, w: int = _TW, h: int = _TH) -> str:
    arr = np.random.randint(30, 180, (h, w, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(str(path))
    return str(path)


def _make_manifest(image_w: int = _TW, image_h: int = _TH) -> SceneManifest:
    return SceneManifest(
        job_id="test_job",
        source_sha256="abc",
        image_width=image_w,
        image_height=image_h,
        model="gpt-4o",
        objects=[
            SceneObject(
                id="prod_0",
                role="product",
                required=True,
                movable=True,
                removable_from_scene=True,
                bbox=BBox(x=10, y=10, width=80, height=60),
                polygon=[[10, 10], [90, 10], [90, 70], [10, 70]],
                confidence=0.95,
            ),
            SceneObject(
                id="cta_0",
                role="cta_group",
                required=True,
                movable=True,
                removable_from_scene=True,
                bbox=BBox(x=10, y=80, width=100, height=30),
                polygon=[[10, 80], [110, 80], [110, 110], [10, 110]],
                confidence=0.88,
            ),
        ],
    )


def _make_extraction(tmp_path: Path) -> ExtractionResult:
    d = tmp_path / "objs"
    d.mkdir(exist_ok=True)
    prod_rgba = _make_rgba(d / "prod_0.rgba.png", w=80, h=60)
    cta_rgba = _make_rgba(d / "cta_0.rgba.png", w=100, h=30)
    return ExtractionResult(
        job_id="test_job",
        objects=[
            ExtractedObject(
                id="prod_0", role="product", required=True, movable=True,
                removable_from_scene=True,
                source_bbox=SourceBBox(x=10, y=10, width=80, height=60),
                rgba_path=prod_rgba,
                mask_path=str(d / "prod_0.mask.png"),
                meta_path=str(d / "prod_0.json"),
            ),
            ExtractedObject(
                id="cta_0", role="cta_group", required=True, movable=True,
                removable_from_scene=True,
                source_bbox=SourceBBox(x=10, y=80, width=100, height=30),
                rgba_path=cta_rgba,
                mask_path=str(d / "cta_0.mask.png"),
                meta_path=str(d / "cta_0.json"),
            ),
        ],
    )


def _make_plate_result(tmp_path: Path) -> ScenePlateResult:
    scene = _make_scene_plate(tmp_path / "scene_plate.png")
    return ScenePlateResult(
        job_id="test_job",
        target_width=_TW,
        target_height=_TH,
        source_projection_path=scene,
        projected_removal_mask_path=scene,   # placeholder
        projected_restore_mask_path=scene,   # placeholder
        ai_cleanup_path=scene,               # placeholder
        scene_plate_path=scene,
        scene_json_path="",
        api_call_count=1,
    )


# ── SafeZone geometry ─────────────────────────────────────────────────────────

def test_safe_zone_geometry():
    sz = compute_sz(300, 200, safe_left=20, safe_top=15, safe_right=20, safe_bottom=15)
    assert sz.left == 20
    assert sz.top == 15
    assert sz.right == 280    # 300 - 20
    assert sz.bottom == 185   # 200 - 15
    assert sz.width == 260
    assert sz.height == 170


def test_safe_zone_contains():
    sz = SafeZone(left=20, top=15, right=280, bottom=185)
    assert contains(sz, x=20, y=15, w=100, h=50)      # aligned to safe zone left/top
    assert not contains(sz, x=19, y=15, w=100, h=50)  # x < left
    assert not contains(sz, x=200, y=15, w=100, h=50) # right edge = 300 > 280


# ── Normal: required objects placed in safe zone ──────────────────────────────

def test_required_objects_placed_in_safe_zone(tmp_path):
    extraction = _make_extraction(tmp_path)
    plate = _make_plate_result(tmp_path)
    manifest = _make_manifest()
    lg = _make_logger(tmp_path)

    result, layout = run(
        extraction=extraction,
        scene_plate_result=plate,
        manifest=manifest,
        safe_left=20, safe_top=15, safe_right=20, safe_bottom=15,
        output_dir=str(tmp_path / "output"),
        job_id="test_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.PASS, result.reasons
    assert result.stage == StageName.LAYOUT
    assert layout is not None
    assert len(layout.placed) >= 2   # both required objects placed

    sz = compute_sz(_TW, _TH, 20, 15, 20, 15)

    for po in layout.placed:
        if po.required:
            assert contains(sz, po.x, po.y, po.width, po.height), (
                f"Required object '{po.object_id}' at ({po.x},{po.y},{po.width},{po.height}) "
                f"is outside safe zone {sz}"
            )

    # Output files exist
    assert Path(layout.candidates_json_path).exists()
    assert Path(layout.layout_json_path).exists()
    assert Path(layout.layout_debug_path).exists()


# ── Deterministic: same input → same output ───────────────────────────────────

def test_same_input_produces_same_result(tmp_path):
    """Two runs with identical inputs must produce identical placed objects."""
    extraction = _make_extraction(tmp_path)
    plate = _make_plate_result(tmp_path)
    manifest = _make_manifest()

    def do_run(run_id: str):
        lg = _make_logger(tmp_path, run_id)
        _, layout = run(
            extraction=extraction,
            scene_plate_result=plate,
            manifest=manifest,
            safe_left=20, safe_top=15, safe_right=20, safe_bottom=15,
            output_dir=str(tmp_path / "output" / run_id),
            job_id=run_id,
            logger=lg,
        )
        lg.close()
        return layout

    layout_a = do_run("run_a")
    layout_b = do_run("run_b")

    assert layout_a is not None and layout_b is not None

    # Sort by object_id for stable comparison
    pa = sorted(layout_a.placed, key=lambda p: p.object_id)
    pb = sorted(layout_b.placed, key=lambda p: p.object_id)

    assert len(pa) == len(pb)
    for a, b in zip(pa, pb):
        assert a.object_id == b.object_id
        assert a.anchor == b.anchor
        assert a.scale == b.scale
        assert a.x == b.x and a.y == b.y
        assert a.width == b.width and a.height == b.height


# ── Failure: no valid candidates ──────────────────────────────────────────────

def test_no_valid_candidates_fails(tmp_path):
    """Required object (80×60) in a safe zone only 10px wide → all candidates fail."""
    d = tmp_path / "objs"
    d.mkdir()
    prod_rgba = _make_rgba(d / "prod.rgba.png", w=80, h=60)

    extraction = ExtractionResult(
        job_id="fail_job",
        objects=[
            ExtractedObject(
                id="prod_0", role="product", required=True, movable=True,
                removable_from_scene=True,
                source_bbox=SourceBBox(x=0, y=0, width=80, height=60),
                rgba_path=prod_rgba,
                mask_path=str(d / "prod.mask.png"),
                meta_path=str(d / "prod.json"),
            )
        ],
    )

    # Safe zone width = 300 - 145 - 145 = 10 px — smallest candidate is 80*0.6=48 > 10
    plate = _make_plate_result(tmp_path)
    manifest = SceneManifest(
        job_id="fail_job",
        source_sha256="x",
        image_width=_TW, image_height=_TH,
        model="gpt-4o",
        objects=[
            SceneObject(
                id="prod_0", role="product", required=True, movable=True,
                removable_from_scene=True,
                bbox=BBox(x=0, y=0, width=80, height=60),
                polygon=[[0,0],[80,0],[80,60],[0,60]],
                confidence=0.9,
            )
        ],
    )

    lg = _make_logger(tmp_path, "fail_job")
    result, layout = run(
        extraction=extraction,
        scene_plate_result=plate,
        manifest=manifest,
        safe_left=145, safe_top=10, safe_right=145, safe_bottom=10,
        output_dir=str(tmp_path / "output"),
        job_id="fail_job",
        logger=lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert layout is None
    assert any("NO_VALID_CANDIDATES" in r for r in result.reasons)
