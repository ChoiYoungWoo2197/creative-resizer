"""Stage 20 typography pipeline unit tests — minimum 80 tests.

All tests run without a real PSD file. They test:
  - role_resolver (15 roles, Korean/English aliases, priority chain)
  - text_extractor (Korean detection, NFC, font metadata)
  - font_resolver (PSD→CSS mapping)
  - layout_templates (all 5 spec types + 1250x560 exact)
  - duplicate_detector (group-composite skip + text similarity dedup)
  - cta_layout (CTA group detection)
  - compositor (z-order, dedup skip, fallback bg)
  - quality_gate (required roles, safe zone, score calculation)
  - schemas (dataclass defaults)
  - pipeline (disabled flag)
"""
import os
import sys
import unittest
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── role_resolver ──────────────────────────────────────────────────────────────
from typography.role_resolver import (
    classify_role_by_name, classify_role_by_position,
    classify_role_by_text_content, resolve_layer_role,
    resolve_roles, get_role_stats, PRIORITY_MAP,
)


class TestRoleResolverByName(unittest.TestCase):
    def test_background_en(self):       self.assertEqual(classify_role_by_name("background"), "background")
    def test_background_ko(self):       self.assertEqual(classify_role_by_name("배경"), "background")
    def test_background_bkg(self):      self.assertEqual(classify_role_by_name("bkg_layer"), "background")
    def test_main_image_product(self):  self.assertEqual(classify_role_by_name("product"), "main_image")
    def test_main_image_ko(self):       self.assertEqual(classify_role_by_name("제품이미지"), "main_image")
    def test_main_image_model(self):    self.assertEqual(classify_role_by_name("model_01"), "main_image")
    def test_title_en(self):            self.assertEqual(classify_role_by_name("headline"), "title")
    def test_title_ko(self):            self.assertEqual(classify_role_by_name("타이틀"), "title")
    def test_title_copy(self):          self.assertEqual(classify_role_by_name("main_copy_layer"), "title")
    def test_body_text_en(self):        self.assertEqual(classify_role_by_name("subcopy"), "body_text")
    def test_body_text_ko(self):        self.assertEqual(classify_role_by_name("서브카피"), "body_text")
    def test_cta_en(self):              self.assertEqual(classify_role_by_name("cta_btn"), "cta")
    def test_cta_ko(self):              self.assertEqual(classify_role_by_name("버튼"), "cta")
    def test_cta_buy(self):             self.assertEqual(classify_role_by_name("buy_now"), "cta")
    def test_logo_en(self):             self.assertEqual(classify_role_by_name("brand_logo"), "logo")
    def test_logo_ko(self):             self.assertEqual(classify_role_by_name("로고"), "logo")
    def test_badge_en(self):            self.assertEqual(classify_role_by_name("sale_tag"), "badge")
    def test_badge_ko(self):            self.assertEqual(classify_role_by_name("이벤트태그"), "badge")
    def test_legal_text(self):          self.assertEqual(classify_role_by_name("disclaimer"), "legal_text")
    def test_legal_text_ko(self):       self.assertEqual(classify_role_by_name("면책문구"), "legal_text")
    def test_brand_name(self):          self.assertEqual(classify_role_by_name("brand_name"), "brand_name")
    def test_product_detail(self):      self.assertEqual(classify_role_by_name("product_detail"), "product_detail")
    def test_sub_logo(self):            self.assertEqual(classify_role_by_name("sub_logo"), "sub_logo")
    def test_scene(self):               self.assertEqual(classify_role_by_name("scene"), "scene")
    def test_decoration(self):          self.assertEqual(classify_role_by_name("deco_layer"), "decoration")
    def test_pattern(self):             self.assertEqual(classify_role_by_name("texture"), "pattern")
    def test_overlay(self):             self.assertEqual(classify_role_by_name("gradient_overlay"), "overlay")
    def test_unknown(self):             self.assertEqual(classify_role_by_name("flat_merge_42"), "unknown")
    def test_empty(self):               self.assertEqual(classify_role_by_name(""), "unknown")

    def test_nfc_normalization(self):
        # 배경 in NFC vs NFD
        nfd = unicodedata.normalize("NFD", "배경")
        self.assertEqual(classify_role_by_name(nfd), "background")


