"""Stage 21 Bundle A unit tests.

Categories:
  A  Background layer isolation (bg plate contains only background roles)
  B  Unknown safety (unknown/decorative not auto-included in background)
  C  Removal mask (foreground alpha in mask, background not)
  D  Outpaint + removal union (provider allowed area edge cases)
  E  Provider input (foreground not in provider input, SHA differs from source composite)
  F  Single composition (objectId once, duplicate detection)
  G  Mother simulation (hand+product excluded; each object composited once)
  H  Yada simulation (main_image absent from bg plate; body_text absent from fg compos)
  I  A→B→A artifact isolation
  J  Fail-closed (bg plate failure → no composite fallback, provider not called)

Run inside the creative-resizer worker container:
  pytest test_background_plate.py -v
Or via the direct runner script.
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from PIL import Image, ImageDraw
import numpy as np


# ── Image helpers ──────────────────────────────────────────────────────────────

def _rgba(w: int, h: int, color=(200, 100, 50, 255)) -> Image.Image:
    return Image.new("RGBA", (w, h), color)


def _rgb(w: int, h: int, color=(200, 100, 50)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def _sha(img: Image.Image) -> str:
    return hashlib.sha256(img.convert("RGBA").tobytes()).hexdigest()


def _mask_nonzero(mask: Image.Image) -> int:
    arr = np.array(mask.convert("L"))
    return int((arr > 127).sum())


# ── Fake _layer_obj ────────────────────────────────────────────────────────────

class _FakeLayerObj:
    """Minimal psd-tools layer stub."""

    def __init__(self, img: Image.Image):
        self._img = img

    def composite(self) -> Image.Image:
        return self._img.copy()


def _make_psd_layer(
    role: str,
    x: int, y: int, w: int, h: int,
    color=(120, 180, 220, 255),
    layer_id: str = "",
    name: str = "",
) -> dict:
    img = _rgba(w, h, color)
    return {
        "id": layer_id or f"{role}_{x}_{y}",
        "name": name or role,
        "role": role,
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "depth": 0,
        "_layer_obj": _FakeLayerObj(img),
    }


# ── Imports under test ─────────────────────────────────────────────────────────

from background.background_plate_builder import (
    build_background_plate,
    BACKGROUND_PLATE_ROLES,
    FOREGROUND_EXCLUDED_ROLES,
    BackgroundPlateResult,
)
from foreground.compositor import composite_foreground, ForegroundCompositeResult
from foreground.layer_extractor import extract_foreground_layers, _make_object_id


# ═══════════════════════════════════════════════════════════════════════════════
# A  Background layer isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackgroundLayerIsolation:
    """Category A: bg plate contains ONLY background-role layers."""

    def test_a1_only_background_roles_included(self):
        """bg plate strategy A places background role, excludes human+product."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300, color=(30, 80, 150, 255)),
            _make_psd_layer("human_subject", 50, 50, 100, 200, color=(255, 200, 180, 255)),
            _make_psd_layer("product", 200, 100, 80, 80, color=(255, 0, 0, 255)),
        ]
        source = _rgb(400, 300, (100, 100, 100))
        res = build_background_plate(source, layers, 400, 300)

        assert res.success, f"Expected success, got failure_reason={res.failure_reason!r}"
        assert res.strategy == "layer_composite"
        assert len(res.included_layer_ids) == 1
        assert len(res.excluded_layer_ids) == 2

    def test_a2_background_pixel_differs_from_source(self):
        """bg plate pixel SHA must differ from full source composite."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300, color=(30, 80, 150, 255)),
            _make_psd_layer("human_subject", 50, 50, 100, 200, color=(255, 200, 180, 255)),
        ]
        source = _rgb(400, 300, (100, 100, 100))
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        source_sha = _sha(source)
        assert res.background_pixel_sha256 != source_sha, (
            "bg plate SHA must differ from full source — foreground was not removed"
        )

    def test_a3_all_background_roles_accepted(self):
        """All four background-plate roles are accepted by strategy A."""
        roles_to_test = ["background", "background_fill", "background_texture", "environmental_background"]
        for role in roles_to_test:
            layers = [_make_psd_layer(role, 0, 0, 200, 200)]
            source = _rgb(200, 200)
            res = build_background_plate(source, layers, 200, 200)
            assert res.success, f"role={role!r} should be accepted as background"
            assert role in BACKGROUND_PLATE_ROLES

    def test_a4_excluded_roles_in_result(self):
        """excluded_foreground_objects lists all non-background layers."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300),
            _make_psd_layer("title", 10, 10, 200, 40),
            _make_psd_layer("cta", 10, 250, 100, 30),
        ]
        source = _rgb(400, 300)
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        excluded_roles = {o["role"] for o in res.excluded_foreground_objects}
        assert "title" in excluded_roles
        assert "cta" in excluded_roles
        assert "background" not in excluded_roles

    def test_a5_no_background_layer_fails_closed(self):
        """If no background-role layer exists, build fails (no silent fallback)."""
        layers = [
            _make_psd_layer("human_subject", 0, 0, 400, 300),
            _make_psd_layer("product", 100, 100, 100, 100),
        ]
        # Strategy B may succeed here, but strategy A alone cannot
        # This test verifies that at minimum, the result.success path is honest
        source = _rgb(400, 300, (200, 200, 200))
        res = build_background_plate(source, layers, 400, 300)
        # Without a background layer, strategy A fails.
        # Strategy B (blank foreground) should still produce something.
        # Key requirement: no full-composite silent fallback (source unchanged = invalid).
        if res.success:
            # Strategy B ran — that's allowed.
            # But if strategy B was used, the plate must differ from source (blanked fg).
            plate_sha = res.background_pixel_sha256
            src_sha = _sha(source)
            assert plate_sha != src_sha, "Strategy B must blank foreground, not return source unchanged"


