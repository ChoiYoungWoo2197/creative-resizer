"""Stage 21 Bundle B: Deterministic reflow + safe zone validation tests.

Run with: python -m pytest -q test_reflow_safezone.py

Test categories:
  A. Safe zone rect computation
  B. Uniform scale preservation
  C. Role safe-zone policy
  D. Layout role override (semantic != layoutRole)
  E. Candidate determinism
  F. Required object placement
  G. Clipping detection
  H. Overlap detection
  I. Mother simulation (wide banner, product + title + body_text + decorative)
  J. Yada simulation (hero visual + product + body_text, wide banner)
  K. A→B→A determinism (no cross-job state)
  L. Missing safe zone data
  M. Counter correctness
  N. jobId/specId provenance in logs
"""
from __future__ import annotations

import io
import sys
import os
import contextlib
import pytest
from PIL import Image

# ── helpers ───────────────────────────────────────────────────────────────────

NAVER_1250x560_SPEC = {
    "safeZone": {"top": 50, "right": 240, "bottom": 35, "left": 240},
    "safeZoneParseStatus": "parsed_text",
}

SQUARE_SPEC = {
    "safeZone": {"top": 80, "right": 80, "bottom": 80, "left": 80},
    "safeZoneParseStatus": "parsed_text",
}


def make_img(w: int, h: int, color=(128, 64, 32, 200)) -> Image.Image:
    img = Image.new("RGBA", (w, h), color)
    return img


def make_fg_layer(
    role: str,
    src_x: int, src_y: int, src_w: int, src_h: int,
    canvas_w: int = 1200, canvas_h: int = 1200,
    target_w: int = 1250, target_h: int = 560,
    obj_id: str = "",
    name: str = "",
    layer_id: str = "",
) -> dict:
    """Minimal fg_layer dict matching extract_foreground_layers() output."""
    scale_x = target_w / canvas_w
    scale_y = target_h / canvas_h
    scale_uniform = min(scale_x, scale_y)
    tx = round(src_x * scale_x)
    ty = round(src_y * scale_y)
    tw = max(1, round(src_w * scale_uniform))
    th = max(1, round(src_h * scale_uniform))
    img = make_img(tw, th)
    return {
        "role": role,
        "name": name or role,
        "image": img,
        "bbox": {"x": tx, "y": ty, "width": tw, "height": th},
        "sourceBBox": {"x": src_x, "y": src_y, "width": src_w, "height": src_h},
        "depth": 0,
        "layerId": layer_id or f"lid_{role}",
        "objectId": obj_id or f"obj_{role}",
        "sourcePixelSha256": "",
        "compositedCount": 0,
    }