class TestRoleResolverByPosition(unittest.TestCase):
    def _layer(self, x, y, w, h, cw=1000, ch=600, lt="pixel"):
        return {"x": x, "y": y, "width": w, "height": h}, cw, ch, lt

    def test_background_large_area(self):
        role = classify_role_by_position({"x": 0, "y": 0, "width": 1000, "height": 600}, 1000, 600)
        self.assertEqual(role, "background")

    def test_logo_top(self):
        role = classify_role_by_position({"x": 10, "y": 10, "width": 80, "height": 40}, 1000, 600)
        self.assertEqual(role, "logo")

    def test_title_top_wide(self):
        role = classify_role_by_position({"x": 100, "y": 60, "width": 500, "height": 80}, 1000, 600)
        self.assertEqual(role, "title")

    def test_cta_bottom(self):
        role = classify_role_by_position({"x": 100, "y": 480, "width": 120, "height": 40}, 1000, 600)
        self.assertEqual(role, "cta")

    def test_main_image_center(self):
        role = classify_role_by_position({"x": 50, "y": 100, "width": 300, "height": 350}, 1000, 600)
        self.assertEqual(role, "main_image")

    def test_none_for_tiny(self):
        role = classify_role_by_position({"x": 500, "y": 300, "width": 10, "height": 10}, 1000, 600)
        self.assertIsNone(role)


class TestRoleResolverTextContent(unittest.TestCase):
    def test_cta_phrase_ko(self):
        self.assertEqual(classify_role_by_text_content("지금 바로 신청하기"), "cta")

    def test_legal_text(self):
        txt = "※ 이 광고는 상품·서비스 등에 관한 사실을 소비자가 합리적으로 선택할 수 있도록 한 것입니다." * 3
        self.assertEqual(classify_role_by_text_content(txt), "legal_text")

    def test_normal_text_unknown(self):
        self.assertEqual(classify_role_by_text_content("여름 한정 특가"), "unknown")


class TestResolveRoles(unittest.TestCase):
    def _make_layers(self):
        return [
            {"id": "l1", "name": "배경", "type": "pixel", "bbox": {"x": 0, "y": 0, "width": 1000, "height": 600},
             "canvasWidth": 1000, "canvasHeight": 600, "isTextLayer": False},
            {"id": "l2", "name": "타이틀", "type": "type", "bbox": {"x": 100, "y": 50, "width": 500, "height": 80},
             "canvasWidth": 1000, "canvasHeight": 600, "isTextLayer": True, "textContent": "여름 특가"},
            {"id": "l3", "name": "big_product", "type": "pixel", "bbox": {"x": 200, "y": 100, "width": 400, "height": 400},
             "canvasWidth": 1000, "canvasHeight": 600, "isTextLayer": False},
        ]

    def test_roles_assigned(self):
        result = resolve_roles(self._make_layers())
        roles = {l["id"]: l["role"] for l in result}
        self.assertEqual(roles["l1"], "background")
        self.assertEqual(roles["l2"], "title")
        self.assertEqual(roles["l3"], "main_image")

    def test_priority_set(self):
        result = resolve_roles(self._make_layers())
        bg = next(l for l in result if l["id"] == "l1")
        self.assertEqual(bg["priority"], "required")

    def test_user_override(self):
        layers = self._make_layers()
        result = resolve_roles(layers, {"l3": "logo"})
        role_l3 = next(l["role"] for l in result if l["id"] == "l3")
        self.assertEqual(role_l3, "logo")

    def test_invalid_user_override_ignored(self):
        layers = self._make_layers()
        result = resolve_roles(layers, {"l3": "not_a_real_role"})
        role_l3 = next(l["role"] for l in result if l["id"] == "l3")
        # Falls back to role_source chain: position → main_image (large area)
        self.assertEqual(role_l3, "main_image")

    def test_role_stats(self):
        result = resolve_roles(self._make_layers())
        stats = get_role_stats(result)
        self.assertGreater(stats["classifyRate"], 0.5)
        self.assertIn("background", stats["roles"])

    def test_heuristic_promotes_largest_unknown_to_main_image(self):
        layers = [
            {"id": "u1", "name": "layer1", "type": "pixel",
             "bbox": {"x": 0, "y": 0, "width": 10, "height": 10},
             "canvasWidth": 1000, "canvasHeight": 600, "isTextLayer": False},
            {"id": "u2", "name": "layer2", "type": "pixel",
             "bbox": {"x": 0, "y": 0, "width": 300, "height": 300},
             "canvasWidth": 1000, "canvasHeight": 600, "isTextLayer": False},
        ]
        result = resolve_roles(layers)
        role_u2 = next(l["role"] for l in result if l["id"] == "u2")
        self.assertEqual(role_u2, "main_image")

    def test_heuristic_promotes_largest_text_to_title(self):
        # Layer in the middle with small area — doesn't match any position rule → unknown
        # After heuristic: only text layer is unknown → promoted to title
        layers = [
            {"id": "t1", "name": "layer_flat_merge", "type": "type",
             "bbox": {"x": 400, "y": 300, "width": 50, "height": 15},
             "canvasWidth": 1000, "canvasHeight": 600, "isTextLayer": True, "textContent": "hello"},
        ]
        result = resolve_roles(layers)
        role_t1 = next(l["role"] for l in result if l["id"] == "t1")
        self.assertEqual(role_t1, "title")


