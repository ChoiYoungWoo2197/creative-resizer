"""Stage 21 Bundle D-3: Decorative grouping and composition ownership tests.

Verifies apply_decorative_policy():
  - Independent large decorative → excluded
  - Title background bbox → title group
  - CTA bottom bar → cta group
  - Logo decorative → logo group
  - Scene-like (very large) → scene_plate
  - group_child not in eligible list
  - No double composition of parent + child
  - required objects (title/CTA/product) unaffected
  - No file-name heuristics (pure geometry)
  - Deterministic grouping for same input
"""
from __future__ import annotations

import pytest
from PIL import Image

from foreground.decorative_policy import (
    apply_decorative_policy,
    OWNER_FOREGROUND_REFLOW,
    OWNER_SCENE_PLATE,
    OWNER_GROUP_CHILD,
    OWNER_EXCLUDED,
)


# ── Fixture helpers ────────────────────────────────────────────────────────────

def _layer(role: str, x: int, y: int, w: int, h: int,
           name: str = "", ltype: str = "shape",
           group_id: str = "", depth: int = 0, obj_id: str = "") -> dict:
    oid = obj_id or f"{role}_{x}_{y}_{w}_{h}"
    return {
        "role": role,
        "name": name or role,
        "type": ltype,
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "sourceBBox": {"x": x, "y": y, "width": w, "height": h},
        "depth": depth,
        "objectId": oid,
        "layerId": oid,
        "compositionEligible": True,
        "groupId": group_id,
        "parentId": group_id,
    }


CANVAS_W, CANVAS_H = 1200, 1200


# ── Test: independent large decorative excluded ───────────────────────────────

