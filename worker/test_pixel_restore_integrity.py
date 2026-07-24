"""Stage 2 tests: build_canonical_at_target + fail-closed pixel restore.

Verifies:
  - build_canonical_at_target returns target-sized image for both outpaint and cover_crop
  - Pixel in canonical_at_target at paste_offset matches source pixel at (0,0)
  - apply_default_immutable_policy raises on size mismatch (no silent return)
  - compute_immutable_metrics raises on size mismatch
  - allowedGenerationCoverage is NOT 1.0 when canonical_at_target is correct size
  - Source-mapped region is restored from canonical, not from AI result
  - New-canvas region keeps AI pixels

Zero actual AI/OpenAI requests.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image


# ── helpers ───────────────────────────────────────────────────────────────────

def _solid(w, h, color):
    return Image.new("RGB", (w, h), color)


def _make_outpaint_transform(src_w, src_h, tgt_w, tgt_h):
    """Return a SceneCanvasTransform with correct outpaint paste geometry."""
    from scene_cleanup.models import SceneCanvasTransform, TRANSFORM_STRATEGY_SUBJECT_PRESERVING_OUTPAINT
    scale = min(tgt_w / src_w, tgt_h / src_h)
    sw = max(int(src_w * scale + 0.5), 1)
    sh = max(int(src_h * scale + 0.5), 1)
    px = (tgt_w - sw) // 2
    py = (tgt_h - sh) // 2
    return SceneCanvasTransform(
        strategy=TRANSFORM_STRATEGY_SUBJECT_PRESERVING_OUTPAINT,
        source_w=src_w, source_h=src_h,
        canvas_w=tgt_w, canvas_h=tgt_h,
        scale=round(scale, 6),
        crop_x=0, crop_y=0,
        paste_offset_x=px, paste_offset_y=py,
        scaled_width=sw, scaled_height=sh,
        mapped_rect={"x1": px, "y1": py, "x2": px + sw, "y2": py + sh},
        outpaint_required=(sw < tgt_w or sh < tgt_h),
    )


def _make_cover_crop_transform(src_w, src_h, tgt_w, tgt_h):
    from scene_cleanup.models import SceneCanvasTransform, TRANSFORM_STRATEGY_COVER_CROP
    scale = max(tgt_w / src_w, tgt_h / src_h)
    scaled_w = max(int(src_w * scale + 0.5), tgt_w)
    scaled_h = max(int(src_h * scale + 0.5), tgt_h)
    cx = (scaled_w - tgt_w) // 2
    cy = (scaled_h - tgt_h) // 2
    return SceneCanvasTransform(
        strategy=TRANSFORM_STRATEGY_COVER_CROP,
        source_w=src_w, source_h=src_h,
        canvas_w=tgt_w, canvas_h=tgt_h,
        scale=round(scale, 6),
        crop_x=cx, crop_y=cy,
        outpaint_required=False,
    )


# ── build_canonical_at_target ─────────────────────────────────────────────────

class TestBuildCanonicalAtTarget:
    def test_outpaint_returns_target_size(self):
        from scene_cleanup.canvas_builder import build_canonical_at_target
        src = _solid(1200, 1200, (200, 100, 50))
        ct = _make_outpaint_transform(1200, 1200, 1250, 560)
        out = build_canonical_at_target(src, ct)
        assert out.size == (1250, 560)

    def test_cover_crop_returns_target_size(self):
        from scene_cleanup.canvas_builder import build_canonical_at_target
        src = _solid(1200, 628, (100, 150, 200))
        ct = _make_cover_crop_transform(1200, 628, 800, 600)
        out = build_canonical_at_target(src, ct)
        assert out.size == (800, 600)

    def test_unknown_strategy_returns_target_size(self):
        from scene_cleanup.canvas_builder import build_canonical_at_target
        from scene_cleanup.models import SceneCanvasTransform
        ct = SceneCanvasTransform(strategy="unknown_mode",
                                   canvas_w=400, canvas_h=300, scale=1.0)
        src = _solid(800, 600, (0, 0, 0))
        out = build_canonical_at_target(src, ct)
        assert out.size == (400, 300)

    def test_outpaint_source_pixel_at_paste_offset(self):
        """Pixel at (paste_offset_x, paste_offset_y) in canonical_at_target should match
        source pixel at (0, 0) after LANCZOS scaling."""
        from scene_cleanup.canvas_builder import build_canonical_at_target
        import numpy as np
        src = Image.new("RGB", (600, 600))
        src_arr = np.zeros((600, 600, 3), dtype=np.uint8)
        src_arr[0, 0] = [255, 0, 128]
        src = Image.fromarray(src_arr, "RGB")
        ct = _make_outpaint_transform(600, 600, 1250, 560)
        out = build_canonical_at_target(src, ct)
        assert out.size == (1250, 560)
        # Top-left of source lands at paste_offset in the canvas
        px = ct.paste_offset_x
        py = ct.paste_offset_y
        out_arr = np.array(out)
        # Pixel at (py, px) in the canvas corresponds to src (0,0)
        assert out_arr[py, px, 0] > 100, "Red channel at paste_offset should be high"

    def test_null_source_raises(self):
        from scene_cleanup.canvas_builder import build_canonical_at_target
        ct = _make_outpaint_transform(800, 600, 400, 300)
        with pytest.raises(RuntimeError, match="CANONICAL_BUILD_NULL_INPUT"):
            build_canonical_at_target(None, ct)

    def test_null_transform_raises(self):
        from scene_cleanup.canvas_builder import build_canonical_at_target
        src = _solid(400, 300, (50, 50, 50))
        with pytest.raises(RuntimeError, match="CANONICAL_BUILD_NULL_INPUT"):
            build_canonical_at_target(src, None)

    def test_invalid_target_raises(self):
        from scene_cleanup.canvas_builder import build_canonical_at_target
        from scene_cleanup.models import SceneCanvasTransform
        ct = SceneCanvasTransform(canvas_w=0, canvas_h=0)
        src = _solid(400, 300, (0, 0, 0))
        with pytest.raises(RuntimeError, match="CANONICAL_BUILD_INVALID_TARGET"):
            build_canonical_at_target(src, ct)

    def test_outpaint_outpaint_regions_are_black(self):
        """Regions outside the pasted source should be black (zero-fill)."""
        from scene_cleanup.canvas_builder import build_canonical_at_target
        src = _solid(600, 600, (200, 150, 100))
        ct = _make_outpaint_transform(600, 600, 1250, 560)  # wide target → left/right bars
        out = build_canonical_at_target(src, ct)
        out_arr = np.array(out)
        px = ct.paste_offset_x
        if px > 0:
            left_bar = out_arr[:, :px, :]
            # Left bar is black fill
            assert left_bar.max() == 0, "Outpaint region should be black"

    def test_cover_crop_is_rgb(self):
        from scene_cleanup.canvas_builder import build_canonical_at_target
        src = _solid(1200, 628, (10, 20, 30))
        ct = _make_cover_crop_transform(1200, 628, 800, 600)
        out = build_canonical_at_target(src, ct)
        assert out.mode == "RGB"


# ── pixel_restorer fail-closed on size mismatch ───────────────────────────────

class TestPixelRestoreSizeMismatch:
    """apply_default_immutable_policy raises RuntimeError on size mismatch."""

    def test_apply_immutable_raises_on_size_mismatch(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = _solid(400, 300, (100, 100, 100))  # original size
        ai_result = _solid(1250, 560, (200, 200, 200))  # target size
        mask = np.full((560, 1250), 255, dtype=np.uint8)
        with pytest.raises(RuntimeError, match="PIXEL_RESTORE_CANONICAL_SIZE_MISMATCH"):
            apply_default_immutable_policy(canonical, ai_result, mask)

    def test_compute_metrics_raises_on_size_mismatch(self):
        from scene_cleanup.pixel_restorer import compute_immutable_metrics
        canonical = _solid(400, 300, (100, 100, 100))
        ai_result = _solid(1250, 560, (200, 200, 200))
        mask = np.full((560, 1250), 255, dtype=np.uint8)
        with pytest.raises(RuntimeError, match="PIXEL_METRICS_CANONICAL_SIZE_MISMATCH"):
            compute_immutable_metrics(canonical, ai_result, mask)

    def test_apply_immutable_succeeds_with_matching_sizes(self):
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy
        canonical = _solid(1250, 560, (50, 50, 50))
        ai_result = _solid(1250, 560, (200, 200, 200))
        mask = np.full((560, 1250), 255, dtype=np.uint8)
        out = apply_default_immutable_policy(canonical, ai_result, mask)
        assert out.size == (1250, 560)


# ── allowedGenerationCoverage correct with canonical_at_target ────────────────

class TestCoverageWithCanonicalAtTarget:
    """allowedGenerationCoverage must reflect outpaint mask coverage, not 1.0."""

    def _run(self, src_w, src_h, tgt_w, tgt_h):
        from scene_cleanup.canvas_builder import build_canonical_at_target
        from scene_cleanup.pixel_restorer import compute_immutable_metrics
        src = _solid(src_w, src_h, (100, 100, 100))
        ct = _make_outpaint_transform(src_w, src_h, tgt_w, tgt_h)
        canonical_at_target = build_canonical_at_target(src, ct)
        ai_result = _solid(tgt_w, tgt_h, (200, 200, 200))

        # Build outpaint mask: white in outpaint regions, black in source region
        mask_arr = np.full((tgt_h, tgt_w), 255, dtype=np.uint8)
        x1, y1 = ct.paste_offset_x, ct.paste_offset_y
        x2, y2 = x1 + ct.scaled_width, y1 + ct.scaled_height
        if x2 > x1 and y2 > y1:
            mask_arr[y1:y2, x1:x2] = 0

        metrics = compute_immutable_metrics(canonical_at_target, ai_result, mask_arr)
        return metrics

    def test_coverage_not_1_for_wide_target(self):
        metrics = self._run(1200, 1200, 1250, 560)
        cov = metrics["allowedGenerationCoverage"]
        assert cov < 0.999, f"allowedGenerationCoverage={cov} should be < 0.999"

    def test_coverage_matches_outpaint_fraction(self):
        """Coverage should equal (outpaint area) / (total area)."""
        metrics = self._run(1200, 1200, 1250, 560)
        # For 1200×1200 → 1250×560: scale=560/1200=0.4667, sw=560, sh=560
        # offset_x=(1250-560)//2=345, left+right bars = 345+345 = 690px wide
        # outpaint_area = 1250*560 - 560*560 = 700000 - 313600 = 386400
        # total = 1250*560 = 700000
        # expected coverage ≈ 0.552
        cov = metrics["allowedGenerationCoverage"]
        assert 0.4 < cov < 0.7, f"Expected ~0.552, got {cov}"

    def test_square_to_square_coverage_zero(self):
        """Square→square: no outpaint regions → allowedGenerationCoverage≈0."""
        metrics = self._run(800, 800, 800, 800)
        cov = metrics["allowedGenerationCoverage"]
        assert cov < 0.01, f"No outpaint: coverage should be ~0, got {cov}"


# ── source-region pixel restoration ──────────────────────────────────────────

class TestPixelRestoration:
    """Pixels in source-mapped region are restored from canonical_at_target."""

    def test_source_region_restored_from_canonical(self):
        """AI changes source region → pixels restored to canonical."""
        from scene_cleanup.canvas_builder import build_canonical_at_target
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy

        src = _solid(600, 600, (100, 0, 0))
        ct = _make_outpaint_transform(600, 600, 1250, 560)
        canonical_at_target = build_canonical_at_target(src, ct)

        # AI result: AI fills everything red (changed source region too)
        ai_result = _solid(1250, 560, (200, 200, 200))

        # Mask: white=outpaint (AI edits), black=source region (immutable)
        mask_arr = np.full((560, 1250), 255, dtype=np.uint8)
        x1, y1 = ct.paste_offset_x, ct.paste_offset_y
        x2, y2 = x1 + ct.scaled_width, y1 + ct.scaled_height
        mask_arr[y1:y2, x1:x2] = 0

        restored = apply_default_immutable_policy(canonical_at_target, ai_result, mask_arr)
        restored_arr = np.array(restored)
        canonical_arr = np.array(canonical_at_target)

        # Source region in restored should match canonical_at_target
        src_restored = restored_arr[y1:y2, x1:x2]
        src_canonical = canonical_arr[y1:y2, x1:x2]
        assert np.allclose(src_restored.astype(float), src_canonical.astype(float), atol=2)

    def test_outpaint_region_keeps_ai_pixels(self):
        """Pixels in outpaint region should come from AI result, not canonical."""
        from scene_cleanup.canvas_builder import build_canonical_at_target
        from scene_cleanup.pixel_restorer import apply_default_immutable_policy

        src = _solid(600, 600, (50, 50, 50))
        ct = _make_outpaint_transform(600, 600, 1250, 560)
        canonical_at_target = build_canonical_at_target(src, ct)

        ai_result = _solid(1250, 560, (0, 200, 0))  # green

        mask_arr = np.full((560, 1250), 255, dtype=np.uint8)
        x1, y1 = ct.paste_offset_x, ct.paste_offset_y
        x2, y2 = x1 + ct.scaled_width, y1 + ct.scaled_height
        mask_arr[y1:y2, x1:x2] = 0

        restored = apply_default_immutable_policy(canonical_at_target, ai_result, mask_arr)
        restored_arr = np.array(restored)

        if x1 > 0:
            left_bar = restored_arr[:, :x1]
            # outpaint region should be green (from AI)
            assert left_bar[:, :, 1].mean() > 100, "Outpaint region should keep AI pixels (green)"