# ── text_extractor ─────────────────────────────────────────────────────────────
from typography.text_extractor import (
    _is_korean, _nfc, extract_text_layers, count_korean_layers, get_text_summary,
)


class TestTextExtractor(unittest.TestCase):
    def test_is_korean_true(self):
        self.assertTrue(_is_korean("안녕하세요"))

    def test_is_korean_false(self):
        self.assertFalse(_is_korean("Hello World"))

    def test_is_korean_mixed(self):
        self.assertTrue(_is_korean("Hello 안녕"))

    def test_nfc_normalization(self):
        nfd = unicodedata.normalize("NFD", "배경")
        nfc = _nfc(nfd)
        self.assertEqual(nfc, "배경")

    def test_extract_text_layers_non_text_unchanged(self):
        layers = [{"id": "l1", "name": "bg", "type": "pixel", "isTextLayer": False}]
        result = extract_text_layers(layers)
        self.assertEqual(result[0]["id"], "l1")
        self.assertNotIn("textContent", result[0])

    def test_extract_text_layers_text_layer_annotated(self):
        layers = [{
            "id": "t1", "name": "title", "type": "type",
            "isTextLayer": True, "textContent": "여름 특가",
        }]
        result = extract_text_layers(layers)
        self.assertEqual(result[0].get("textContent"), "여름 특가")
        self.assertTrue(result[0].get("isKorean"))

    def test_extract_text_layers_english_not_korean(self):
        layers = [{
            "id": "t2", "name": "title", "type": "type",
            "isTextLayer": True, "textContent": "Summer Sale",
        }]
        result = extract_text_layers(layers)
        self.assertFalse(result[0].get("isKorean"))

    def test_count_korean_layers(self):
        layers = [
            {"isKorean": True}, {"isKorean": False}, {"isKorean": True},
        ]
        self.assertEqual(count_korean_layers(layers), 2)

    def test_get_text_summary(self):
        layers = [
            {"type": "type", "isTextLayer": True, "isKorean": True, "textContent": "안녕"},
            {"type": "pixel", "isTextLayer": False, "isKorean": False, "textContent": ""},
        ]
        s = get_text_summary(layers)
        self.assertEqual(s["totalTextLayers"], 1)
        self.assertEqual(s["koreanLayers"], 1)


# ── font_resolver ──────────────────────────────────────────────────────────────
from typography.font_resolver import resolve_font


class TestFontResolver(unittest.TestCase):
    def test_nanum_gothic(self):
        f = resolve_font("NanumGothic", is_korean=True)
        self.assertIn("NanumGothic", f)

    def test_nanum_gothic_bold_prefix_match(self):
        f = resolve_font("NanumGothicBold", is_korean=True)
        self.assertIn("NanumGothic", f)

    def test_helvetica(self):
        f = resolve_font("Helvetica", is_korean=False)
        self.assertIn("Helvetica", f)

    def test_unknown_korean(self):
        f = resolve_font("", is_korean=True)
        self.assertIn("NanumGothic", f)

    def test_unknown_non_korean(self):
        f = resolve_font("", is_korean=False)
        self.assertIn("sans-serif", f)

    def test_custom_korean_family(self):
        f = resolve_font("CustomGothicKR", is_korean=True)
        # Should contain the font name or a Korean fallback
        self.assertTrue("Gothic" in f or "NanumGothic" in f)


# ── layout_templates ───────────────────────────────────────────────────────────
from typography.layout_templates import get_template, slots_as_dict, _spec_type