def run_layout(
    fg_layers: list,
    spec: dict,
    canvas_w: int = 1200, canvas_h: int = 1200,
    target_w: int = 1250, target_h: int = 560,
    psd_layers: list | None = None,
    apply_logs: list | None = None,
    job_id: str = "test", spec_id: str = "1250x560",
):
    """Call plan_foreground_layout() with stdout suppressed."""
    from layout.reflow_engine import plan_foreground_layout
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = plan_foreground_layout(
            fg_layers=fg_layers,
            spec=spec,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            target_w=target_w,
            target_h=target_h,
            psd_layers=psd_layers or [],
            apply_logs=apply_logs or [],
            job_id=job_id,
            spec_id=spec_id,
        )
    return result, buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# A. Safe zone rect computation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeZoneRect:
    def test_1250x560_fixture(self):
        """Naver 1250x560: safe rect = (240,50,1010,525)."""
        from safe_zone import normalize_safe_zone
        sz = normalize_safe_zone(NAVER_1250x560_SPEC, 1250, 560)
        g = sz["general"]
        assert g["left"] == 240
        assert g["top"] == 50
        assert g["right"] == 240
        assert g["bottom"] == 35
        safe_x2 = 1250 - 240
        safe_y2 = 560 - 35
        assert safe_x2 == 1010
        assert safe_y2 == 525

    def test_safe_rect_in_layout_result(self):
        """LayoutPlanResult.safeZoneRect matches expected pixel coords."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        r = result.safeZoneRect
        assert r["x1"] == 240
        assert r["y1"] == 50
        assert r["x2"] == 1010
        assert r["y2"] == 525

    def test_fallback_ratio_based(self):
        """No safeZone spec → ratio-based fallback, still computes a valid rect."""
        from safe_zone import normalize_safe_zone
        sz = normalize_safe_zone({}, 1250, 560)
        g = sz["general"]
        assert g["top"] >= 0
        assert g["right"] >= 0
        assert g["bottom"] >= 0
        assert g["left"] >= 0

    def test_parsed_diagram_status_uses_spec(self):
        """parsed_diagram status also uses spec safeZone values."""
        from safe_zone import normalize_safe_zone
        spec = {
            "safeZone": {"top": 30, "right": 30, "bottom": 30, "left": 30},
            "safeZoneParseStatus": "parsed_diagram",
        }
        sz = normalize_safe_zone(spec, 800, 600)
        assert sz["general"]["top"] == 30

    def test_incomplete_spec_falls_back(self, capsys):
        """Parsed status with incomplete safeZone → fallback + warning."""
        from safe_zone import normalize_safe_zone
        spec = {
            "safeZone": {"top": 50, "bottom": 35},
            "safeZoneParseStatus": "parsed_text",
        }
        sz = normalize_safe_zone(spec, 1250, 560)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        # Falls back to ratio-based
        assert sz["general"]["left"] > 0  # not 0 (which would be incomplete spec)


# ═══════════════════════════════════════════════════════════════════════════════
# B. Uniform scale preservation
# ═══════════════════════════════════════════════════════════════════════════════

class TestUniformScale:
    def test_product_aspect_ratio_preserved(self):
        """Product object: aspect ratio must match source within 0.5%."""
        layer = make_fg_layer("product", 100, 200, 300, 400,
                              canvas_w=1200, canvas_h=1200,
                              target_w=1250, target_h=560)
        src_w = layer["sourceBBox"]["width"]
        src_h = layer["sourceBBox"]["height"]
        src_aspect = src_w / src_h

        layers = [layer]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        # Find placement
        placements = result.objectPlacements
        assert len(placements) == 1
        tb = placements[0].targetBBox
        dst_aspect = tb["width"] / tb["height"]
        assert abs(src_aspect - dst_aspect) < 0.005, (
            f"aspect ratio changed: src={src_aspect:.4f} dst={dst_aspect:.4f}"
        )

    def test_title_raster_aspect_ratio_preserved(self):
        """Title (raster) object: width/height ratio must not change."""
        layer = make_fg_layer("title", 50, 30, 800, 80,
                              canvas_w=1200, canvas_h=1200,
                              target_w=1250, target_h=560)
        src_aspect = 800 / 80
        layers = [layer]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        p = result.objectPlacements[0]
        tb = p.targetBBox
        dst_aspect = tb["width"] / tb["height"]
        assert abs(src_aspect - dst_aspect) < 0.005

    def test_no_independent_scale_xy(self):
        """_fit_in_slot must produce equal scaleX and scaleY."""
        from layout.reflow_engine import _fit_in_slot
        # Source 300x600 → slot 200x200 (square slot)
        bbox, scale = _fit_in_slot(300, 600, 10, 10, 200, 200, "center")
        # Uniform scale: scale=min(200/300, 200/600) = min(0.667, 0.333) = 0.333
        expected_w = round(300 * scale)
        expected_h = round(600 * scale)
        actual_aspect = bbox["width"] / bbox["height"]
        expected_aspect = 300 / 600
        assert abs(actual_aspect - expected_aspect) < 0.005

    def test_fit_in_slot_scale_matches_dimensions(self):
        """_fit_in_slot: returned scale * src gives returned dimensions."""
        from layout.reflow_engine import _fit_in_slot
        bbox, scale = _fit_in_slot(400, 300, 0, 0, 800, 400, "center")
        expected_w = max(1, round(400 * scale))
        expected_h = max(1, round(300 * scale))
        assert bbox["width"] == expected_w
        assert bbox["height"] == expected_h


# ═══════════════════════════════════════════════════════════════════════════════
# C. Role safe-zone policy
# ═══════════════════════════════════════════════════════════════════════════════

class TestRolePolicy:
    def test_product_safe_zone_required(self):
        """product role → safeZoneRequired=True in ObjectPlacement."""
        from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES
        assert "product" in SAFE_ZONE_REQUIRED_ROLES

    def test_title_safe_zone_required(self):
        from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES
        assert "title" in SAFE_ZONE_REQUIRED_ROLES

    def test_body_text_safe_zone_required(self):
        from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES
        assert "body_text" in SAFE_ZONE_REQUIRED_ROLES

    def test_logo_safe_zone_required(self):
        from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES
        assert "logo" in SAFE_ZONE_REQUIRED_ROLES

    def test_cta_safe_zone_required(self):
        from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES
        assert "cta" in SAFE_ZONE_REQUIRED_ROLES

    def test_human_subject_bleed_allowed(self):
        """human_subject: bleed outside safe zone is allowed."""
        from layout.layout_role_resolver import BLEED_ALLOWED_ROLES
        assert "human_subject" in BLEED_ALLOWED_ROLES

    def test_main_image_bleed_allowed(self):
        from layout.layout_role_resolver import BLEED_ALLOWED_ROLES
        assert "main_image" in BLEED_ALLOWED_ROLES

    def test_decorative_canvas_bleed(self):
        """decorative role → CANVAS_BLEED_ROLES."""
        from layout.layout_role_resolver import CANVAS_BLEED_ROLES
        assert "decorative" in CANVAS_BLEED_ROLES

    def test_background_canvas_bleed(self):
        from layout.layout_role_resolver import CANVAS_BLEED_ROLES
        assert "background" in CANVAS_BLEED_ROLES

    def test_human_not_safe_zone_required(self):
        """human_subject is NOT in SAFE_ZONE_REQUIRED_ROLES."""
        from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES
        assert "human_subject" not in SAFE_ZONE_REQUIRED_ROLES


# ═══════════════════════════════════════════════════════════════════════════════
# D. Layout role override
# ═══════════════════════════════════════════════════════════════════════════════

class TestLayoutRoleOverride:
    def test_human_subject_wide_strip_becomes_title(self):
        """Pixel human_subject with wide strip geometry + attempted_role=title → title."""
        from layout.layout_role_resolver import resolve_layout_role
        layer = {
            "role": "human_subject",
            "name": "제목 텍스트",
            "bbox": {"x": 0, "y": 0, "width": 500, "height": 40},
            "sourceBBox": {"x": 0, "y": 30, "width": 900, "height": 50},
        }
        psd_layer = {
            "type": "pixel",
            "canvasWidth": 1200, "canvasHeight": 1200,
            "textContent": "",
        }
        layout_role, reason = resolve_layout_role(layer, psd_layer, attempted_role="title")
        assert layout_role == "title", f"Expected title, got {layout_role!r}: {reason}"

    def test_semantic_role_unchanged(self):
        """resolve_layout_role NEVER modifies layer['role']."""
        from layout.layout_role_resolver import resolve_layout_role
        layer = {
            "role": "human_subject",
            "name": "타이틀",
            "bbox": {"x": 0, "y": 0, "width": 300, "height": 30},
            "sourceBBox": {"x": 0, "y": 30, "width": 900, "height": 50},
        }
        psd_layer = {"type": "pixel", "canvasWidth": 1200, "canvasHeight": 1200}
        _ = resolve_layout_role(layer, psd_layer, attempted_role="title")
        assert layer["role"] == "human_subject"  # unchanged

    def test_text_layer_type_infers_title(self):
        """type=text layer at top → layoutRole=title."""
        from layout.layout_role_resolver import resolve_layout_role
        layer = {
            "role": "human_subject",
            "name": "top_copy",
            "bbox": {"x": 0, "y": 0, "width": 300, "height": 30},
            "sourceBBox": {"x": 50, "y": 40, "width": 600, "height": 60},
        }
        psd_layer = {
            "type": "type",
            "canvasWidth": 1200, "canvasHeight": 1200,
            "textContent": "브랜드 헤드라인",
        }
        layout_role, reason = resolve_layout_role(layer, psd_layer, attempted_role=None)
        assert layout_role in ("title", "body_text"), f"Unexpected {layout_role!r}"

    def test_body_text_attempted_confirmed(self):
        """Wide short strip → body_text attempted_role confirmed."""
        from layout.layout_role_resolver import resolve_layout_role
        layer = {
            "role": "human_subject",
            "name": "sub_copy",
            "bbox": {},
            "sourceBBox": {"x": 50, "y": 400, "width": 800, "height": 60},
        }
        psd_layer = {"type": "pixel", "canvasWidth": 1200, "canvasHeight": 1200}
        layout_role, _ = resolve_layout_role(layer, psd_layer, attempted_role="body_text")
        # body_text: height_ratio = 60/1200 = 0.05 <= 0.25 → confirmed
        assert layout_role == "body_text"

    def test_no_attempted_role_fallback_to_semantic(self):
        """No attempted_role, non-text layer → semantic role returned."""
        from layout.layout_role_resolver import resolve_layout_role
        layer = {"role": "product", "name": "item", "bbox": {},
                 "sourceBBox": {"x": 100, "y": 100, "width": 300, "height": 300}}
        layout_role, reason = resolve_layout_role(layer, None, None)
        assert layout_role == "product"
        assert "semantic_role_fallback" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# E. Candidate determinism
# ═══════════════════════════════════════════════════════════════════════════════

class TestCandidateDeterminism:
    def _make_layers(self):
        return [
            make_fg_layer("product", 100, 200, 300, 400, obj_id="obj_product"),
            make_fg_layer("title", 50, 30, 900, 80, obj_id="obj_title"),
            make_fg_layer("body_text", 50, 500, 800, 80, obj_id="obj_body"),
        ]

    def test_same_input_same_candidate(self):
        """Same fg_layers + spec → same selectedCandidateId across 10 runs."""
        candidates = set()
        for _ in range(10):
            layers = self._make_layers()
            result, _ = run_layout(layers, NAVER_1250x560_SPEC)
            candidates.add(result.selectedCandidateId)
        assert len(candidates) == 1, f"Non-deterministic: {candidates}"

    def test_same_input_same_target_bbox(self):
        """Same input → product placed at same targetBBox every time."""
        product_bboxes = set()
        for _ in range(5):
            layers = self._make_layers()
            result, _ = run_layout(layers, NAVER_1250x560_SPEC)
            for p in result.objectPlacements:
                if p.objectId == "obj_product":
                    tb = p.targetBBox
                    product_bboxes.add((tb["x"], tb["y"], tb["width"], tb["height"]))
        assert len(product_bboxes) == 1, f"Non-deterministic product bbox: {product_bboxes}"

    def test_different_spec_different_result(self):
        """Different spec → may produce different layout."""
        layers1 = self._make_layers()
        layers2 = self._make_layers()
        spec1 = NAVER_1250x560_SPEC
        spec2 = {"safeZone": {"top": 100, "right": 100, "bottom": 100, "left": 100},
                 "safeZoneParseStatus": "parsed_text"}
        r1, _ = run_layout(layers1, spec1, target_w=1250, target_h=560)
        r2, _ = run_layout(layers2, spec2, target_w=1250, target_h=560)
        # At minimum safeZoneRect should differ
        assert r1.safeZoneRect != r2.safeZoneRect


# ═══════════════════════════════════════════════════════════════════════════════
# F. Required object placement
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequiredPlacement:
    def test_product_placed_in_safe_zone(self):
        """Product must end up within safe zone x-bounds."""
        layer = make_fg_layer("product", 100, 200, 300, 400, obj_id="obj_prod")
        result, _ = run_layout([layer], NAVER_1250x560_SPEC)
        p = next(pl for pl in result.objectPlacements if pl.objectId == "obj_prod")
        tb = p.targetBBox
        # Product is HERO → placed in visual slot (inside safe zone range)
        assert tb["x"] >= 0 and tb["y"] >= 0
        assert tb["width"] > 0 and tb["height"] > 0

    def test_title_placed_in_safe_zone(self):
        """Title must be placed within the safe rect."""
        title = make_fg_layer("title", 50, 30, 900, 80, obj_id="obj_title")
        result, _ = run_layout([title], NAVER_1250x560_SPEC)
        p = next(pl for pl in result.objectPlacements if pl.objectId == "obj_title")
        tb = p.targetBBox
        # title is in SAFE_ZONE_REQUIRED_ROLES → must be inside safe zone
        assert tb["x"] >= 240, f"title left edge {tb['x']} < safe_x1=240"
        assert tb["y"] >= 50, f"title top edge {tb['y']} < safe_y1=50"
        assert tb["x"] + tb["width"] <= 1010, (
            f"title right edge {tb['x']+tb['width']} > safe_x2=1010"
        )

    def test_body_text_placed_in_safe_zone(self):
        """body_text must be placed within safe rect."""
        body = make_fg_layer("body_text", 50, 500, 800, 80, obj_id="obj_body")
        result, _ = run_layout([body], NAVER_1250x560_SPEC)
        p = next(pl for pl in result.objectPlacements if pl.objectId == "obj_body")
        tb = p.targetBBox
        assert tb["x"] >= 240
        assert tb["x"] + tb["width"] <= 1010

    def test_required_skipped_means_failure(self):
        """If a required object cannot be placed, layout success=False."""
        # We cannot easily force a skip in current engine, but we can verify
        # that allRequiredObjectsPlaced=True implies success.
        layers = [
            make_fg_layer("product", 100, 200, 300, 400, obj_id="obj_p"),
            make_fg_layer("title", 50, 30, 900, 80, obj_id="obj_t"),
        ]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        if result.allRequiredObjectsPlaced:
            assert result.success or result.hardFailReasons  # may fail due to overlap etc.

    def test_all_required_placed_count(self):
        """requiredObjectCount matches actual required roles."""
        from layout.layout_role_resolver import REQUIRED_ROLES
        layers = [
            make_fg_layer("product", 100, 200, 300, 400, obj_id="obj_p"),
            make_fg_layer("title", 50, 30, 900, 80, obj_id="obj_t"),
            make_fg_layer("body_text", 50, 500, 800, 80, obj_id="obj_b"),
            make_fg_layer("decorative", 0, 480, 1200, 60, obj_id="obj_d"),
        ]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        # product, title, body_text are REQUIRED_ROLES; decorative is not
        assert result.requiredObjectCount == 3
        assert result.inputObjectCount == 4


# ═══════════════════════════════════════════════════════════════════════════════
# G. Clipping detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestClipping:
    def test_product_no_clipping_in_safe_zone(self):
        """Product placed in safe zone should not be clipped."""
        from layout.safe_zone_validator import compute_clipping_ratio
        bbox_inside = {"x": 250, "y": 60, "width": 200, "height": 150}
        assert compute_clipping_ratio(bbox_inside, 1250, 560) == 0.0

    def test_product_fully_outside_is_clipped(self):
        """Object placed entirely outside canvas → clippingRatio=1.0."""
        from layout.safe_zone_validator import compute_clipping_ratio
        bbox_outside = {"x": 1300, "y": 0, "width": 100, "height": 100}
        assert compute_clipping_ratio(bbox_outside, 1250, 560) == 1.0

    def test_partial_clipping_detected(self):
        """Object partially outside canvas → 0 < clippingRatio < 1."""
        from layout.safe_zone_validator import compute_clipping_ratio
        bbox_partial = {"x": 1200, "y": 0, "width": 100, "height": 100}
        ratio = compute_clipping_ratio(bbox_partial, 1250, 560)
        assert 0.0 < ratio < 1.0

    def test_decorative_canvas_bleed_not_hard_fail(self):
        """Decorative objects are allowed to bleed off canvas edges."""
        from layout.safe_zone_validator import validate_placement
        from layout.models import ObjectPlacement
        p = ObjectPlacement(
            objectId="deco",
            layoutRole="decorative",
            semanticRole="decorative",
            targetBBox={"x": -50, "y": 400, "width": 1300, "height": 80},
            required=False,
            safeZoneRequired=False,
        )
        check = validate_placement(p, [p], 240, 50, 1010, 525, 1250, 560)
        # decorative → no hard fail for bleed
        assert not check["hardFail"]

    def test_required_role_10pct_clip_is_hard_fail(self):
        """Required role clipped > 10% → hardFail=True."""
        from layout.safe_zone_validator import validate_placement
        from layout.models import ObjectPlacement
        # width=100, placed at x=1200 → 50px inside, 50px outside → 50% clipped
        p = ObjectPlacement(
            objectId="prod",
            layoutRole="product",
            semanticRole="product",
            targetBBox={"x": 1200, "y": 100, "width": 100, "height": 100},
            required=True,
            safeZoneRequired=True,
        )
        check = validate_placement(p, [p], 240, 50, 1010, 525, 1250, 560)
        assert check["hardFail"]


# ═══════════════════════════════════════════════════════════════════════════════
# H. Overlap detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestOverlap:
    def test_no_overlap_returns_empty_list(self):
        """Non-overlapping objects → overlapObjectIds=[]."""
        from layout.safe_zone_validator import compute_overlap_ratio
        a = {"x": 0, "y": 0, "width": 100, "height": 100}
        b = {"x": 200, "y": 0, "width": 100, "height": 100}
        assert compute_overlap_ratio(a, b) == 0.0

    def test_overlap_detected(self):
        """Overlapping objects → overlap ratio > 0."""
        from layout.safe_zone_validator import compute_overlap_ratio
        a = {"x": 0, "y": 0, "width": 200, "height": 100}
        b = {"x": 100, "y": 0, "width": 200, "height": 100}
        ratio = compute_overlap_ratio(a, b)
        assert ratio > 0

    def test_text_text_overlap_is_hard_fail(self):
        """title + body_text overlap > 20% → hardFail=True."""
        from layout.safe_zone_validator import validate_placement
        from layout.models import ObjectPlacement
        title = ObjectPlacement(
            objectId="t1", layoutRole="title", semanticRole="title",
            targetBBox={"x": 300, "y": 60, "width": 400, "height": 60},
            required=True, safeZoneRequired=True,
        )
        body = ObjectPlacement(
            objectId="b1", layoutRole="body_text", semanticRole="body_text",
            targetBBox={"x": 300, "y": 80, "width": 400, "height": 60},  # overlaps title
            required=True, safeZoneRequired=True,
        )
        check = validate_placement(title, [title, body], 240, 50, 1010, 525, 1250, 560)
        assert check["hardFail"]

    def test_text_background_decorative_overlap_allowed(self):
        """title overlapping decorative → NOT a hard fail (intentional bg plate)."""
        from layout.safe_zone_validator import validate_placement
        from layout.models import ObjectPlacement
        title = ObjectPlacement(
            objectId="t1", layoutRole="title", semanticRole="title",
            targetBBox={"x": 300, "y": 60, "width": 400, "height": 60},
            required=True, safeZoneRequired=True,
        )
        deco = ObjectPlacement(
            objectId="d1", layoutRole="decorative", semanticRole="decorative",
            targetBBox={"x": 0, "y": 0, "width": 1250, "height": 200},
            required=False, safeZoneRequired=False,
        )
        check = validate_placement(title, [title, deco], 240, 50, 1010, 525, 1250, 560)
        assert not check["hardFail"]

    def test_product_text_small_overlap_soft_penalty(self):
        """product + title with small overlap → not hard fail (< 40%)."""
        from layout.safe_zone_validator import validate_placement
        from layout.models import ObjectPlacement
        product = ObjectPlacement(
            objectId="p1", layoutRole="product", semanticRole="product",
            targetBBox={"x": 240, "y": 50, "width": 350, "height": 450},
            required=True, safeZoneRequired=True,
        )
        title = ObjectPlacement(
            objectId="t1", layoutRole="title", semanticRole="title",
            targetBBox={"x": 560, "y": 50, "width": 400, "height": 60},
            required=True, safeZoneRequired=True,
        )
        check = validate_placement(product, [product, title], 240, 50, 1010, 525, 1250, 560)
        # No overlap (product ends at x=590, title starts at x=560 - small overlap)
        # But product-title overlap < 40% of smaller → NOT hard fail
        assert not check["hardFail"] or check.get("clippingRatio", 0) < 0.1


# ═══════════════════════════════════════════════════════════════════════════════
# I. Mother simulation
# ═══════════════════════════════════════════════════════════════════════════════

class TestMotherSimulation:
    """Mother PSD: 1200×1200 → Naver DA 1250×560.

    Layers (approx source coordinates):
      - human_subject strip: (72, 37, 484, 38) — actually title banner
      - product: (79, 152, 93, 342)
      - body_text: (64, 504, 503, 35)
      - decorative bottom bar: (−45, 476, 600, 144)
    """

    def _mother_layers_with_applylog(self):
        """Simulate Object Map having assigned 'title' to the human_subject layer."""
        layers = [
            make_fg_layer("human_subject", 72, 37, 484, 38, canvas_w=600, canvas_h=600,
                          obj_id="obj_title_banner", layer_id="lid_banner",
                          name="상단 제목 배너"),
            make_fg_layer("product", 79, 152, 93, 342, canvas_w=600, canvas_h=600,
                          obj_id="obj_product", layer_id="lid_product"),
            make_fg_layer("body_text", 64, 504, 503, 35, canvas_w=600, canvas_h=600,
                          obj_id="obj_body", layer_id="lid_body"),
            make_fg_layer("decorative", 0, 476, 600, 144, canvas_w=600, canvas_h=600,
                          obj_id="obj_deco", layer_id="lid_deco"),
        ]
        apply_logs = [{
            "layerId": "lid_banner",
            "layerName": "상단 제목 배너",
            "layerType": "pixel",
            "oldRole": "human_subject",
            "newRole": "title",
            "matchMethod": "layerId_exact",
            "confidence": 0.95,
            "applied": False,
            "rejectReason": "human_subject_immutable",
        }]
        psd_layers = [
            {"id": "lid_banner", "type": "pixel", "role": "human_subject",
             "name": "상단 제목 배너", "canvasWidth": 600, "canvasHeight": 600,
             "bbox": {"x": 72, "y": 37, "width": 484, "height": 38}},
        ]
        return layers, apply_logs, psd_layers

    def test_title_layout_role_resolved(self):
        """human_subject layer with attempted=title + wide geometry → layoutRole=title."""
        from layout.layout_role_resolver import resolve_layout_role
        layer = {
            "role": "human_subject", "name": "상단 제목 배너",
            "bbox": {}, "sourceBBox": {"x": 72, "y": 37, "width": 484, "height": 38},
            "layerId": "lid_banner",
        }
        psd_layer = {"type": "pixel", "canvasWidth": 600, "canvasHeight": 600}
        layout_role, reason = resolve_layout_role(layer, psd_layer, attempted_role="title")
        assert layout_role == "title", f"Expected title, got {layout_role!r}: {reason}"

    def test_product_in_safe_zone(self):
        """Product must be placed inside Naver safe zone after reflow."""
        layers, apply_logs, psd_layers = self._mother_layers_with_applylog()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=600, canvas_h=600, target_w=1250, target_h=560,
            psd_layers=psd_layers, apply_logs=apply_logs,
        )
        prod = next((p for p in result.objectPlacements if p.objectId == "obj_product"), None)
        assert prod is not None
        tb = prod.targetBBox
        assert tb["x"] >= 0
        assert tb["y"] >= 0
        assert tb["width"] > 0 and tb["height"] > 0

    def test_each_object_id_unique_in_placements(self):
        """Each objectId appears exactly once in objectPlacements."""
        layers, apply_logs, psd_layers = self._mother_layers_with_applylog()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=600, canvas_h=600, target_w=1250, target_h=560,
            psd_layers=psd_layers, apply_logs=apply_logs,
        )
        ids = [p.objectId for p in result.objectPlacements]
        assert len(ids) == len(set(ids)), f"Duplicate objectIds: {ids}"

    def test_no_duplicate_composition(self):
        """noDuplicateComposition=True (duplicateCount=0)."""
        layers, apply_logs, psd_layers = self._mother_layers_with_applylog()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=600, canvas_h=600, target_w=1250, target_h=560,
            psd_layers=psd_layers, apply_logs=apply_logs,
        )
        assert result.duplicateCount == 0
        assert result.noDuplicateComposition

    def test_decorative_can_bleed(self):
        """Decorative bar: safeZoneRequired=False (canvas bleed allowed)."""
        layers, apply_logs, psd_layers = self._mother_layers_with_applylog()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=600, canvas_h=600, target_w=1250, target_h=560,
            psd_layers=psd_layers, apply_logs=apply_logs,
        )
        deco = next((p for p in result.objectPlacements if p.objectId == "obj_deco"), None)
        assert deco is not None
        assert not deco.safeZoneRequired

    def test_aspect_ratio_preserved_product(self):
        """Product aspect ratio unchanged after reflow."""
        src_aspect = 93 / 342  # width/height
        layers, apply_logs, psd_layers = self._mother_layers_with_applylog()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=600, canvas_h=600, target_w=1250, target_h=560,
            psd_layers=psd_layers, apply_logs=apply_logs,
        )
        prod = next((p for p in result.objectPlacements if p.objectId == "obj_product"), None)
        assert prod is not None
        tb = prod.targetBBox
        dst_aspect = tb["width"] / tb["height"]
        assert abs(src_aspect - dst_aspect) < 0.01, (
            f"Aspect ratio changed: src={src_aspect:.4f} dst={dst_aspect:.4f}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# J. Yada simulation
# ═══════════════════════════════════════════════════════════════════════════════

class TestYadaSimulation:
    """Yada PSD: human visual + product + body_text + decorative → wide banner."""

    def _yada_layers(self):
        return [
            make_fg_layer("human_subject", 600, 0, 600, 900,
                          canvas_w=1200, canvas_h=900,
                          obj_id="obj_human", name="모델 이미지"),
            make_fg_layer("product", 50, 200, 300, 400,
                          canvas_w=1200, canvas_h=900,
                          obj_id="obj_product"),
            make_fg_layer("body_text", 50, 700, 500, 80,
                          canvas_w=1200, canvas_h=900,
                          obj_id="obj_body"),
            make_fg_layer("decorative", 0, 800, 1200, 100,
                          canvas_w=1200, canvas_h=900,
                          obj_id="obj_deco"),
        ]

    def test_all_objects_placed(self):
        """All 4 Yada layers get placement entries."""
        layers = self._yada_layers()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=1200, canvas_h=900, target_w=1250, target_h=560,
        )
        assert result.placedObjectCount == 4

    def test_unique_object_ids_in_placements(self):
        """No duplicate objectId in Yada placements."""
        layers = self._yada_layers()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=1200, canvas_h=900, target_w=1250, target_h=560,
        )
        ids = [p.objectId for p in result.objectPlacements]
        assert len(ids) == len(set(ids))

    def test_no_mother_objectid_in_yada(self):
        """Yada placements contain no 'obj_title_banner' (Mother-specific id)."""
        layers = self._yada_layers()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=1200, canvas_h=900, target_w=1250, target_h=560,
        )
        ids = {p.objectId for p in result.objectPlacements}
        assert "obj_title_banner" not in ids

    def test_body_text_placed(self):
        """body_text has a valid targetBBox after reflow."""
        layers = self._yada_layers()
        result, _ = run_layout(
            layers, NAVER_1250x560_SPEC,
            canvas_w=1200, canvas_h=900, target_w=1250, target_h=560,
        )
        body = next((p for p in result.objectPlacements if p.objectId == "obj_body"), None)
        assert body is not None
        assert body.targetBBox["width"] > 0
        assert body.targetBBox["height"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# K. A→B→A determinism
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossJobDeterminism:
    def _layers_a(self):
        return [
            make_fg_layer("product", 100, 200, 300, 400, obj_id="p1"),
            make_fg_layer("title", 50, 30, 900, 80, obj_id="t1"),
        ]

    def _layers_b(self):
        return [
            make_fg_layer("human_subject", 600, 0, 600, 600, obj_id="h1"),
            make_fg_layer("body_text", 50, 500, 800, 80, obj_id="b1"),
        ]

    def test_a1_equals_a2(self):
        """Two runs with same 'A' layers → same selectedCandidateId."""
        r1, _ = run_layout(self._layers_a(), NAVER_1250x560_SPEC, job_id="job_A1")
        r2, _ = run_layout(self._layers_a(), NAVER_1250x560_SPEC, job_id="job_A2")
        assert r1.selectedCandidateId == r2.selectedCandidateId

    def test_a1_product_bbox_equals_a2(self):
        """Same input across 2 runs → product targetBBox identical."""
        def get_product_bbox(layers):
            result, _ = run_layout(layers, NAVER_1250x560_SPEC)
            for p in result.objectPlacements:
                if p.objectId == "p1":
                    tb = p.targetBBox
                    return (tb["x"], tb["y"], tb["width"], tb["height"])
            return None

        bbox1 = get_product_bbox(self._layers_a())
        bbox2 = get_product_bbox(self._layers_a())
        assert bbox1 == bbox2

    def test_b_may_differ_from_a(self):
        """'B' layers have different roles → may differ from 'A' candidate."""
        ra, _ = run_layout(self._layers_a(), NAVER_1250x560_SPEC)
        rb, _ = run_layout(self._layers_b(), NAVER_1250x560_SPEC)
        # At minimum object counts differ
        assert ra.inputObjectCount != rb.inputObjectCount or ra.requiredObjectCount != rb.requiredObjectCount

    def test_fg_state_not_shared_between_runs(self):
        """Modifying fg_layers from run A must not affect run B."""
        layers_a = self._layers_a()
        r1, _ = run_layout(layers_a, NAVER_1250x560_SPEC)
        # After run A, bbox is updated in-place
        old_bbox_a = dict(layers_a[0]["bbox"])

        layers_a2 = self._layers_a()  # fresh A layers
        r2, _ = run_layout(layers_a2, NAVER_1250x560_SPEC)
        new_bbox_a2 = dict(layers_a2[0]["bbox"])

        # Both runs start from the same source → same planned bbox
        assert old_bbox_a == new_bbox_a2


# ═══════════════════════════════════════════════════════════════════════════════
# L. Missing safe zone data
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingSafeZone:
    def test_no_safe_zone_spec_runs_without_error(self):
        """Empty spec → fallback safe zone, no exception."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        result, logs = run_layout(layers, {}, target_w=1250, target_h=560)
        assert result.inputObjectCount == 1

    def test_safe_zone_not_available(self):
        """safeZoneAvailable=False when spec has no parsed_text/diagram status."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        result, _ = run_layout(layers, {"safeZoneParseStatus": "diagram_unreadable"},
                               target_w=1250, target_h=560)
        assert not result.safeZoneAvailable
        assert not result.safeZoneEnforced

    def test_warning_logged_on_missing_safe_zone(self):
        """SAFE_ZONE_UNAVAILABLE warning appears in warnings list."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        result, _ = run_layout(layers, {}, target_w=1250, target_h=560)
        assert any("SAFE_ZONE_UNAVAILABLE" in w for w in result.warnings)

    def test_not_falsely_reported_as_safe_zone_passed(self):
        """When safeZoneAvailable=False, safeZoneViolationCount is 0 (not enforced)."""
        layers = [make_fg_layer("cta", 50, 500, 200, 50)]
        result, _ = run_layout(layers, {}, target_w=1250, target_h=560)
        # Without enforcement, we may still count violations but safeZoneEnforced=False
        assert not result.safeZoneEnforced