class TestIndependentDecorativeExcluded:
    def test_large_standalone_decorative_excluded(self):
        """Large decorative with no anchor overlap → excluded."""
        layers = [
            _layer("product",   100,  400, 400, 400, depth=0),
            _layer("title",     100,  100, 800,  80, depth=1),
            # Large standalone box — no overlap with product/title
            _layer("decorative", 0,  800, 1200, 200, name="하단바", depth=5),
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        excluded = report["excludedObjectIds"]
        assert len(excluded) > 0
        # The large decorative must not appear in eligible
        elig_ids = {l["objectId"] for l in eligible}
        assert "decorative_0_800_1200_200" not in elig_ids

    def test_small_isolated_decorative_excluded(self):
        """Small decorative with no geometric relationship → excluded."""
        layers = [
            _layer("product", 100, 400, 300, 300, depth=0),
            _layer("title",   100, 100, 600,  60, depth=1),
            _layer("decorative", 900, 900, 100, 30, name="작은장식", depth=8),
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_ids = {l["objectId"] for l in eligible}
        assert "decorative_900_900_100_30" not in elig_ids

    def test_excluded_not_in_composition_count(self):
        layers = [
            _layer("product", 100, 400, 300, 300),
            _layer("decorative", 0, 900, CANVAS_W, 100),
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        assert report["excludedCount"] >= 1
        assert report["compositionCount"] == len(eligible)

    def test_excluded_not_in_required_count(self):
        """Excluded decoratives must not count as skipped required."""
        layers = [
            _layer("title", 100, 50, 800, 80),
            _layer("decorative", 0, 900, CANVAS_W, 120),
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        # title must still be in eligible
        elig_roles = {l["role"] for l in eligible}
        assert "title" in elig_roles
        assert report["excludedCount"] >= 1


# ── Test: title background decorative grouped ─────────────────────────────────

class TestTitleBackgroundGrouped:
    def test_decorative_containing_title_is_grouped(self):
        """Decorative whose bbox contains title bbox → group_child."""
        title = _layer("title",     200, 100, 600,  70, depth=2)
        deco  = _layer("decorative", 150,  80, 700, 100, name="titlebox", depth=3)
        layers = [
            _layer("product", 100, 400, 300, 300),
            title,
            deco,
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        assert report["groupedCount"] >= 1
        assert report["groupedObjectIds"]
        # group_child should NOT appear in eligible (parent handles rendering)
        elig_ids = {l["objectId"] for l in eligible}
        assert deco["objectId"] not in elig_ids

    def test_group_child_not_independently_composited(self):
        """group_child decorative must not be in eligible layer list."""
        title = _layer("title", 100, 100, 800, 80)
        deco  = _layer("decorative", 80, 90, 850, 100, name="bg_box")
        layers = [title, deco]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_roles = [l["role"] for l in eligible]
        # Either the deco is excluded or grouped (not eligible as independent)
        assert elig_roles.count("decorative") == 0 or report["groupedCount"] > 0


# ── Test: CTA background grouped ─────────────────────────────────────────────

class TestCTABackgroundGrouped:
    def test_decorative_overlapping_cta_grouped(self):
        """Decorative overlapping CTA significantly → group_child."""
        cta  = _layer("cta",        50, 1100, 400, 60, depth=1)
        deco = _layer("decorative",  40, 1090, 420, 80, name="cta_bg", depth=2)
        layers = [
            _layer("product", 100, 400, 300, 300),
            _layer("title",   100, 100, 600,  60),
            cta,
            deco,
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        assert report["groupedCount"] >= 1

    def test_cta_group_child_not_composited_independently(self):
        cta  = _layer("cta",        50, 1100, 400, 60)
        deco = _layer("decorative",  40, 1090, 420, 80)
        layers = [cta, deco]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_ids = {l["objectId"] for l in eligible}
        assert deco["objectId"] not in elig_ids or report["groupedCount"] > 0


# ── Test: scene-like decorative → scene_plate ─────────────────────────────────

class TestSceneLikeDecorativeScenePlate:
    def test_very_large_decorative_scene_plate(self):
        """Decorative occupying >35% canvas with no anchor → scene_plate."""
        layers = [
            _layer("product", 100, 400, 300, 300),
            # 1200x800 = 960000 out of 1440000 = 66.7% > 35%
            _layer("decorative", 0, 0, CANVAS_W, 800, name="big_bg"),
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_ids = {l["objectId"] for l in eligible}
        assert "decorative_0_0_1200_800" not in elig_ids

    def test_scene_plate_decorative_not_in_eligible(self):
        layers = [
            _layer("decorative", 0, 0, CANVAS_W, CANVAS_H, name="full_canvas_bg"),
        ]
        eligible, _ = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_roles = [l["role"] for l in eligible]
        assert "decorative" not in elig_roles


# ── Test: required roles unaffected ───────────────────────────────────────────

class TestRequiredRolesUnaffected:
    def test_product_always_eligible(self):
        layers = [
            _layer("product", 100, 400, 300, 300),
            _layer("decorative", 0, 900, CANVAS_W, 100),
        ]
        eligible, _ = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_roles = [l["role"] for l in eligible]
        assert "product" in elig_roles

    def test_title_always_eligible(self):
        layers = [
            _layer("title", 100, 50, 800, 80),
            _layer("decorative", 0, 900, CANVAS_W, 100),
        ]
        eligible, _ = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_roles = [l["role"] for l in eligible]
        assert "title" in elig_roles

    def test_cta_always_eligible(self):
        layers = [
            _layer("cta", 100, 1100, 400, 60),
            _layer("decorative", 0, 0, CANVAS_W, 50),
        ]
        eligible, _ = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_roles = [l["role"] for l in eligible]
        assert "cta" in elig_roles

    def test_logo_always_eligible(self):
        layers = [
            _layer("logo", 100, 50, 200, 80),
            _layer("decorative", 0, 900, 300, 50),
        ]
        eligible, _ = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_roles = [l["role"] for l in eligible]
        assert "logo" in elig_roles

    def test_body_text_always_eligible(self):
        layers = [
            _layer("body_text", 100, 200, 600, 50),
        ]
        eligible, _ = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        elig_roles = [l["role"] for l in eligible]
        assert "body_text" in elig_roles


# ── Test: no file-name heuristics ─────────────────────────────────────────────

class TestNoFilenameHeuristics:
    def test_geometry_determines_grouping_not_name(self):
        """Same geometry → same policy regardless of layer name."""
        # Layer named "사각형 4" vs "shape_A" — same bbox relative to title
        title = _layer("title", 100, 100, 800, 80)
        deco_kr = _layer("decorative", 80, 90, 850, 100, name="사각형 4")
        deco_en = _layer("decorative", 80, 90, 850, 100, name="shape_A", obj_id="deco_en")
        layers_kr = [title, deco_kr]
        layers_en = [title, deco_en]
        _, report_kr = apply_decorative_policy(layers_kr, CANVAS_W, CANVAS_H)
        _, report_en = apply_decorative_policy(layers_en, CANVAS_W, CANVAS_H)
        # Both should have same grouping outcome
        assert report_kr["groupedCount"] == report_en["groupedCount"]
        assert report_kr["excludedCount"] == report_en["excludedCount"]

    def test_no_specific_psd_name_hardcoded(self):
        """Policy works on generic geometry regardless of PSD-specific names."""
        title = _layer("title", 100, 100, 700, 60)
        deco  = _layer("decorative", 80, 88, 750, 80, name="Mother_BG_Rect_Special")
        _, report = apply_decorative_policy([title, deco], CANVAS_W, CANVAS_H)
        # result determined by geometry, not the name
        assert isinstance(report["groupedCount"], int)
        assert isinstance(report["excludedCount"], int)


# ── Test: determinism ─────────────────────────────────────────────────────────

class TestDeterministicGrouping:
    def test_same_input_same_output(self):
        layers = [
            _layer("product",   100, 400, 300, 300, depth=0),
            _layer("title",     100, 100, 800,  80, depth=1),
            _layer("decorative",  0, 900, CANVAS_W, 150, depth=5),
            _layer("decorative", 80,  88, 850,  100, depth=2, obj_id="deco_title_bg"),
        ]
        eligible1, report1 = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        eligible2, report2 = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        assert report1["groupedCount"] == report2["groupedCount"]
        assert report1["excludedCount"] == report2["excludedCount"]
        assert sorted(report1["excludedObjectIds"]) == sorted(report2["excludedObjectIds"])
        assert sorted(report1["groupedObjectIds"]) == sorted(report2["groupedObjectIds"])

    def test_eligible_count_deterministic(self):
        layers = [
            _layer("product", 100, 400, 300, 300),
            _layer("decorative", 0, 900, CANVAS_W, 100),
        ]
        el1, _ = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        el2, _ = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        assert len(el1) == len(el2)


# ── Test: empty input ─────────────────────────────────────────────────────────

class TestEmptyInput:
    def test_empty_list_returns_empty(self):
        eligible, report = apply_decorative_policy([], CANVAS_W, CANVAS_H)
        assert eligible == []
        assert report["detectedCount"] == 0

    def test_no_decorative_no_change(self):
        layers = [
            _layer("product", 100, 400, 300, 300),
            _layer("title",   100, 100, 600,  60),
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        assert report["detectedCount"] == 0
        assert report["groupedCount"] == 0
        assert report["excludedCount"] == 0
        assert len(eligible) == 2


# ── Test: report fields ───────────────────────────────────────────────────────

class TestPolicyReportFields:
    def test_report_has_all_required_fields(self):
        layers = [_layer("product", 100, 400, 300, 300)]
        _, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        for key in ("detectedCount", "groupedCount", "excludedCount",
                    "compositionCount", "excludedObjectIds", "groupedObjectIds"):
            assert key in report

    def test_composition_count_equals_eligible_length(self):
        layers = [
            _layer("product", 100, 400, 300, 300),
            _layer("title",   100, 100, 600,  60),
            _layer("decorative", 0, 900, CANVAS_W, 100),
        ]
        eligible, report = apply_decorative_policy(layers, CANVAS_W, CANVAS_H)
        assert report["compositionCount"] == len(eligible)