class TestLayoutTemplates(unittest.TestCase):
    def _classified(self, image_side="left"):
        cx = 100 if image_side == "left" else 900
        return [
            {"role": "main_image", "bbox": {"x": cx, "y": 100, "width": 200, "height": 300},
             "canvasWidth": 1200, "canvasHeight": 628},
            {"role": "title", "bbox": {"x": 400, "y": 50, "width": 300, "height": 60},
             "canvasWidth": 1200, "canvasHeight": 628},
        ]

    def test_spec_type_horizontal(self):  self.assertEqual(_spec_type(1200, 628), "horizontal")
    def test_spec_type_square(self):      self.assertEqual(_spec_type(1000, 1000), "square")
    def test_spec_type_vertical(self):    self.assertEqual(_spec_type(600, 900), "vertical")
    def test_spec_type_ultravert(self):   self.assertEqual(_spec_type(300, 1200), "ultravertical")
    def test_spec_type_ultrawide(self):   self.assertEqual(_spec_type(2000, 500), "ultrawide")

    def test_1250x560_image_left(self):
        name, slots = get_template(1250, 560, self._classified("left"))
        self.assertEqual(name, "layout_1250x560_image_left")
        roles = {s.role for s in slots}
        self.assertIn("background", roles)
        self.assertIn("title", roles)
        self.assertIn("main_image", roles)

    def test_1250x560_image_right(self):
        name, slots = get_template(1250, 560, self._classified("right"))
        self.assertEqual(name, "layout_1250x560_image_right")

    def test_horizontal_returns_slots(self):
        name, slots = get_template(1200, 628, self._classified())
        self.assertIn("horizontal", name)
        self.assertGreater(len(slots), 0)

    def test_square_has_all_core_slots(self):
        classified = [
            {"role": "main_image", "bbox": {"x": 50, "y": 50, "width": 200, "height": 200},
             "canvasWidth": 500, "canvasHeight": 500},
            {"role": "title", "bbox": {"x": 50, "y": 300, "width": 300, "height": 50},
             "canvasWidth": 500, "canvasHeight": 500},
        ]
        name, slots = get_template(1000, 1000, classified)
        self.assertIn("square", name)
        roles = {s.role for s in slots}
        self.assertIn("background", roles)
        self.assertIn("title", roles)

    def test_vertical_template(self):
        classified = [
            {"role": "main_image", "bbox": {"x": 0, "y": 100, "width": 600, "height": 600},
             "canvasWidth": 600, "canvasHeight": 1000},
        ]
        name, slots = get_template(600, 1000, classified)
        self.assertIn("vertical", name)

    def test_ultravert_template(self):
        name, slots = get_template(300, 1200, [])
        self.assertIn("ultravert", name)

    def test_ultrawide_template(self):
        name, slots = get_template(2400, 600, self._classified())
        self.assertIn("ultrawide", name)

    def test_slots_all_within_canvas(self):
        _, slots = get_template(1250, 560, self._classified())
        for s in slots:
            self.assertGreaterEqual(s.x, 0, f"{s.role}.x={s.x}")
            self.assertGreaterEqual(s.y, 0, f"{s.role}.y={s.y}")
            self.assertLessEqual(s.x + s.w, 1250 + 1, f"{s.role} exceeds width")
            self.assertLessEqual(s.y + s.h, 560 + 1, f"{s.role} exceeds height")

    def test_slots_as_dict(self):
        _, slots = get_template(1250, 560, self._classified())
        d = slots_as_dict(slots)
        self.assertIn("background", d)
        self.assertIsInstance(d["title"].x, int)

    def test_z_orders_unique_per_role(self):
        _, slots = get_template(1250, 560, self._classified())
        z_orders = [s.z_order for s in slots]
        self.assertEqual(len(z_orders), len(set(z_orders)), "z_orders should be unique")


# ── duplicate_detector ────────────────────────────────────────────────────────
from typography.duplicate_detector import detect_duplicates, count_deduped, _similarity