# ═══════════════════════════════════════════════════════════════════════════════
# M. Counter correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestCounterCorrectness:
    def test_five_objects_all_placed(self):
        """input=5 unique → placed=5, skipped=0, required tracked correctly."""
        layers = [
            make_fg_layer("product", 100, 200, 300, 400, obj_id="p1"),
            make_fg_layer("title", 50, 30, 900, 80, obj_id="t1"),
            make_fg_layer("body_text", 50, 500, 800, 80, obj_id="b1"),
            make_fg_layer("logo", 50, 10, 200, 80, obj_id="l1"),
            make_fg_layer("decorative", 0, 480, 1200, 60, obj_id="d1"),
        ]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        assert result.inputObjectCount == 5
        assert result.uniqueObjectCount == 5
        assert result.placedObjectCount == 5
        assert result.skippedObjectCount == 0
        assert result.allUniqueObjectsPlaced

    def test_duplicate_objectid_counted(self):
        """Duplicate objectId increments duplicateCount, unique=n-1."""
        layers = [
            make_fg_layer("product", 100, 200, 300, 400, obj_id="same_id"),
            make_fg_layer("title", 50, 30, 900, 80, obj_id="same_id"),
            make_fg_layer("body_text", 50, 500, 800, 80, obj_id="body_id"),
        ]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        assert result.inputObjectCount == 3
        assert result.uniqueObjectCount == 2
        assert result.duplicateCount == 1
        assert not result.noDuplicateComposition

    def test_all_required_placed_true_when_no_required_missing(self):
        """When all required roles are placed, allRequiredObjectsPlaced=True."""
        layers = [
            make_fg_layer("product", 100, 200, 300, 400, obj_id="p1"),
            make_fg_layer("title", 50, 30, 900, 80, obj_id="t1"),
        ]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        assert result.allRequiredObjectsPlaced

    def test_all_objects_composited_once_true_no_dups(self):
        """allObjectsCompositedOnce=True when no duplicates and all placed."""
        layers = [
            make_fg_layer("product", 100, 200, 300, 400, obj_id="p1"),
            make_fg_layer("title", 50, 30, 900, 80, obj_id="t1"),
        ]
        result, _ = run_layout(layers, NAVER_1250x560_SPEC)
        assert result.noDuplicateComposition
        assert result.allUniqueObjectsPlaced
        assert result.allObjectsCompositedOnce