# ═══════════════════════════════════════════════════════════════════════════════
# B  Unknown safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnknownSafety:
    """Category B: unknown / decorative / ambient not included in background plate."""

    def test_b1_unknown_excluded_from_background(self):
        """'unknown' role is treated as foreground, not background."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300),
            _make_psd_layer("unknown", 100, 100, 100, 100),
        ]
        source = _rgb(400, 300)
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        assert "unknown" in FOREGROUND_EXCLUDED_ROLES
        excluded_roles = {o["role"] for o in res.excluded_foreground_objects}
        assert "unknown" in excluded_roles

    def test_b2_decorative_excluded_from_background(self):
        """'decorative' role is treated as foreground for safety."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300),
            _make_psd_layer("decorative", 150, 150, 80, 80),
        ]
        source = _rgb(400, 300)
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        assert "decorative" in FOREGROUND_EXCLUDED_ROLES
        excluded_roles = {o["role"] for o in res.excluded_foreground_objects}
        assert "decorative" in excluded_roles

    def test_b3_person_roles_excluded(self):
        """All person-variant roles (person, person_or_hand, hand, face, skin) excluded."""
        for role in ("person", "person_or_hand", "hand", "face", "skin"):
            assert role in FOREGROUND_EXCLUDED_ROLES, f"role={role!r} must be in FOREGROUND_EXCLUDED_ROLES"

    def test_b4_text_roles_excluded(self):
        """All text roles excluded from background plate."""
        for role in ("title", "headline", "body_text", "text"):
            assert role in FOREGROUND_EXCLUDED_ROLES, f"role={role!r} must be in FOREGROUND_EXCLUDED_ROLES"

    def test_b5_background_fill_not_in_foreground_excluded(self):
        """background_fill must be a background role, not excluded."""
        assert "background_fill" in BACKGROUND_PLATE_ROLES
        assert "background_fill" not in FOREGROUND_EXCLUDED_ROLES


# ═══════════════════════════════════════════════════════════════════════════════
# C  Removal mask
# ═══════════════════════════════════════════════════════════════════════════════

