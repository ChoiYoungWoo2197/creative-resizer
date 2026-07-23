"""Stage 21 Bundle D-3: Role contradiction (human_subject ↔ text role) tests.

Verifies object_map_applicator.py D-3 contradiction correction logic:
  - raster text + exact layerId match + high conf → title allowed
  - Real hand/photo layer → immutable reject (unchanged)
  - Real person layer → immutable reject
  - Text geometry (wide/thin) → correction candidate
  - Low confidence → immutable reject
  - Name-only fallback → immutable reject
  - layerId exact + text evidence → ROLE_CONTRADICTION_RESOLVED
"""
from __future__ import annotations

import pytest
from object_map_applicator import apply_object_map


# ── Helpers ───────────────────────────────────────────────────────────────────

def _layer(lid: str, role: str, ltype: str = "pixel",
           name: str = "", w: int = 400, h: int = 60,
           text_content: str = "") -> dict:
    return {
        "id": lid,
        "role": role,
        "type": ltype,
        "name": name or f"layer_{lid}",
        "bbox": {"x": 0, "y": 0, "width": w, "height": h},
        "textContent": text_content,
        "priority": "optional",
    }


def _obj_result(matched_id: str, new_role: str, confidence: float = 0.9,
                match_method: str = "layerId_exact",
                match_status: str = "ready") -> dict:
    return {
        "matchedLayerId": matched_id,
        "role": new_role,
        "confidence": confidence,
        "matchStatus": match_status,
        "matchMethod": match_method,
    }


# ── Category 1: Text correction allowed ───────────────────────────────────────

class TestTextRoleCorrection:
    def test_raster_text_exact_match_high_conf_to_title(self):
        """Text layer mis-labeled human_subject → corrected to title."""
        layer = _layer("L001", "human_subject", ltype="typelayer",
                       name="headline_text", w=800, h=60)
        obj = _obj_result("L001", "title", confidence=0.9)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "title"
        assert logs[0]["applied"] is True

    def test_thin_wide_geometry_enables_correction(self):
        """Thin wide bbox (text geometry) enables human→title correction."""
        # w/h = 600/30 = 20 → text geometry
        layer = _layer("L002", "human_subject", ltype="pixel",
                       name="promo_text", w=600, h=30)
        obj = _obj_result("L002", "title", confidence=0.85)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "title"

    def test_text_content_enables_correction(self):
        """Layer with textContent evidence → correction allowed."""
        layer = _layer("L003", "human_subject", ltype="pixel",
                       name="some_layer", w=300, h=40,
                       text_content="특별 할인!")
        obj = _obj_result("L003", "body_text", confidence=0.9)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "body_text"

    def test_correction_to_cta_role(self):
        layer = _layer("L010", "human_subject", ltype="typelayer",
                       name="cta_button_text", w=300, h=50)
        obj = _obj_result("L010", "cta", confidence=0.92)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "cta"

    def test_correction_to_brand_text(self):
        layer = _layer("L011", "human_subject", ltype="typelayer",
                       name="brand_name_text", w=400, h=45)
        obj = _obj_result("L011", "brand_text", confidence=0.88)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        # brand_text is in _TEXT_CORRECTION_ROLES
        assert updated[0]["role"] == "brand_text"


# ── Category 2: Real photo layers stay immutable ──────────────────────────────

class TestRealHumanImmutable:
    def test_hand_photo_smartobject_stays_human_subject(self):
        """Real hand photo (smartobject) cannot become title via Object Map."""
        layer = _layer("L004", "human_subject", ltype="smartobject",
                       name="hand_holding_product", w=400, h=600)
        obj = _obj_result("L004", "title", confidence=0.9)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "human_subject"
        log = next(l for l in logs if l["layerId"] == "L004")
        assert log["applied"] is False
        assert "immutable" in (log["rejectReason"] or "")

    def test_person_pixel_layer_stays_human_subject(self):
        """Person pixel layer cannot become body_text."""
        layer = _layer("L005", "human_subject", ltype="pixel",
                       name="model_photo", w=600, h=800)
        # No text evidence, not text geometry (h > w)
        obj = _obj_result("L005", "body_text", confidence=0.95)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "human_subject"

    def test_large_photo_not_correctable_by_name(self):
        """Large square photo — even with title role in map — stays immutable."""
        layer = _layer("L006", "human_subject", ltype="pixel",
                       name="photo_large_square", w=500, h=500)
        obj = _obj_result("L006", "title", confidence=0.99)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        # w/h = 1.0, not text geometry; no text content
        assert updated[0]["role"] == "human_subject"