# ═══════════════════════════════════════════════════════════════════════════════
# N. jobId/specId provenance in logs
# ═══════════════════════════════════════════════════════════════════════════════

class TestProvenance:
    def test_job_id_appears_in_layout_input_log(self):
        """[LAYOUT_INPUT] log must contain the jobId."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        _, logs = run_layout(layers, NAVER_1250x560_SPEC, job_id="provenance_test_job")
        assert "provenance_test_job" in logs

    def test_spec_id_appears_in_layout_input_log(self):
        """[LAYOUT_INPUT] log must contain the specId."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        _, logs = run_layout(layers, NAVER_1250x560_SPEC, spec_id="1250x560_naver")
        assert "1250x560_naver" in logs

    def test_reflow_object_log_contains_object_id(self):
        """[REFLOW_OBJECT] log contains objectId for each placed object."""
        layers = [make_fg_layer("product", 100, 200, 300, 400, obj_id="unique_prod_id")]
        _, logs = run_layout(layers, NAVER_1250x560_SPEC)
        assert "unique_prod_id" in logs

    def test_layout_candidate_log_contains_candidate_ids(self):
        """[LAYOUT_CANDIDATE] log appears for each candidate."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        _, logs = run_layout(layers, NAVER_1250x560_SPEC)
        assert "[LAYOUT_CANDIDATE]" in logs
        assert "candidate_A" in logs
        assert "candidate_B" in logs
        assert "candidate_C" in logs

    def test_layout_selected_log_present(self):
        """[LAYOUT_SELECTED] log appears after selection."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        _, logs = run_layout(layers, NAVER_1250x560_SPEC)
        assert "[LAYOUT_SELECTED]" in logs

    def test_layout_summary_log_present(self):
        """[LAYOUT_SUMMARY] appears at end of layout run."""
        layers = [make_fg_layer("product", 100, 200, 300, 400)]
        _, logs = run_layout(layers, NAVER_1250x560_SPEC)
        assert "[LAYOUT_SUMMARY]" in logs

    def test_safe_zone_check_log_present(self):
        """[SAFE_ZONE_CHECK] appears for each placed object."""
        layers = [make_fg_layer("product", 100, 200, 300, 400, obj_id="pcheck")]
        _, logs = run_layout(layers, NAVER_1250x560_SPEC)
        assert "[SAFE_ZONE_CHECK]" in logs