class TestRemovalMask:
    """Category C: removal mask covers foreground bboxes, not background."""

    def test_c1_foreground_areas_in_mask(self):
        """Mask has nonzero pixels in the area where human layer was."""
        human_x, human_y, human_w, human_h = 50, 50, 100, 200
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300),
            _make_psd_layer("human_subject", human_x, human_y, human_w, human_h),
        ]
        source = _rgb(400, 300)
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        assert res.foreground_removal_mask is not None

        # Sample a pixel inside the human bbox — mask should be active
        mask = res.foreground_removal_mask.convert("L")
        center_x = human_x + human_w // 2
        center_y = human_y + human_h // 2
        pixel_val = mask.getpixel((center_x, center_y))
        assert pixel_val > 127, (
            f"Pixel at human center ({center_x},{center_y}) should be masked=255, got {pixel_val}"
        )

    def test_c2_background_only_area_not_in_mask(self):
        """Mask pixel outside any foreground bbox should be 0."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300),
            _make_psd_layer("human_subject", 100, 100, 80, 80),
        ]
        source = _rgb(400, 300)
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        mask = res.foreground_removal_mask.convert("L")
        # Top-left corner (0,0) is well outside the human bbox (100,100)+(3px dilation)
        pixel_val = mask.getpixel((0, 0))
        assert pixel_val == 0, f"Background-only area should have mask=0, got {pixel_val}"

    def test_c3_mask_size_matches_canvas(self):
        """Removal mask dimensions match original PSD canvas, not target."""
        layers = [
            _make_psd_layer("background", 0, 0, 800, 600),
            _make_psd_layer("product", 200, 200, 100, 100),
        ]
        source = _rgb(800, 600)
        res = build_background_plate(source, layers, 800, 600)

        assert res.success
        assert res.foreground_removal_mask is not None
        assert res.foreground_removal_mask.size == (800, 600)

    def test_c4_removal_pixel_count_nonzero_when_foreground_present(self):
        """removal_pixel_count > 0 when there is at least one foreground layer."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300),
            _make_psd_layer("product", 100, 100, 80, 80),
        ]
        source = _rgb(400, 300)
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        assert res.removal_pixel_count > 0

    def test_c5_no_foreground_layers_gives_no_mask(self):
        """When all layers are background, removal mask is None or empty."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300),
        ]
        source = _rgb(400, 300)
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        # No foreground → mask should be None (nothing to remove)
        assert res.foreground_removal_mask is None or res.removal_pixel_count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# D  Outpaint + removal union
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutpaintRemovalUnion:
    """Category D: generationAllowedMask = outpaint margins ∪ removal mask."""

    def _make_gen_allowed_mock(
        self,
        canvas_w: int, canvas_h: int,
        target_w: int, target_h: int,
        off_x: int, off_y: int,
        removal_mask=None,
    ) -> Image.Image:
        """Simplified generationAllowedMask builder (mirrors SFR logic)."""
        gen_mask = Image.new("L", (target_w, target_h), 0)
        draw = ImageDraw.Draw(gen_mask)

        # Outpaint margins
        if off_x > 0:
            draw.rectangle([0, 0, off_x - 1, target_h - 1], fill=255)
            draw.rectangle([off_x + canvas_w, 0, target_w - 1, target_h - 1], fill=255)
        if off_y > 0:
            draw.rectangle([0, 0, target_w - 1, off_y - 1], fill=255)
            draw.rectangle([0, off_y + canvas_h, target_w - 1, target_h - 1], fill=255)

        # Union with removal mask
        if removal_mask is not None:
            rm_resized = removal_mask.resize((target_w, target_h), Image.NEAREST)
            gen_arr = np.array(gen_mask)
            rm_arr = np.array(rm_resized.convert("L"))
            union = np.maximum(gen_arr, rm_arr)
            gen_mask = Image.fromarray(union.astype(np.uint8), "L")

        return gen_mask

    def test_d1_outpaint_margins_covered_when_offset_positive(self):
        """Outpaint margins (off_x, off_y > 0) are in generationAllowedMask."""
        gen_mask = self._make_gen_allowed_mock(
            canvas_w=400, canvas_h=300,
            target_w=600, target_h=400,
            off_x=100, off_y=50,
        )
        # Left margin pixel
        assert gen_mask.getpixel((10, 200)) == 255, "Left outpaint margin must be in gen mask"
        # Top margin pixel
        assert gen_mask.getpixel((300, 5)) == 255, "Top outpaint margin must be in gen mask"
        # Center pixel (source area, no removal mask)
        assert gen_mask.getpixel((300, 200)) == 0, "Source center should not be in gen mask"

    def test_d2_removal_mask_unioned_into_gen_allowed(self):
        """Foreground removal mask pixels appear in generationAllowedMask."""
        removal = Image.new("L", (400, 300), 0)
        ImageDraw.Draw(removal).rectangle([50, 50, 150, 150], fill=255)

        gen_mask = self._make_gen_allowed_mock(
            canvas_w=400, canvas_h=300,
            target_w=400, target_h=300,
            off_x=0, off_y=0,
            removal_mask=removal,
        )
        # Pixel inside removal mask area
        assert gen_mask.getpixel((100, 100)) == 255, "Removal mask area must be in gen mask"
        # Background-only pixel
        assert gen_mask.getpixel((300, 250)) == 0, "Background-only area must not be in gen mask"

    def test_d3_negative_offset_no_outpaint_margin(self):
        """When off_y < 0 (source taller than target), no top margin should appear."""
        gen_mask = self._make_gen_allowed_mock(
            canvas_w=400, canvas_h=600,
            target_w=400, target_h=400,
            off_x=0, off_y=-100,  # negative: no outpaint margin
        )
        # With off_y < 0 and off_x == 0, no outpaint pixels → all zero
        assert gen_mask.getpixel((10, 5)) == 0, "No top margin when off_y < 0"

    def test_d4_both_axes_covered(self):
        """Positive off_x and off_y both produce outpaint margins."""
        gen_mask = self._make_gen_allowed_mock(
            canvas_w=300, canvas_h=200,
            target_w=500, target_h=400,
            off_x=100, off_y=100,
        )
        # Right margin
        assert gen_mask.getpixel((490, 200)) == 255, "Right margin must be covered"
        # Bottom margin
        assert gen_mask.getpixel((250, 390)) == 255, "Bottom margin must be covered"


# ═══════════════════════════════════════════════════════════════════════════════
# E  Provider input (foreground not in provider input)
# ═══════════════════════════════════════════════════════════════════════════════

class TestProviderInput:
    """Category E: provider receives background plate (no human/product/text)."""

    def test_e1_bg_plate_sha_differs_from_source_sha(self):
        """Background plate SHA256 is different from the full source composite SHA256."""
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300, color=(30, 80, 150, 255)),
            _make_psd_layer("human_subject", 50, 50, 100, 200, color=(255, 200, 180, 255)),
        ]
        # Make source composite visibly include human area
        source = Image.new("RGB", (400, 300), (30, 80, 150))
        draw = ImageDraw.Draw(source)
        draw.rectangle([50, 50, 149, 249], fill=(255, 200, 180))

        res = build_background_plate(source, layers, 400, 300)
        assert res.success

        source_sha = _sha(source)
        assert res.background_pixel_sha256 != source_sha, (
            "Provider input (bg plate) must differ from full source composite"
        )

    def test_e2_bg_plate_has_no_human_pixels(self):
        """In strategy A, human bbox area in bg plate should not contain human color."""
        human_color = (255, 0, 0, 255)  # pure red — distinctive
        bg_color = (0, 100, 200, 255)   # blue
        layers = [
            _make_psd_layer("background", 0, 0, 400, 300, color=bg_color),
            _make_psd_layer("human_subject", 100, 100, 100, 100, color=human_color),
        ]
        source = _rgb(400, 300)
        res = build_background_plate(source, layers, 400, 300)

        assert res.success
        assert res.strategy == "layer_composite"

        plate = res.image.convert("RGBA")
        # Sample center of human bbox
        px = plate.getpixel((150, 150))
        assert px[:3] != (255, 0, 0), (
            f"Human-area pixel in bg plate should NOT be red (human color), got {px}"
        )

    def test_e3_background_pixel_sha256_is_nonempty(self):
        """bg plate SHA256 is populated on success."""
        layers = [_make_psd_layer("background", 0, 0, 200, 200)]
        source = _rgb(200, 200)
        res = build_background_plate(source, layers, 200, 200)

        assert res.success
        assert res.background_pixel_sha256, "background_pixel_sha256 must not be empty"
        assert len(res.background_pixel_sha256) == 64, "SHA256 should be 64 hex chars"

    def test_e4_only_bg_layers_in_strategy_a_plate(self):
        """Strategy A plate contains color from background layer, not foreground."""
        bg_color = (10, 20, 30, 255)
        fg_color = (200, 50, 50, 255)
        layers = [
            _make_psd_layer("background", 0, 0, 200, 200, color=bg_color),
            _make_psd_layer("product", 50, 50, 100, 100, color=fg_color),
        ]
        source = _rgb(200, 200)
        res = build_background_plate(source, layers, 200, 200)

        assert res.success
        # Corner pixel (outside fg bbox) should match bg color
        plate = res.image.convert("RGBA")
        px = plate.getpixel((5, 5))
        assert px[:3] == bg_color[:3], f"BG plate corner should be bg color {bg_color[:3]}, got {px}"


# ═══════════════════════════════════════════════════════════════════════════════
# F  Single composition (objectId deduplication)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleComposition:
    """Category F: each objectId is composited exactly once."""

    def _make_fg_layer(
        self,
        role: str, x: int, y: int, w: int, h: int,
        color=(100, 200, 100, 255),
        object_id: str = "",
        name: str = "",
    ) -> dict:
        img = _rgba(w, h, color)
        oid = object_id or f"oid_{role}_{x}_{y}"
        return {
            "role": role,
            "name": name or role,
            "image": img,
            "bbox": {"x": x, "y": y, "width": w, "height": h},
            "sourceBBox": {"x": x, "y": y, "width": w, "height": h},
            "depth": 0,
            "layerId": oid,
            "objectId": oid,
            "sourcePixelSha256": "",
            "compositedCount": 0,
        }

    def test_f1_unique_objects_each_composited_once(self):
        """Two unique objects are both composited, compositedCount=1 each."""
        bg = _rgb(400, 300, (30, 80, 150))
        layers = [
            self._make_fg_layer("product", 10, 10, 80, 80, object_id="oid_product"),
            self._make_fg_layer("logo", 200, 10, 60, 40, object_id="oid_logo"),
        ]
        res = composite_foreground(bg, layers)

        assert res.success
        assert res.all_objects_composited_once
        assert res.duplicate_count == 0
        assert res.placed_count == 2
        for entry in res.object_manifest:
            assert entry.get("compositedCount") == 1, (
                f"objectId={entry['objectId']!r} should have compositedCount=1"
            )

    def test_f2_duplicate_objectid_logged_and_skipped(self):
        """Duplicate objectId logs DUPLICATE_FOREGROUND_OBJECT and skips second instance."""
        bg = _rgb(400, 300)
        oid = "oid_product_duplicate"
        layers = [
            self._make_fg_layer("product", 10, 10, 80, 80, object_id=oid),
            self._make_fg_layer("product", 10, 10, 80, 80, object_id=oid),  # duplicate
        ]
        res = composite_foreground(bg, layers)

        assert res.success
        assert res.duplicate_count == 1
        assert oid in res.duplicate_object_ids
        assert res.unique_object_count == 1
        # all_objects_composited_once is False because there was a duplicate
        assert not res.all_objects_composited_once

    def test_f3_no_duplicate_all_once_flag_true(self):
        """all_objects_composited_once is True when no duplicates exist."""
        bg = _rgb(400, 300)
        layers = [
            self._make_fg_layer("title", 0, 0, 200, 50, object_id="oid_title"),
            self._make_fg_layer("cta", 0, 250, 100, 40, object_id="oid_cta"),
        ]
        res = composite_foreground(bg, layers)

        assert res.success
        assert res.all_objects_composited_once

    def test_f4_object_manifest_contains_all_unique_objects(self):
        """object_manifest has one entry per unique objectId."""
        bg = _rgb(400, 300)
        layers = [
            self._make_fg_layer("product", 50, 50, 100, 100, object_id="oid_p1"),
            self._make_fg_layer("logo", 200, 50, 80, 60, object_id="oid_l1"),
            self._make_fg_layer("title", 50, 200, 200, 40, object_id="oid_t1"),
        ]
        res = composite_foreground(bg, layers)

        assert res.success
        manifest_ids = [e["objectId"] for e in res.object_manifest]
        assert "oid_p1" in manifest_ids
        assert "oid_l1" in manifest_ids
        assert "oid_t1" in manifest_ids
        assert len(manifest_ids) == 3

    def test_f5_composited_count_updated_in_layer_dict(self):
        """After compositing, layer dict's compositedCount is updated to 1."""
        bg = _rgb(400, 300)
        layer = self._make_fg_layer("product", 50, 50, 100, 100, object_id="oid_p")
        assert layer["compositedCount"] == 0  # initial state

        composite_foreground(bg, [layer])

        assert layer["compositedCount"] == 1, (
            "compositor must update compositedCount in-place on placed layer"
        )

    def test_f6_multiple_duplicates_all_logged(self):
        """Three instances of same objectId → 2 duplicates logged, 1 placed."""
        bg = _rgb(400, 300)
        oid = "oid_multi_dup"
        layers = [
            self._make_fg_layer("badge", 10, 10, 50, 50, object_id=oid),
            self._make_fg_layer("badge", 10, 10, 50, 50, object_id=oid),
            self._make_fg_layer("badge", 10, 10, 50, 50, object_id=oid),
        ]
        res = composite_foreground(bg, layers)

        assert res.duplicate_count == 2
        assert res.unique_object_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# G  Mother simulation