# ── Category 3: Low confidence → immutable ────────────────────────────────────

class TestLowConfidenceImmutable:
    def test_low_confidence_text_layer_stays_immutable(self):
        """Confidence < 0.8 → human_subject immutable even with text evidence."""
        layer = _layer("L007", "human_subject", ltype="typelayer",
                       name="promo_text", w=600, h=50)
        obj = _obj_result("L007", "title", confidence=0.75)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        # confidence below threshold → not corrected
        assert updated[0]["role"] == "human_subject"

    def test_exact_threshold_accepted(self):
        """confidence == 0.8 → allowed (inclusive threshold)."""
        layer = _layer("L008", "human_subject", ltype="typelayer",
                       name="text_layer", w=800, h=60)
        obj = _obj_result("L008", "title", confidence=0.8)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "title"


# ── Category 4: Fallback match → immutable ────────────────────────────────────

class TestFallbackMatchImmutable:
    def test_name_fallback_cannot_override_human_subject(self):
        """Name-based match is not allowed for human→text correction."""
        layer = _layer("L009", "human_subject", ltype="typelayer",
                       name="text_promo_layer", w=800, h=60)
        obj = {
            "matchedLayerId": "",
            "role": "title",
            "confidence": 0.95,
            "matchStatus": "ready",
            "matchMethod": "layerName_type",
        }
        updated, logs = apply_object_map([layer], [obj], strict=False)
        # non-strict mode with name fallback should still reject human→text
        # because method != layerId_exact
        assert updated[0]["role"] == "human_subject"


# ── Category 5: Non-human roles unchanged ─────────────────────────────────────

class TestNonHumanRolesUnchanged:
    def test_product_to_logo_normal_override(self):
        """Non-human role override works normally."""
        layer = _layer("L100", "product", ltype="smartobject",
                       name="product_image", w=300, h=300)
        obj = _obj_result("L100", "logo", confidence=0.9)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "logo"

    def test_background_unaffected(self):
        layer = _layer("L101", "background", ltype="pixel",
                       name="bg_gradient", w=1200, h=1200)
        obj = _obj_result("L101", "background", confidence=0.9)
        updated, logs = apply_object_map([layer], [obj], strict=True)
        assert updated[0]["role"] == "background"


# ── Category 6: stale version reason code ─────────────────────────────────────

class TestStaleVersionReasonCode:
    def test_d3_reason_codes_exist(self):
        from verdict import reason_codes as RC
        assert hasattr(RC, "EXTRACTION_OBJECT_ROLE_CONTRADICTION")
        assert hasattr(RC, "TECH_INVALID_BACKGROUND_GENERATION_MODE")
        assert hasattr(RC, "TECH_SEMANTIC_SCENE_CLEANUP_FAILED")
        assert hasattr(RC, "TECH_FORBIDDEN_LEGACY_FALLBACK")
        assert hasattr(RC, "EXTRACTION_STALE_OBJECT_MAP_VERSION")
        assert hasattr(RC, "EXTRACTION_DECORATIVE_GROUP_AMBIGUOUS")
        assert hasattr(RC, "COMPOSITION_UNGROUPED_DECORATIVE_EXCLUDED")
        assert hasattr(RC, "COMPOSITION_DUPLICATE_GROUP_CHILD")
        assert hasattr(RC, "LAYOUT_ORIGINAL_PRESERVATION_EXCESSIVE")
        assert hasattr(RC, "LAYOUT_TARGET_ADAPTATION_INSUFFICIENT")
        assert hasattr(RC, "LAYOUT_DECORATIVE_DOMINANCE")

    def test_d3_codes_in_all_codes(self):
        from verdict.reason_codes import ALL_CODES
        from verdict import reason_codes as RC
        d3_codes = [
            RC.TECH_INVALID_BACKGROUND_GENERATION_MODE,
            RC.TECH_SEMANTIC_SCENE_CLEANUP_FAILED,
            RC.TECH_FORBIDDEN_LEGACY_FALLBACK,
            RC.EXTRACTION_STALE_OBJECT_MAP_VERSION,
            RC.COMPOSITION_UNGROUPED_DECORATIVE_EXCLUDED,
            RC.LAYOUT_ORIGINAL_PRESERVATION_EXCESSIVE,
        ]
        for code in d3_codes:
            assert code in ALL_CODES, f"Missing from ALL_CODES: {code}"

    def test_all_codes_sorted_unique(self):
        from verdict.reason_codes import ALL_CODES
        assert ALL_CODES == sorted(set(ALL_CODES))