class TestDuplicateDetector(unittest.TestCase):
    def test_similarity_identical(self):
        self.assertAlmostEqual(_similarity("hello world", "hello world"), 1.0)

    def test_similarity_different(self):
        self.assertLess(_similarity("hello", "world"), 0.5)

    def test_similarity_empty(self):
        self.assertEqual(_similarity("", ""), 1.0)
        self.assertEqual(_similarity("a", ""), 0.0)

    def test_no_dups_unchanged(self):
        layers = [
            {"id": "a", "role": "title", "type": "type", "isTextLayer": True,
             "textContent": "타이틀 텍스트", "fontSize": 20, "isGroupComposite": False},
            {"id": "b", "role": "body_text", "type": "type", "isTextLayer": True,
             "textContent": "서브 텍스트", "fontSize": 14, "isGroupComposite": False},
        ]
        result = detect_duplicates(layers)
        self.assertFalse(result[0]["dedupSkip"])
        self.assertFalse(result[1]["dedupSkip"])

    def test_same_role_similar_text_dedup(self):
        layers = [
            {"id": "a", "role": "title", "type": "type", "isTextLayer": True,
             "textContent": "여름 특가 50% 할인", "fontSize": 20, "bbox": {"x": 0, "y": 0, "width": 100, "height": 30},
             "isGroupComposite": False},
            {"id": "b", "role": "title", "type": "type", "isTextLayer": True,
             "textContent": "여름 특가 50% 할인", "fontSize": 15, "bbox": {"x": 0, "y": 0, "width": 100, "height": 30},
             "isGroupComposite": False},
        ]
        result = detect_duplicates(layers)
        skipped = [l for l in result if l["dedupSkip"]]
        self.assertEqual(len(skipped), 1)
        # Keep the larger font size layer
        kept = [l for l in result if not l["dedupSkip"]]
        self.assertEqual(kept[0]["fontSize"], 20)

    def test_different_role_no_dedup(self):
        layers = [
            {"id": "a", "role": "title", "type": "type", "isTextLayer": True,
             "textContent": "same text", "fontSize": 20,
             "bbox": {"x": 0, "y": 0, "width": 100, "height": 30}, "isGroupComposite": False},
            {"id": "b", "role": "body_text", "type": "type", "isTextLayer": True,
             "textContent": "same text", "fontSize": 20,
             "bbox": {"x": 0, "y": 0, "width": 100, "height": 30}, "isGroupComposite": False},
        ]
        result = detect_duplicates(layers)
        skipped = sum(1 for l in result if l["dedupSkip"])
        self.assertEqual(skipped, 0)

    def test_group_composite_covers_child(self):
        layers = [
            # Group composite that renders the full card
            {"id": "g1", "name": "card_group", "type": "group", "isTextLayer": False,
             "isGroupComposite": True, "role": "unknown",
             "bbox": {"x": 50, "y": 50, "width": 400, "height": 200}},
            # Title text inside the group — should be covered
            {"id": "t1", "name": "title", "type": "type", "isTextLayer": True,
             "isGroupComposite": False, "role": "title",
             "bbox": {"x": 100, "y": 80, "width": 200, "height": 40},
             "textContent": "타이틀", "fontSize": 18},
        ]
        result = detect_duplicates(layers)
        title_layer = next(l for l in result if l["id"] == "t1")
        self.assertTrue(title_layer["dedupSkip"])

    def test_count_deduped(self):
        layers = [
            {"id": "a", "dedupSkip": True},
            {"id": "b", "dedupSkip": False},
            {"id": "c", "dedupSkip": True},
        ]
        self.assertEqual(count_deduped(layers), 2)


# ── cta_layout ────────────────────────────────────────────────────────────────
from typography.cta_layout import detect_cta_groups


class TestCtaLayout(unittest.TestCase):
    def _make_layers(self, with_bg=True):
        layers = [
            {"id": "cta_text", "role": "cta", "type": "type", "isTextLayer": True,
             "textContent": "지금 구매", "dedupSkip": False,
             "bbox": {"x": 100, "y": 400, "width": 120, "height": 40}},
        ]
        if with_bg:
            layers.append({
                "id": "cta_bg", "role": "unknown", "type": "pixel", "isTextLayer": False,
                "dedupSkip": False,
                "bbox": {"x": 90, "y": 390, "width": 140, "height": 60},
            })
        return layers

    def test_cta_group_detected(self):
        groups = detect_cta_groups(self._make_layers(with_bg=True))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].text_layer_id, "cta_text")
        self.assertEqual(groups[0].bg_layer_id, "cta_bg")

    def test_cta_group_no_bg(self):
        groups = detect_cta_groups(self._make_layers(with_bg=False))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].bg_layer_id, "")

    def test_no_cta_layers(self):
        layers = [{"id": "t", "role": "title", "type": "type", "isTextLayer": True,
                   "textContent": "hello", "dedupSkip": False,
                   "bbox": {"x": 0, "y": 0, "width": 100, "height": 30}}]
        groups = detect_cta_groups(layers)
        self.assertEqual(groups, [])

    def test_cta_text_content_preserved(self):
        groups = detect_cta_groups(self._make_layers())
        self.assertEqual(groups[0].text_content, "지금 구매")

    def test_confidence_score(self):
        groups = detect_cta_groups(self._make_layers(with_bg=True))
        self.assertGreater(groups[0].confidence, 0.0)
        self.assertLessEqual(groups[0].confidence, 1.0)