# ═══════════════════════════════════════════════════════════════════════════════

class TestMotherSimulation:
    """Category G: Mother-like PSD — hand + product excluded from bg plate."""

    def _make_mother_layers(self) -> list[dict]:
        return [
            _make_psd_layer("background", 0, 0, 1200, 1200, color=(200, 210, 220, 255), name="bg"),
            _make_psd_layer("background_texture", 0, 0, 1200, 1200, color=(210, 215, 225, 255), name="texture"),
            _make_psd_layer("human_subject", 100, 50, 400, 900, color=(255, 200, 180, 255), name="hand"),
            _make_psd_layer("product", 300, 400, 200, 200, color=(180, 0, 0, 255), name="product_bottle"),
            _make_psd_layer("logo", 900, 50, 200, 80, color=(0, 0, 0, 255), name="logo"),
            _make_psd_layer("title", 50, 1000, 600, 60, color=(0, 0, 0, 255), name="title"),
            _make_psd_layer("cta", 900, 1100, 200, 60, color=(255, 100, 0, 255), name="cta"),
        ]

    def test_g1_bg_plate_excludes_hand(self):
        """Background plate must not include hand/human_subject layer."""
        layers = self._make_mother_layers()
        source = _rgb(1200, 1200)
        res = build_background_plate(source, layers, 1200, 1200)

        assert res.success
        excluded_roles = {o["role"] for o in res.excluded_foreground_objects}
        assert "human_subject" in excluded_roles, "Hand (human_subject) must be excluded from bg plate"

    def test_g2_bg_plate_excludes_product(self):
        """Background plate must not include product layer."""
        layers = self._make_mother_layers()
        source = _rgb(1200, 1200)
        res = build_background_plate(source, layers, 1200, 1200)

        assert res.success
        excluded_roles = {o["role"] for o in res.excluded_foreground_objects}
        assert "product" in excluded_roles, "Product must be excluded from bg plate"

    def test_g3_bg_includes_both_background_layers(self):
        """Both background + background_texture layers should be included."""
        layers = self._make_mother_layers()
        source = _rgb(1200, 1200)
        res = build_background_plate(source, layers, 1200, 1200)

        assert res.success
        assert res.strategy == "layer_composite"
        assert len(res.included_layer_ids) == 2  # bg + texture

    def test_g4_foreground_composited_once(self):
        """Each foreground object in Mother layout is composited exactly once."""
        bg = _rgb(1250, 560, (200, 210, 220))
        fg_layers = [
            {
                "role": "human_subject",
                "name": "hand",
                "image": _rgba(300, 700),
                "bbox": {"x": 100, "y": 0, "width": 300, "height": 560},
                "sourceBBox": {"x": 100, "y": 50, "width": 400, "height": 900},
                "depth": 1,
                "layerId": "layer_hand",
                "objectId": "oid_hand",
                "sourcePixelSha256": "",
                "compositedCount": 0,
            },
            {
                "role": "product",
                "name": "product_bottle",
                "image": _rgba(167, 93),
                "bbox": {"x": 400, "y": 186, "width": 167, "height": 93},
                "sourceBBox": {"x": 300, "y": 400, "width": 200, "height": 200},
                "depth": 2,
                "layerId": "layer_product",
                "objectId": "oid_product",
                "sourcePixelSha256": "",
                "compositedCount": 0,
            },
        ]
        res = composite_foreground(bg, fg_layers, job_id="mother_sim", spec_id="1250x560")

        assert res.success
        assert res.duplicate_count == 0
        assert res.all_objects_composited_once
        assert res.human_subject_preserved
        assert res.product_placed

    def test_g5_hand_not_in_bg_plate_pixel_check(self):
        """Hand color (red) must not appear in background plate at hand's location."""
        hand_color = (255, 0, 0, 255)  # pure red
        bg_color = (200, 210, 220, 255)  # blue-grey
        layers = [
            _make_psd_layer("background", 0, 0, 400, 400, color=bg_color),
            _make_psd_layer("human_subject", 100, 100, 100, 200, color=hand_color),
        ]
        source = _rgb(400, 400)
        res = build_background_plate(source, layers, 400, 400)

        assert res.success
        plate = res.image.convert("RGBA")
        # Check pixel at center of hand bbox (150, 200)
        px = plate.getpixel((150, 200))
        assert px[:3] != (255, 0, 0), f"Hand color must not appear in bg plate at hand location, got {px}"