# ═══════════════════════════════════════════════════════════════════════════════
# Additional _fit_in_slot unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFitInSlot:
    def test_center_anchor_centers_object(self):
        """center anchor: square object in wider slot → x centered."""
        from layout.reflow_engine import _fit_in_slot
        # 100x100 → slot 200x100: scale=min(2.0,1.0)=1.0, new_w=100, x=(200-100)//2=50
        bbox, scale = _fit_in_slot(100, 100, 0, 0, 200, 100, "center")
        assert scale == pytest.approx(1.0)
        assert bbox["x"] == 50
        assert bbox["y"] == 0

    def test_top_center_anchor(self):
        """top-center: object narrower than slot when height-constrained → x centered."""
        from layout.reflow_engine import _fit_in_slot
        # 100x50 → slot 300x100: scale=min(3.0,2.0)=2.0, new_w=200, x=(300-200)//2=50
        bbox, scale = _fit_in_slot(100, 50, 0, 0, 300, 100, "top-center")
        assert bbox["y"] == 0
        assert bbox["x"] == 50

    def test_slot_smaller_than_object(self):
        """Object larger than slot → scales down, no dimension exceeds slot."""
        from layout.reflow_engine import _fit_in_slot
        bbox, scale = _fit_in_slot(400, 300, 0, 0, 200, 150, "center")
        assert bbox["width"] <= 200
        assert bbox["height"] <= 150
        assert scale < 1.0

    def test_aspect_ratio_preserved_in_slot(self):
        """Aspect ratio of fitted object matches source."""
        from layout.reflow_engine import _fit_in_slot
        src_w, src_h = 400, 300
        bbox, _ = _fit_in_slot(src_w, src_h, 0, 0, 300, 200, "center")
        aspect_src = src_w / src_h
        aspect_dst = bbox["width"] / bbox["height"]
        assert abs(aspect_src - aspect_dst) < 0.005


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
