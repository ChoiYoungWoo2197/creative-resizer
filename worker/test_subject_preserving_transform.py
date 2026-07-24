"""Stage 1 tests: SceneCanvasTransform paste-offset geometry.

Verifies that build_provider_canvas_outpaint correctly populates
paste_offset_x/y, scaled_width, scaled_height, and mapped_rect on
SceneCanvasTransform for various source→target aspect ratios.

Zero actual AI/OpenAI requests.
"""
from __future__ import annotations

import math
import pytest
from PIL import Image


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_source(w: int, h: int, color=(200, 100, 50)) -> object:
    return Image.new("RGB", (w, h), color)


def _full_image_source(img: object) -> object:
    from scene_cleanup.models import FullImageSource
    return FullImageSource(
        image=img,
        source_path="test.png",
        source_type="png",
        source_file_sha256="",
        composite_sha256="",
        width=img.width,
        height=img.height,
        has_native_layers=False,
        composite_render_method="png",
    )


def _build_outpaint(src_w, src_h, tgt_w, tgt_h):
    from scene_cleanup.canvas_builder import build_provider_canvas_outpaint
    img = _make_source(src_w, src_h)
    fis = _full_image_source(img)
    return build_provider_canvas_outpaint(fis, tgt_w, tgt_h)


# ── SceneCanvasTransform has new fields ───────────────────────────────────────

class TestSceneCanvasTransformFields:
    def test_default_fields_are_zero(self):
        from scene_cleanup.models import SceneCanvasTransform
        ct = SceneCanvasTransform()
        assert ct.paste_offset_x == 0
        assert ct.paste_offset_y == 0
        assert ct.scaled_width == 0
        assert ct.scaled_height == 0
        assert ct.mapped_rect is None

    def test_fields_can_be_set(self):
        from scene_cleanup.models import SceneCanvasTransform
        ct = SceneCanvasTransform(
            paste_offset_x=345,
            paste_offset_y=0,
            scaled_width=560,
            scaled_height=560,
            mapped_rect={"x1": 345, "y1": 0, "x2": 905, "y2": 560},
        )
        assert ct.paste_offset_x == 345
        assert ct.paste_offset_y == 0
        assert ct.scaled_width == 560
        assert ct.scaled_height == 560
        assert ct.mapped_rect == {"x1": 345, "y1": 0, "x2": 905, "y2": 560}


# ── build_provider_canvas_outpaint correctness ───────────────────────────────

class TestOutpaintCanvasGeometry:
    """Verify paste-offset fields across multiple aspect-ratio pairs."""

    def _check_transform(self, src_w, src_h, tgt_w, tgt_h):
        _, _, ct, _ = _build_outpaint(src_w, src_h, tgt_w, tgt_h)
        scale = min(tgt_w / src_w, tgt_h / src_h)
        exp_scaled_w = max(int(src_w * scale + 0.5), 1)
        exp_scaled_h = max(int(src_h * scale + 0.5), 1)
        exp_off_x = (tgt_w - exp_scaled_w) // 2
        exp_off_y = (tgt_h - exp_scaled_h) // 2
        return ct, scale, exp_scaled_w, exp_scaled_h, exp_off_x, exp_off_y

    # 1200×1200 → 1250×560  (wide target → left/right bars)
    def test_square_to_wide_offset_x(self):
        ct, _, sw, sh, off_x, off_y = self._check_transform(1200, 1200, 1250, 560)
        assert ct.paste_offset_x == off_x
        assert ct.paste_offset_y == off_y
        assert off_x > 0, "wide target: left bar should be non-zero"
        assert off_y == 0

    def test_square_to_wide_scaled_dims(self):
        ct, scale, sw, sh, off_x, off_y = self._check_transform(1200, 1200, 1250, 560)
        assert ct.scaled_width == sw
        assert ct.scaled_height == sh

    def test_square_to_wide_mapped_rect(self):
        ct, _, sw, sh, off_x, off_y = self._check_transform(1200, 1200, 1250, 560)
        mr = ct.mapped_rect
        assert mr is not None
        assert mr["x1"] == off_x
        assert mr["y1"] == off_y
        assert mr["x2"] == off_x + sw
        assert mr["y2"] == off_y + sh

    # 1200×628 → 300×1200  (tall target → top/bottom bars)
    def test_wide_to_tall_offset_y(self):
        ct, _, sw, sh, off_x, off_y = self._check_transform(1200, 628, 300, 1200)
        assert ct.paste_offset_x == off_x
        assert ct.paste_offset_y == off_y
        assert off_y > 0, "tall target: top bar should be non-zero"

    def test_wide_to_tall_crop_x_is_zero(self):
        ct, _, _, _, _, _ = self._check_transform(1200, 628, 300, 1200)
        # outpaint mode: crop_x / crop_y remain 0 (no cropping)
        assert ct.crop_x == 0
        assert ct.crop_y == 0

    # 300×1200 → 1200×300
    def test_tall_to_wide(self):
        ct, _, sw, sh, off_x, off_y = self._check_transform(300, 1200, 1200, 300)
        assert ct.paste_offset_x == off_x
        assert ct.paste_offset_y == off_y

    # square → square: no bars
    def test_square_to_square_no_bars(self):
        ct, _, sw, sh, off_x, off_y = self._check_transform(800, 800, 800, 800)
        assert ct.paste_offset_x == 0
        assert ct.paste_offset_y == 0
        assert ct.scaled_width == 800
        assert ct.scaled_height == 800

    # Provider input is target-sized
    def test_provider_input_size(self):
        img, mask, ct, _ = _build_outpaint(1200, 1200, 1250, 560)
        assert img.size == (1250, 560)
        assert mask.size == (1250, 560)

    # allowed_generation_mask equals mask array
    def test_allowed_generation_mask_matches_mask(self):
        import numpy as np
        _, mask, ct, allowed = _build_outpaint(1200, 1200, 1250, 560)
        mask_arr = np.array(mask)
        assert np.array_equal(mask_arr, allowed)

    # Mask is white in outpaint regions, black in source region
    def test_mask_source_region_black(self):
        import numpy as np
        _, mask, ct, _ = _build_outpaint(1200, 1200, 1250, 560)
        mask_arr = np.array(mask)
        px = ct.paste_offset_x
        py = ct.paste_offset_y
        sw = ct.scaled_width
        sh = ct.scaled_height
        # source region should be 0 (immutable)
        src_region = mask_arr[py:py + sh, px:px + sw]
        assert src_region.max() == 0, "source region mask must be black (immutable)"

    def test_mask_outpaint_region_white(self):
        import numpy as np
        _, mask, ct, _ = _build_outpaint(1200, 1200, 1250, 560)
        mask_arr = np.array(mask)
        px = ct.paste_offset_x
        # left bar should be white
        if px > 0:
            left_bar = mask_arr[:, :px]
            assert left_bar.min() == 255, "outpaint left bar must be white"

    def test_strategy_is_outpaint(self):
        from scene_cleanup.models import TRANSFORM_STRATEGY_SUBJECT_PRESERVING_OUTPAINT
        _, _, ct, _ = _build_outpaint(1200, 1200, 1250, 560)
        assert ct.strategy == TRANSFORM_STRATEGY_SUBJECT_PRESERVING_OUTPAINT