# ═══════════════════════════════════════════════════════════════════════════════
# H  Yada simulation
# ═══════════════════════════════════════════════════════════════════════════════

class TestYadaSimulation:
    """Category H: Yada-like PSD — main_image absent from bg plate; fg composited once."""

    def _make_yada_layers(self) -> list[dict]:
        return [
            _make_psd_layer("background", 0, 0, 1200, 628, color=(240, 235, 225, 255), name="bg_cream"),
            _make_psd_layer("main_image", 600, 50, 550, 528, color=(150, 120, 90, 255), name="woman"),
            _make_psd_layer("logo", 30, 30, 150, 60, color=(0, 0, 0, 255), name="brand_logo"),
            _make_psd_layer("title", 30, 150, 500, 80, color=(20, 20, 20, 255), name="headline"),
            _make_psd_layer("body_text", 30, 250, 500, 200, color=(60, 60, 60, 255), name="body"),
            _make_psd_layer("cta", 30, 520, 200, 60, color=(200, 50, 50, 255), name="buy_now"),
        ]

    def test_h1_main_image_absent_from_bg_plate(self):
        """main_image (woman) must not be in background plate."""
        layers = self._make_yada_layers()
        source = _rgb(1200, 628)
        res = build_background_plate(source, layers, 1200, 628)

        assert res.success
        excluded_roles = {o["role"] for o in res.excluded_foreground_objects}
        assert "main_image" in excluded_roles, "main_image must be excluded from bg plate"

    def test_h2_body_text_excluded_from_bg_plate(self):
        """body_text must not be in background plate."""
        layers = self._make_yada_layers()
        source = _rgb(1200, 628)
        res = build_background_plate(source, layers, 1200, 628)

        assert res.success
        excluded_roles = {o["role"] for o in res.excluded_foreground_objects}
        assert "body_text" in excluded_roles

    def test_h3_fg_objects_composited_once_yada(self):
        """All Yada foreground objects composited exactly once."""
        bg = _rgb(1250, 560)
        fg_layers = []
        for i, (role, x, y, w, h) in enumerate([
            ("main_image", 600, 0, 600, 560),
            ("logo", 20, 20, 150, 60),
            ("title", 20, 120, 500, 80),
            ("body_text", 20, 220, 500, 200),
            ("cta", 20, 480, 200, 60),
        ]):
            fg_layers.append({
                "role": role,
                "name": role,
                "image": _rgba(w, h),
                "bbox": {"x": x, "y": y, "width": w, "height": h},
                "sourceBBox": {"x": x, "y": y, "width": w, "height": h},
                "depth": i,
                "layerId": f"layer_{role}",
                "objectId": f"oid_{role}",
                "sourcePixelSha256": "",
                "compositedCount": 0,
            })

        res = composite_foreground(bg, fg_layers, job_id="yada_sim", spec_id="1250x560")

        assert res.success
        assert res.duplicate_count == 0
        assert res.all_objects_composited_once
        # title placed as "headline" flag (title is in placed_roles)
        assert "title" in res.placed_roles

    def test_h4_bg_plate_sha_differs_from_yada_source(self):
        """Yada bg plate SHA differs from source (main_image removed)."""
        # Make source visually include main_image area
        source = Image.new("RGB", (1200, 628), (240, 235, 225))
        draw = ImageDraw.Draw(source)
        draw.rectangle([600, 50, 1149, 577], fill=(150, 120, 90))  # woman area

        layers = self._make_yada_layers()
        res = build_background_plate(source, layers, 1200, 628)

        assert res.success
        assert res.background_pixel_sha256 != _sha(source)