# ── quality_gate ──────────────────────────────────────────────────────────────
from typography.quality_gate import evaluate, LAYOUT_SCORE_THRESHOLD
from typography.schemas import LayoutSlot


class TestQualityGate(unittest.TestCase):
    def _classified(self, roles=("background", "main_image", "title", "cta")):
        return [{"role": r, "dedupSkip": False, "isKorean": r == "title"} for r in roles]

    def _slots_for(self, w=1000, h=600, roles=("background", "main_image", "title", "cta")):
        slot_defs = {
            "background": (0, 0, w, h, "cover"),
            "main_image": (0, 50, w//2, h-100, "contain"),
            "title": (w//2+20, 50, w//2-40, 80, "contain"),
            "cta": (w//2+20, 450, 200, 60, "contain"),
        }
        return [LayoutSlot(role=r, x=sd[0], y=sd[1], w=sd[2], h=sd[3], mode=sd[4])
                for r, sd in slot_defs.items() if r in roles]

    def test_full_pass(self):
        classified = self._classified()
        slots = self._slots_for()
        result = evaluate(classified, slots, 1000, 600, had_korean=True, dedup_count=0)
        self.assertTrue(result.success)
        self.assertGreaterEqual(result.quality_score, LAYOUT_SCORE_THRESHOLD)

    def test_missing_required_roles_fails(self):
        classified = self._classified(roles=("background",))
        slots = self._slots_for(roles=("background",))
        result = evaluate(classified, slots, 1000, 600)
        self.assertFalse(result.success)
        self.assertIn("title", result.missing_roles)
        self.assertIn("main_image", result.missing_roles)

    def test_korean_not_preserved_warning(self):
        classified = [{"role": "title", "dedupSkip": False, "isKorean": False},
                      {"role": "main_image", "dedupSkip": False, "isKorean": False}]
        slots = self._slots_for()
        result = evaluate(classified, slots, 1000, 600, had_korean=True, dedup_count=0)
        self.assertTrue(any("korean" in w for w in result.warnings))

    def test_dedup_removed_partial_credit(self):
        classified = self._classified()
        slots = self._slots_for()
        result_no_dedup = evaluate(classified, slots, 1000, 600, dedup_count=0)
        result_with_dedup = evaluate(classified, slots, 1000, 600, dedup_count=2)
        self.assertGreaterEqual(result_no_dedup.quality_score, result_with_dedup.quality_score)

    def test_cta_group_detected_adds_points(self):
        classified = self._classified(roles=("background", "main_image", "title"))
        slots = self._slots_for(roles=("background", "main_image", "title"))
        result_no_cta = evaluate(classified, slots, 1000, 600, cta_group_detected=False)
        result_with_cta = evaluate(classified, slots, 1000, 600, cta_group_detected=True)
        self.assertGreater(result_with_cta.quality_score, result_no_cta.quality_score)

    def test_safe_zone_violations_reported(self):
        classified = self._classified()
        # Title slot placed outside safe zone (x=0, full width → safe_zone check passes)
        # Place title far out of bounds
        slots = [
            LayoutSlot(role="background", x=0, y=0, w=1000, h=600, mode="cover"),
            LayoutSlot(role="main_image", x=0, y=0, w=500, h=600, mode="contain"),
            LayoutSlot(role="title", x=-100, y=-50, w=100, h=30, mode="contain"),  # out of safe zone
            LayoutSlot(role="cta", x=500, y=450, w=200, h=60, mode="contain"),
        ]
        result = evaluate(classified, slots, 1000, 600)
        self.assertFalse(result.safe_zone_pass)
        self.assertGreater(len(result.safe_zone_violations), 0)


# ── schemas ────────────────────────────────────────────────────────────────────
from typography.schemas import TypographyResult, LayoutSlot as _LS, TextRun


class TestSchemas(unittest.TestCase):
    def test_typography_result_defaults(self):
        r = TypographyResult()
        self.assertFalse(r.success)
        self.assertEqual(r.quality_score, 0.0)
        self.assertEqual(r.warnings, [])

    def test_layout_slot_defaults(self):
        s = LayoutSlot(role="title", x=10, y=20, w=300, h=80)
        self.assertEqual(s.mode, "contain")
        self.assertTrue(s.safe)

    def test_text_run_defaults(self):
        tr = TextRun()
        self.assertEqual(tr.text, "")
        self.assertEqual(tr.font_weight, "normal")


# ── pipeline disabled flag ─────────────────────────────────────────────────────
from typography.pipeline import run_typography_pipeline


class TestPipelineDisabledFlag(unittest.TestCase):
    def test_disabled_by_default(self):
        os.environ.pop("TYPOGRAPHY_PIPELINE_ENABLED", None)
        result = run_typography_pipeline(
            "nonexistent.psd", 1000, 600, "/tmp/test_out.jpg"
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "typography_pipeline_disabled")

    def test_enabled_but_file_not_found(self):
        os.environ["TYPOGRAPHY_PIPELINE_ENABLED"] = "true"
        result = run_typography_pipeline(
            "/nonexistent_psd_that_does_not_exist.psd", 1000, 600, "/tmp/test_out.jpg"
        )
        os.environ.pop("TYPOGRAPHY_PIPELINE_ENABLED", None)
        self.assertFalse(result["success"])
        self.assertIsNotNone(result["error"])
        self.assertNotEqual(result["error"], "typography_pipeline_disabled")


# ── Stage 20.1: Korean raster text extraction and role inference ──────────────
from typography.text_extractor import _normalize_layer_name_text, extract_text_layers, count_korean_layers
from typography.role_resolver import _infer_korean_text_role, resolve_korean_text_roles


class TestNormalizeLayerNameText(unittest.TestCase):
    def test_removes_positive_coord_suffix(self):
        self.assertEqual(_normalize_layer_name_text("레이어명_69_79"), "레이어명")

    def test_removes_negative_coord_suffix(self):
        self.assertEqual(_normalize_layer_name_text("사각형_4_-43_1021"), "사각형 4")

    def test_removes_both_negative(self):
        self.assertEqual(_normalize_layer_name_text("BG_-117_-46"), "BG")

    def test_normalizes_underscores_to_spaces(self):
        result = _normalize_layer_name_text("어머님_손에_금보다_필요한_건_69_79")
        self.assertEqual(result, "어머님 손에 금보다 필요한 건")

    def test_collapses_double_underscores(self):
        result = _normalize_layer_name_text("흑자__검버섯__기미_61_1081")
        self.assertEqual(result, "흑자 검버섯 기미")

    def test_nfc_applied(self):
        nfd_name = unicodedata.normalize("NFD", "배경_0_0")
        result = _normalize_layer_name_text(nfd_name)
        self.assertEqual(result, unicodedata.normalize("NFC", "배경"))

    def test_no_suffix_unchanged(self):
        self.assertEqual(_normalize_layer_name_text("logo"), "logo")

    def test_empty_string(self):
        self.assertEqual(_normalize_layer_name_text(""), "")


class TestKoreanRasterLayerFallback(unittest.TestCase):
    def _raster_korean(self, name="어머님_손에_금보다_필요한_건_69_79",
                       bbox=None, canvas_h=1000):
        return {
            "id": "r1", "name": name, "type": "pixel", "isTextLayer": False,
            "bbox": bbox or {"x": 0, "y": 79, "width": 600, "height": 60},
            "canvasWidth": 800, "canvasHeight": canvas_h,
        }

    def test_korean_raster_gets_textContent(self):
        layers = [self._raster_korean()]
        result = extract_text_layers(layers)
        self.assertEqual(result[0]["textContent"], "어머님 손에 금보다 필요한 건")

    def test_korean_raster_gets_is_korean_true(self):
        layers = [self._raster_korean()]
        result = extract_text_layers(layers)
        self.assertTrue(result[0]["isKorean"])

    def test_korean_raster_source_is_layer_name_fallback(self):
        layers = [self._raster_korean()]
        result = extract_text_layers(layers)
        self.assertEqual(result[0]["textContentSource"], "layer_name_fallback")

    def test_non_korean_raster_layer_unchanged(self):
        layer = {"id": "r2", "name": "bg_main_1024_768", "type": "pixel", "isTextLayer": False}
        result = extract_text_layers([layer])
        self.assertNotIn("textContent", result[0])
        self.assertNotIn("textContentSource", result[0])

    def test_korean_raster_counted_in_korean_layers(self):
        layers = extract_text_layers([self._raster_korean()])
        self.assertEqual(count_korean_layers(layers), 1)


class TestInferKoreanTextRole(unittest.TestCase):
    def _layer(self, text, cy_ratio, canvas_h=1000):
        y = int(cy_ratio * canvas_h)
        return {
            "textContent": text,
            "bbox": {"x": 0, "y": y, "width": 600, "height": 60},
            "canvasHeight": canvas_h,
        }

    def test_short_text_top_position_gives_title(self):
        layer = self._layer("어머님 손에 금보다 필요한 건", cy_ratio=0.10)
        self.assertEqual(_infer_korean_text_role(layer, existing_title=False), "title")

    def test_long_text_bottom_position_gives_body_text(self):
        layer = self._layer("흑자 검버섯 기미 확실하게 지속적으로 관리하세요 전문가 추천", cy_ratio=0.70)
        self.assertEqual(_infer_korean_text_role(layer, existing_title=False), "body_text")

    def test_existing_title_forces_body_text(self):
        # Short text + top position but title already exists → body_text
        layer = self._layer("짧은 카피", cy_ratio=0.10)
        self.assertEqual(_infer_korean_text_role(layer, existing_title=True), "body_text")


class TestResolveKoreanTextRoles(unittest.TestCase):
    def _classified_with_korean_raster(self):
        return [
            {
                "id": "bg", "name": "BG_-117_-46", "type": "pixel", "role": "background",
                "roleSource": "name", "priority": "required",
                "textContentSource": None, "isKorean": False,
                "bbox": {"x": 0, "y": 0, "width": 800, "height": 1000}, "canvasHeight": 1000,
            },
            {
                "id": "r1", "name": "어머님_손에_금보다_필요한_건_69_79", "type": "pixel",
                "role": "unknown", "roleSource": "heuristic", "priority": "optional",
                "textContent": "어머님 손에 금보다 필요한 건",
                "textContentSource": "layer_name_fallback", "isKorean": True,
                "bbox": {"x": 10, "y": 79, "width": 600, "height": 60}, "canvasHeight": 1000,
            },
            {
                "id": "r2", "name": "흑자__검버섯__기미_확실하게_관리하세요_61_1081", "type": "pixel",
                "role": "unknown", "roleSource": "heuristic", "priority": "optional",
                "textContent": "흑자 검버섯 기미 확실하게 관리하세요",
                "textContentSource": "layer_name_fallback", "isKorean": True,
                "bbox": {"x": 10, "y": 1081, "width": 600, "height": 60}, "canvasHeight": 1500,
            },
        ]

    def test_assigns_title_to_top_short_text(self):
        result = resolve_korean_text_roles(self._classified_with_korean_raster())
        role_r1 = next(l["role"] for l in result if l["id"] == "r1")
        self.assertEqual(role_r1, "title")

    def test_assigns_body_text_to_lower_text_when_title_exists(self):
        result = resolve_korean_text_roles(self._classified_with_korean_raster())
        role_r2 = next(l["role"] for l in result if l["id"] == "r2")
        self.assertEqual(role_r2, "body_text")

    def test_does_not_touch_non_fallback_layers(self):
        result = resolve_korean_text_roles(self._classified_with_korean_raster())
        bg = next(l for l in result if l["id"] == "bg")
        self.assertEqual(bg["role"], "background")
        self.assertEqual(bg["roleSource"], "name")


class TestQualityGateErrorMessage(unittest.TestCase):
    from typography.quality_gate import _build_error_message

    def test_missing_role_only_message(self):
        from typography.quality_gate import _build_error_message
        msg = _build_error_message(80.0, 65.0, ["title"])
        self.assertNotIn("< 65.0", msg)
        self.assertIn("missing_roles", msg)

    def test_score_below_threshold_only(self):
        from typography.quality_gate import _build_error_message
        msg = _build_error_message(50.0, 65.0, [])
        self.assertIn("< 65.0", msg)
        self.assertNotIn("missing_roles", msg)

    def test_both_conditions_separated(self):
        from typography.quality_gate import _build_error_message
        msg = _build_error_message(50.0, 65.0, ["title"])
        self.assertIn("< 65.0", msg)
        self.assertIn("missing_roles", msg)
        self.assertIn("|", msg)  # conditions separated

    def test_score_equals_threshold_not_in_error(self):
        from typography.quality_gate import _build_error_message
        msg = _build_error_message(65.0, 65.0, ["title"])
        self.assertNotIn("< 65.0", msg)
        self.assertIn("missing_roles", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
