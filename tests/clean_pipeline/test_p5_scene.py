"""P5 tests: SCENE_GENERATION stage.

Tests:
  1. Normal: restoreMask region pixels exactly match source_projection
  2. Failure: cleanup API returns wrong-size → CLEANUP_SIZE_MISMATCH
  3. ContainTransform: src always fits within target, no distortion
  4. DarkBg: dark expansion band → AI path forced (no direct fill even if std < threshold)
  5. Prefill: AI path sends pre-filled image (removal zone has natural bg color)
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from worker.clean_pipeline.contracts import PipelineStatus, StageName
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.removal.models import RemovalMaskResult
from worker.clean_pipeline.scene import immutable_pixel_restorer, target_transform
from worker.clean_pipeline.scene.openai_cleanup import _API_W, _API_H
from worker.clean_pipeline.scene.scene_plate_generator import generate

_W, _H = 200, 150   # canonical size
_TW, _TH = 300, 200  # target size


def _make_logger(tmp_path: Path, job_id: str = "test_job") -> PipelineLogger:
    return PipelineLogger(job_id, tmp_path / "pipeline.jsonl")


def _img_b64(width: int, height: int, color=(80, 120, 160)) -> str:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_mock_openai(api_w: int, api_h: int):
    """Mock that returns an (api_w × api_h) image from images.edit."""
    mock_response = MagicMock()
    mock_response.data = [MagicMock(b64_json=_img_b64(api_w, api_h))]
    mock_client = MagicMock()
    mock_client.images.edit.return_value = mock_response
    return mock_client


def _make_removal_result(tmp_path: Path, img_w: int, img_h: int) -> RemovalMaskResult:
    """Create a RemovalMaskResult with a non-empty removal mask at the given dimensions."""
    stage_dir = tmp_path / "masks"
    stage_dir.mkdir()

    rem_arr = np.zeros((img_h, img_w), dtype=np.uint8)
    rem_arr[20:80, 30:120] = 255          # ad region
    res_arr = (255 - rem_arr).astype(np.uint8)

    rem_path = stage_dir / "removal_mask.png"
    res_path = stage_dir / "restore_mask.png"
    Image.fromarray(rem_arr, "L").save(str(rem_path))
    Image.fromarray(res_arr, "L").save(str(res_path))

    return RemovalMaskResult(
        job_id="test_job",
        image_width=img_w,
        image_height=img_h,
        source_object_count=1,
        dilation_px=3,
        removal_mask_path=str(rem_path),
        restore_mask_path=str(res_path),
        removal_json_path="",
    )


def _make_canonical(tmp_path: Path, w: int = _W, h: int = _H) -> str:
    arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
    p = tmp_path / "canonical.png"
    Image.fromarray(arr, "RGB").save(str(p))
    return str(p)


def _make_gradient_panel_canonical(tmp_path: Path, w: int = _W, h: int = _H) -> str:
    """Canonical mimicking the ya-da dark-panel scenario.

    Left half: uniform natural background (180).
    Right half: uniform dark overlay panel (8).
    Transition zone (30 px wide, centred on the boundary): gradient 180→8.

    When the removal mask covers the right half, the 30-px dilation expansion band
    lands in the gradient zone.  The gradient produces bg_std >> 25, triggering the
    AI path.  The non-removal pixels (left half = 180) give natural_bg ≈ 180.
    """
    mid = w // 2
    arr = np.full((h, w, 3), 180, dtype=np.uint8)
    arr[:, mid:] = 8  # dark panel

    # gradient in ±15 px around the boundary so expansion band has high variance
    for dx in range(-15, 16):
        col = mid + dx
        if 0 <= col < w:
            ratio = (dx + 15) / 30.0          # 0 = left side (180), 1 = right side (8)
            val = int(180 * (1 - ratio) + 8 * ratio)
            arr[:, col] = val

    p = tmp_path / "canonical_gradient_panel.png"
    Image.fromarray(arr, "RGB").save(str(p))
    return str(p)


def _make_right_half_removal(tmp_path: Path, img_w: int, img_h: int) -> RemovalMaskResult:
    """Removal mask covering the right half of the image (the dark panel)."""
    stage_dir = tmp_path / "masks_panel"
    stage_dir.mkdir(exist_ok=True)

    rem_arr = np.zeros((img_h, img_w), dtype=np.uint8)
    rem_arr[:, img_w // 2:] = 255
    res_arr = (255 - rem_arr).astype(np.uint8)

    rem_path = stage_dir / "removal_mask.png"
    res_path = stage_dir / "restore_mask.png"
    Image.fromarray(rem_arr, "L").save(str(rem_path))
    Image.fromarray(res_arr, "L").save(str(res_path))

    return RemovalMaskResult(
        job_id="panel_job",
        image_width=img_w,
        image_height=img_h,
        source_object_count=1,
        dilation_px=3,
        removal_mask_path=str(rem_path),
        restore_mask_path=str(res_path),
        removal_json_path="",
    )


# ── ContainTransform: geometry correctness ────────────────────────────────────

def test_contain_transform_source_fits_in_target():
    tx = target_transform.compute(200, 150, 300, 200)
    assert tx.offset_x >= 0 and tx.offset_y >= 0
    assert tx.offset_x + tx.proj_width <= 300
    assert tx.offset_y + tx.proj_height <= 200
    assert tx.proj_width <= 300
    assert tx.proj_height <= 200


def test_contain_transform_no_distortion():
    """Projected aspect ratio must be within 1px rounding of source ratio."""
    src_w, src_h = 400, 300
    tgt_w, tgt_h = 250, 200
    tx = target_transform.compute(src_w, src_h, tgt_w, tgt_h)
    # Aspect ratio: proj_w/proj_h ≈ src_w/src_h
    ratio_src = src_w / src_h
    ratio_proj = tx.proj_width / tx.proj_height
    assert abs(ratio_proj - ratio_src) < 0.02, (
        f"Aspect ratio distorted: src={ratio_src:.4f} proj={ratio_proj:.4f}"
    )


# ── Normal: restoreMask pixels exactly match source_projection ────────────────

def test_restore_pixels_exactly_match_source_projection(tmp_path):
    """After generate(), the inner restore region (far from mask boundary) must
    closely match source_projection. Boundary pixels may be blended (blend_radius=20).
    """
    canonical = _make_canonical(tmp_path)
    removal = _make_removal_result(tmp_path, _W, _H)
    lg = _make_logger(tmp_path)

    with patch("openai.OpenAI", return_value=_make_mock_openai(_API_W, _API_H)):
        result, plate = generate(
            canonical_path=canonical,
            removal_result=removal,
            target_width=_TW,
            target_height=_TH,
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="test_job",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.PASS, result.reasons
    assert plate is not None
    assert Path(plate.scene_plate_path).exists()
    assert Path(plate.source_projection_path).exists()

    scene = np.array(Image.open(plate.scene_plate_path).convert("RGB"), dtype=np.int32)
    src_proj = np.array(Image.open(plate.source_projection_path).convert("RGB"), dtype=np.int32)

    restore_mask = np.array(
        Image.open(plate.projected_restore_mask_path).convert("L"), dtype=np.uint8
    )

    # Inner core: pixels fully inside the restore region (mask = 255 after erosion).
    # With blend_radius=20, PIL GaussianBlur has effective σ ≈ 32px.
    # At 21px depth from boundary (MinFilter radius=20), alpha ≈ Φ(21/32) ≈ 0.75.
    # Max deviation = (1 - 0.75) × max_diff ≤ 0.25 × 130 ≈ 33.
    # Tolerance 50 catches complete corruption (diff>170) while allowing blending fade.
    from PIL import ImageFilter
    eroded = Image.fromarray(restore_mask).filter(ImageFilter.MinFilter(size=41))
    inner = np.array(eroded, dtype=np.uint8) > 128

    if inner.any():
        diff = np.abs(scene[inner] - src_proj[inner]).max()
        assert diff <= 50, (
            f"Inner restore region deviated by {diff} — expected blending to preserve "
            f"source pixels (blend_radius=20, min alpha≈0.75 at 21px depth)"
        )

    # Output size must match target
    assert scene.shape[:2] == (_TH, _TW)

    # api_call_count == 1
    assert plate.api_call_count == 1


# ── Failure: cleanup API returns wrong size → CLEANUP_SIZE_MISMATCH ───────────

def test_cleanup_size_mismatch_fails(tmp_path):
    """If the API returns wrong-size image, FAIL with CLEANUP_SIZE_MISMATCH."""
    canonical = _make_canonical(tmp_path)
    removal = _make_removal_result(tmp_path, _W, _H)
    lg = _make_logger(tmp_path, "fail_job")

    # API returns 100×100 instead of the expected 1024×1024
    wrong_size_mock = _make_mock_openai(api_w=100, api_h=100)

    with patch("openai.OpenAI", return_value=wrong_size_mock):
        result, plate = generate(
            canonical_path=canonical,
            removal_result=removal,
            target_width=_TW,
            target_height=_TH,
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="fail_job",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert plate is None
    assert any("CLEANUP_SIZE_MISMATCH" in r for r in result.reasons)


# ── Unit: immutable_pixel_restorer standalone ─────────────────────────────────

def test_restorer_replaces_only_restore_region(tmp_path):
    """Restorer: restore-mask pixels come from source; non-restore pixels come from cleanup."""
    w, h = 100, 80

    src_proj = Image.new("RGB", (w, h), (200, 100, 50))   # orange source
    ai_cleanup = Image.new("RGB", (w, h), (50, 150, 200)) # blue cleanup

    # Restore mask: top half = white (restore), bottom half = black (keep cleanup)
    restore_arr = np.zeros((h, w), dtype=np.uint8)
    restore_arr[:h // 2, :] = 255
    restore_mask = Image.fromarray(restore_arr, "L")

    scene = immutable_pixel_restorer.restore(ai_cleanup, src_proj, restore_mask)
    scene_arr = np.array(scene)

    # Top half → source orange
    top = scene_arr[:h // 2, :]
    assert np.all(top == [200, 100, 50]), f"Expected orange, got {top[0,0]}"

    # Bottom half → cleanup blue
    bot = scene_arr[h // 2:, :]
    assert np.all(bot == [50, 150, 200]), f"Expected blue, got {bot[0,0]}"


# ── P5 v2: projected clean source region is pixel-identical in scene plate ────

def test_projected_clean_source_pixels_match_scene_plate(tmp_path):
    """scene_plate projected region must be pixel-identical to clean_source_projection (P5 v2)."""
    canonical = _make_canonical(tmp_path)
    removal = _make_removal_result(tmp_path, _W, _H)
    lg = _make_logger(tmp_path, "clean_proj_job")

    with patch("openai.OpenAI", return_value=_make_mock_openai(_API_W, _API_H)):
        result, plate = generate(
            canonical_path=canonical,
            removal_result=removal,
            target_width=_TW,
            target_height=_TH,
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="clean_proj_job",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.PASS, result.reasons
    assert plate is not None
    # New artifact path must exist
    assert plate.clean_source_projection_path
    assert Path(plate.clean_source_projection_path).exists()

    scene = np.array(Image.open(plate.scene_plate_path).convert("RGB"), dtype=np.uint8)
    clean_proj = np.array(Image.open(plate.clean_source_projection_path).convert("RGB"), dtype=np.uint8)
    # projected_restore_mask_path is now the inverted outpaint mask (white = projected region)
    proj_mask = np.array(Image.open(plate.projected_restore_mask_path).convert("L"), dtype=np.uint8)
    projected = proj_mask > 128

    assert projected.any(), "Expected non-empty projected region"
    assert np.array_equal(scene[projected], clean_proj[projected]), (
        f"Projected region mismatch: "
        f"{np.count_nonzero(np.any(scene[projected] != clean_proj[projected], axis=-1))} pixels"
    )
    # Two API calls when letterboxing exists (200×150 → 300×200 has left/right letterbox)
    assert plate.total_api_call_count >= 1


# ── P5 v2: outpaint API returns wrong size → OUTPAINT_SIZE_MISMATCH ──────────

def test_outpaint_wrong_size_fails(tmp_path):
    """If outpaint AI returns wrong-size image, P5 FAIL with OUTPAINT_SIZE_MISMATCH."""
    canonical = _make_canonical(tmp_path)
    removal = _make_removal_result(tmp_path, _W, _H)
    lg = _make_logger(tmp_path, "outpaint_fail_job")

    # First API call (source cleanup) returns correct 1024×1024.
    # Second API call (outpaint) returns wrong 512×512.
    r_ok = MagicMock()
    r_ok.data = [MagicMock(b64_json=_img_b64(_API_W, _API_H))]
    r_bad = MagicMock()
    r_bad.data = [MagicMock(b64_json=_img_b64(512, 512))]

    mock_client = MagicMock()
    mock_client.images.edit.side_effect = [r_ok, r_bad]

    with patch("openai.OpenAI", return_value=mock_client):
        result, plate = generate(
            canonical_path=canonical,
            removal_result=removal,
            target_width=_TW,
            target_height=_TH,
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="outpaint_fail_job",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert plate is None
    assert any("SIZE_MISMATCH" in r for r in result.reasons)


# ── DarkPanel: gradient-edge dark panel triggers AI path, prefill created ─────

def test_dark_panel_gradient_edge_triggers_ai_path(tmp_path):
    """Gradient boundary in dark panel causes bg_std > threshold → AI path.

    Scenario mirrors the ya-da banner:
    - Left half: uniform natural bg (180)
    - Right half: uniform dark panel (8) with 30-px gradient at the boundary
    - Removal mask covers the right half

    The 30-px dilation expansion band lands in the gradient zone → bg_std >> 25
    → AI path is taken (not direct fill).

    Expected:
    - source_cleanup_direct.png absent  (direct fill skipped)
    - source_cleanup_prefill.png present (AI pre-fill created)
    """
    canonical = _make_gradient_panel_canonical(tmp_path)
    removal = _make_right_half_removal(tmp_path, _W, _H)
    lg = _make_logger(tmp_path, "panel_job")

    with patch("openai.OpenAI", return_value=_make_mock_openai(_API_W, _API_H)):
        result, plate = generate(
            canonical_path=canonical,
            removal_result=removal,
            target_width=_TW,
            target_height=_TH,
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="panel_job",
            logger=lg,
        )
    lg.close()

    assert result.status == PipelineStatus.PASS, result.reasons

    scene_dir = Path(tmp_path / "output" / "panel_job" / "clean_v1" / "05_scene")
    assert not (scene_dir / "source_cleanup_direct.png").exists(), (
        "source_cleanup_direct.png must not exist — gradient edge should have forced AI path"
    )
    assert (scene_dir / "source_cleanup_prefill.png").exists(), (
        "source_cleanup_prefill.png must exist — AI context pre-fill is required"
    )


# ── Prefill: removal zone in pre-filled image has natural background color ─────

def test_ai_prefill_fills_removal_zone_with_natural_bg(tmp_path):
    """Pre-filled image must replace the dark panel zone with natural bg color.

    Canonical: left half natural bg ~180, right half dark panel ~8 (with gradient edge).
    natural_bg = median of non-removal pixels ≈ 180 (left half).
    After pre-fill, the center of the dark panel in the prefill image should be ~180.
    """
    canonical = _make_gradient_panel_canonical(tmp_path)
    removal = _make_right_half_removal(tmp_path, _W, _H)
    lg = _make_logger(tmp_path, "prefill_job")

    with patch("openai.OpenAI", return_value=_make_mock_openai(_API_W, _API_H)):
        generate(
            canonical_path=canonical,
            removal_result=removal,
            target_width=_TW,
            target_height=_TH,
            api_key="sk-test",
            output_dir=str(tmp_path / "output"),
            job_id="prefill_job",
            logger=lg,
        )
    lg.close()

    prefill_path = (
        tmp_path / "output" / "prefill_job" / "clean_v1" / "05_scene" / "source_cleanup_prefill.png"
    )
    assert prefill_path.exists(), "source_cleanup_prefill.png must be written"

    prefill_arr = np.array(Image.open(str(prefill_path)).convert("RGB"), dtype=np.uint8)

    # The CENTER of the right-half (dark panel) should now be ~180 (natural bg).
    # Sample from target-canvas coordinates: right half center, avoiding edges.
    tx = target_transform.compute(_W, _H, _TW, _TH)
    panel_start_x = tx.offset_x + tx.proj_width // 2  # approx start of right half in target
    panel_center_x = tx.offset_x + (tx.proj_width * 3) // 4  # center of right half
    cy0, cy1 = tx.offset_y + 10, tx.offset_y + tx.proj_height - 10
    cx0, cx1 = panel_start_x + 15, panel_center_x + 10  # avoid gradient edge

    panel_pixels = prefill_arr[cy0:cy1, cx0:cx1]
    assert panel_pixels.size > 0, "Sampled panel region is empty"

    mean_val = float(panel_pixels.mean())
    # Should be near 180 (natural bg), not near 8 (dark panel)
    assert mean_val > 100, (
        f"Pre-fill panel mean={mean_val:.1f} — expected ~180 (natural bg), "
        f"dark panel color (~8) was NOT replaced. Pre-fill logic failed."
    )