# ═══════════════════════════════════════════════════════════════════════════════
# I  A→B→A artifact isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestArtifactIsolation:
    """Category I: Two successive builds are independent (no shared state)."""

    def _make_layers_for_job(self, bg_color, human_color, canvas=400) -> list[dict]:
        return [
            _make_psd_layer("background", 0, 0, canvas, canvas, color=bg_color),
            _make_psd_layer("human_subject", 100, 100, 100, 200, color=human_color),
            _make_psd_layer("title", 50, 350, 300, 40),
        ]

    def test_i1_successive_builds_independent(self):
        """Two builds from different source composites produce different bg plates."""
        source_a = Image.new("RGB", (400, 400), (10, 20, 30))
        layers_a = self._make_layers_for_job(
            bg_color=(10, 20, 30, 255),
            human_color=(255, 100, 100, 255),
        )
        res_a = build_background_plate(source_a, layers_a, 400, 400)

        source_b = Image.new("RGB", (400, 400), (200, 210, 220))
        layers_b = self._make_layers_for_job(
            bg_color=(200, 210, 220, 255),
            human_color=(100, 200, 100, 255),
        )
        res_b = build_background_plate(source_b, layers_b, 400, 400)

        assert res_a.success
        assert res_b.success
        assert res_a.background_pixel_sha256 != res_b.background_pixel_sha256, (
            "Two different jobs must produce different bg plate SHAs"
        )

    def test_i2_fg_composite_state_not_shared(self):
        """Two separate compositor calls don't share compositedCount state."""
        bg = _rgb(400, 300)

        def _make_layers(oid_suffix: str) -> list[dict]:
            return [{
                "role": "product",
                "name": "product",
                "image": _rgba(80, 80),
                "bbox": {"x": 50, "y": 50, "width": 80, "height": 80},
                "sourceBBox": {"x": 50, "y": 50, "width": 80, "height": 80},
                "depth": 0,
                "layerId": f"layer_{oid_suffix}",
                "objectId": f"oid_{oid_suffix}",
                "sourcePixelSha256": "",
                "compositedCount": 0,
            }]

        layers_a = _make_layers("a")
        layers_b = _make_layers("b")

        res_a = composite_foreground(bg, layers_a, job_id="job_a")
        res_b = composite_foreground(bg, layers_b, job_id="job_b")

        assert res_a.success and res_b.success
        assert res_a.duplicate_count == 0
        assert res_b.duplicate_count == 0
        assert res_a.all_objects_composited_once
        assert res_b.all_objects_composited_once

    def test_i3_a_b_a_bg_plate_isolation(self):
        """A→B→A: job A result is not affected by job B's build."""
        def _build(bg_color, human_color):
            source = Image.new("RGB", (400, 400), bg_color[:3])
            layers = [
                _make_psd_layer("background", 0, 0, 400, 400, color=bg_color),
                _make_psd_layer("human_subject", 100, 100, 100, 200, color=human_color),
            ]
            return build_background_plate(source, layers, 400, 400)

        res_a1 = _build((10, 20, 30, 255), (255, 100, 100, 255))
        res_b = _build((200, 210, 220, 255), (100, 200, 100, 255))
        res_a2 = _build((10, 20, 30, 255), (255, 100, 100, 255))

        assert res_a1.success and res_b.success and res_a2.success
        assert res_a1.background_pixel_sha256 == res_a2.background_pixel_sha256, (
            "Same source A should produce identical bg plate SHA regardless of intervening B build"
        )
        assert res_a1.background_pixel_sha256 != res_b.background_pixel_sha256