# ── diagnostic_logger uses paste_offset not crop_x ──────────────────────────

class TestTransformGeometryLog:
    """[TRANSFORM_GEOMETRY] must report actualOffset from paste_offset_x/y."""

    def _capture_log(self, src_w, src_h, tgt_w, tgt_h):
        import io, contextlib
        _, _, ct, _ = _build_outpaint(src_w, src_h, tgt_w, tgt_h)
        from verdict.diagnostic_logger import log_transform_geometry
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            log_transform_geometry(ct, source_w=src_w, source_h=src_h,
                                   target_w=tgt_w, target_h=tgt_h,
                                   job_id="jt1", spec_id=f"{tgt_w}x{tgt_h}")
        return buf.getvalue(), ct

    def test_geometry_valid_when_paste_offsets_correct(self):
        line, ct = self._capture_log(1200, 1200, 1250, 560)
        assert "geometryValid=True" in line, f"Expected geometryValid=True, got: {line}"

    def test_actual_offset_uses_paste_offset(self):
        line, ct = self._capture_log(1200, 1200, 1250, 560)
        off_x = ct.paste_offset_x
        off_y = ct.paste_offset_y
        assert f"actualOffset={off_x},{off_y}" in line, f"actualOffset mismatch in: {line}"

    def test_no_offset_mismatch_reason_code(self):
        line, _ = self._capture_log(1200, 628, 300, 1200)
        assert "OFFSET_X_MISMATCH" not in line
        assert "OFFSET_Y_MISMATCH" not in line

    def test_crop_x_zero_does_not_cause_mismatch(self):
        # crop_x=0, paste_offset_x>0 — log must not flag as mismatch
        line, ct = self._capture_log(1200, 1200, 1250, 560)
        assert ct.crop_x == 0
        assert ct.paste_offset_x > 0
        assert "OFFSET_X_MISMATCH" not in line, f"False mismatch triggered: {line}"

    def test_mapped_rect_uses_stored_value(self):
        line, ct = self._capture_log(1200, 1200, 1250, 560)
        px = ct.paste_offset_x
        py = ct.paste_offset_y
        sw = ct.scaled_width
        sh = ct.scaled_height
        expected_rect = f"{{'x1': {px}, 'y1': {py}, 'x2': {px + sw}, 'y2': {py + sh}}}"
        assert f"mappedRect={expected_rect}" in line, f"mappedRect mismatch in: {line}"

    def test_expected_offset_matches_actual_offset(self):
        line, ct = self._capture_log(1200, 1200, 1250, 560)
        off_x = ct.paste_offset_x
        off_y = ct.paste_offset_y
        assert f"expectedOffset={off_x},{off_y}" in line, f"expectedOffset mismatch: {line}"