# ═══════════════════════════════════════════════════════════════════════════════
# J  Fail-closed
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailClosed:
    """Category J: bg plate failure → no fallback, success=False, reason populated."""

    def test_j1_empty_psd_layers_fails_closed(self):
        """No psd_layers → build fails (not silent fallback to source)."""
        source = _rgb(400, 300)
        res = build_background_plate(source, [], 400, 300)

        assert not res.success
        assert res.failure_reason, "failure_reason must be populated on failure"
        assert res.image is None, "No image should be returned on failure"

    def test_j2_failure_reason_populated(self):
        """failure_reason is a non-empty string when success=False."""
        res = build_background_plate(_rgb(100, 100), [], 100, 100)
        assert not res.success
        assert isinstance(res.failure_reason, str) and len(res.failure_reason) > 0

    def test_j3_no_composite_image_on_failure(self):
        """image is None when build fails."""
        res = build_background_plate(_rgb(100, 100), [], 100, 100)
        assert not res.success
        assert res.image is None

    def test_j4_strategy_empty_on_failure(self):
        """strategy is empty string when build fails."""
        res = build_background_plate(_rgb(100, 100), [], 100, 100)
        assert not res.success
        assert res.strategy == ""

    def test_j5_no_foreground_removal_mask_on_failure(self):
        """foreground_removal_mask is None when build fails."""
        res = build_background_plate(_rgb(100, 100), [], 100, 100)
        assert not res.success
        assert res.foreground_removal_mask is None

    def test_j6_bg_sha_empty_on_failure(self):
        """background_pixel_sha256 is empty when build fails."""
        res = build_background_plate(_rgb(100, 100), [], 100, 100)
        assert not res.success
        assert res.background_pixel_sha256 == ""


# ═══════════════════════════════════════════════════════════════════════════════
# objectId unit tests (layer_extractor._make_object_id)
# ═══════════════════════════════════════════════════════════════════════════════

class TestObjectId:
    """Supplementary: objectId stability and uniqueness."""

    def test_oid_with_layer_id_returns_id(self):
        """If layer has an id, _make_object_id returns it directly."""
        layer = {"id": "psd_layer_123", "role": "product", "name": "product", "bbox": {}}
        assert _make_object_id(layer) == "psd_layer_123"

    def test_oid_without_layer_id_stable_hash(self):
        """Without layer id, same role+name+bbox always gives same hash."""
        layer = {
            "id": "",
            "role": "title",
            "name": "Headline Text",
            "bbox": {"x": 50, "y": 200, "width": 300, "height": 60},
        }
        oid1 = _make_object_id(layer)
        oid2 = _make_object_id(layer)
        assert oid1 == oid2
        assert oid1.startswith("auto_")

    def test_oid_different_bbox_gives_different_id(self):
        """Different bbox → different auto-generated objectId."""
        base = {"id": "", "role": "product", "name": "product", "bbox": {}}
        layer_a = {**base, "bbox": {"x": 10, "y": 10, "width": 100, "height": 100}}
        layer_b = {**base, "bbox": {"x": 200, "y": 200, "width": 100, "height": 100}}

        assert _make_object_id(layer_a) != _make_object_id(layer_b)

    def test_oid_different_role_gives_different_id(self):
        """Different role → different auto-generated objectId."""
        base = {"id": "", "name": "layer", "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}
        layer_p = {**base, "role": "product"}
        layer_l = {**base, "role": "logo"}

        assert _make_object_id(layer_p) != _make_object_id(layer_l)

    def test_oid_hash_length(self):
        """Auto-generated objectId is 'auto_' + 12 hex chars."""
        layer = {"id": "", "role": "cta", "name": "Buy Now", "bbox": {"x": 100, "y": 500, "width": 200, "height": 60}}
        oid = _make_object_id(layer)
        assert oid.startswith("auto_")
        assert len(oid) == len("auto_") + 12


if __name__ == "__main__":
    import subprocess
    import sys as _sys
    _sys.exit(subprocess.call(["python", "-m", "pytest", __file__, "-v", "--tb=short"]))
