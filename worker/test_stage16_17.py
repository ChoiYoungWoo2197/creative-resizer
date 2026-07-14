"""Stage 16+17 PoC 단위 테스트.

A. PSD alpha product mask test
B. bbox coarse mask test
C. wide visual_context rejection test
D. text/cta mask generation test
E. local inpaint smoke test
F. local outpaint smoke test
G. fallback safety test (provider unavailable)
H. metadata fields test

기존 테스트 비교:
  - test_stage14_15.py    (25 tests) → MUST NOT BREAK
  - test_layout_repair.py (7 tests)  → MUST NOT BREAK
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image

# ─── import target modules ────────────────────────────────────────────────────
import mask_utils
from mask_utils import (
    create_mask_dict, bbox_to_mask_image, psd_alpha_to_canvas_mask,
    feather_mask, scale_mask_to_target, build_mask_union, compute_mask_quality,
    MASK_ROLES, SOURCE_PRIORITY,
)
import segmentation_poc
from segmentation_poc import (
    is_segmentation_poc_enabled, _classify_mask_role, run_segmentation_poc,
)
import inpaint_outpaint_poc
from inpaint_outpaint_poc import (
    run_inpaint_poc, run_outpaint_poc,
    inpaint_with_external_ai, outpaint_with_external_ai, generate_mask_with_external_ai,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _solid_rgba(w, h, color=(200, 100, 50, 255)) -> Image.Image:
    return Image.new("RGBA", (w, h), color)


def _transparent_rgba(w, h, alpha=100) -> Image.Image:
    img = Image.new("RGBA", (w, h), (255, 0, 0, alpha))
    return img


def _make_cos_with_jar(jar_path: str, wide_path: str) -> dict:
    """2개 main_image 후보 + headline COS."""
    return {
        "canvas": {"width": 1200, "height": 628},
        "warnings": [],
        "objects": [
            {
                "id": "obj_bg", "role": "background",
                "imagePath": None,
                "bbox": {"x": 0, "y": 0, "width": 526, "height": 628},
                "sourceType": "psd_layer_smartobject", "qualityRisk": None,
            },
            {
                "id": "obj_jar", "role": "main_image",
                "imagePath": jar_path,
                "bbox": {"x": 900, "y": 50, "width": 140, "height": 270},
                "sourceType": "psd_layer_smartobject", "qualityRisk": None,
            },
            {
                "id": "obj_wide", "role": "main_image",
                "imagePath": wide_path,
                "bbox": {"x": 0, "y": 0, "width": 900, "height": 628},
                "sourceType": "ai_bbox_crop", "qualityRisk": "high",
            },
            {
                "id": "obj_hl", "role": "headline",
                "imagePath": None,
                "bbox": {"x": 50, "y": 100, "width": 400, "height": 80},
                "sourceType": "ai_bbox_crop", "qualityRisk": None,
            },
        ],
    }


# ─── A. PSD alpha product mask test ──────────────────────────────────────────

class TestPsdAlphaMask(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = os.path.join(
            os.path.dirname(__file__), "_test_tmp_stage16"
        )
        os.makedirs(self.tmp_dir, exist_ok=True)

    def test_alpha_mask_extracted_from_transparent_image(self):
        """투명 배경 이미지에서 alpha mask 추출 → product mask 생성."""
        # 투명 픽셀이 많은 이미지 생성 (배경=0, 제품 영역=255)
        layer = Image.new("RGBA", (140, 270), (0, 0, 0, 0))  # all transparent
        # 중앙 50×100 픽셀에 제품 색상 (alpha=220)
        product_region = Image.new("RGBA", (50, 100), (255, 50, 50, 220))
        layer.paste(product_region, (45, 85))

        jar_path = os.path.join(self.tmp_dir, "jar_transparent.png")
        layer.save(jar_path)

        bbox = {"x": 900, "y": 50, "width": 140, "height": 270}
        canvas_w, canvas_h = 1200, 628

        result = psd_alpha_to_canvas_mask(layer, bbox, canvas_w, canvas_h)
        self.assertIsNotNone(result, "alpha mask 추출 실패")
        self.assertEqual(result.size, (canvas_w, canvas_h))
        # 제품 영역 (canvas 좌표: x=945+, y=135+)은 white
        self.assertGreater(result.getpixel((945, 135)), 100)
        # 좌상단 빈 영역은 black
        self.assertEqual(result.getpixel((0, 0)), 0)

    def test_solid_image_returns_none(self):
        """완전 불투명 이미지 → alpha mask None 반환 (solid image, no transparent region)."""
        solid = Image.new("RGBA", (100, 200), (255, 100, 50, 255))
        bbox = {"x": 0, "y": 0, "width": 100, "height": 200}
        result = psd_alpha_to_canvas_mask(solid, bbox, 1200, 628)
        self.assertIsNone(result, "solid image는 None이어야 함")

    def test_run_segmentation_poc_produces_product_mask(self):
        """run_segmentation_poc: Photoroom 경로 → product mask 생성."""
        # Photoroom 이름 포함 jar 이미지 (투명 배경)
        jar = Image.new("RGBA", (140, 270), (0, 0, 0, 0))
        jar.paste(Image.new("RGBA", (60, 120), (255, 50, 50, 200)), (40, 75))
        jar_path = os.path.join(self.tmp_dir, "4837_Photoroom_jar.png")
        jar.save(jar_path)

        wide = _solid_rgba(900, 628)
        wide_path = os.path.join(self.tmp_dir, "wide_crop.png")
        wide.save(wide_path)

        cos = _make_cos_with_jar(jar_path, wide_path)
        # jar imagePath Photoroom 포함
        cos["objects"][1]["imagePath"] = jar_path

        masks, meta = run_segmentation_poc(
            cos, None, 1200, 628, self.tmp_dir, "test_a",
            extra_flags={"experimentalSegmentation": True},
        )
        self.assertTrue(meta["segmentationPocEnabled"])
        self.assertGreater(meta["masksGenerated"], 0)
        self.assertTrue(meta["productMaskSelected"])
        self.assertIsNotNone(meta["productMaskId"])
        product_masks = [m for m in masks if m["role"] == "product"]
        self.assertGreater(len(product_masks), 0)
        self.assertEqual(product_masks[0]["source"], "psd_alpha")


# ─── B. bbox coarse mask test ─────────────────────────────────────────────────

class TestBboxCoarseMask(unittest.TestCase):
    def test_bbox_to_mask_image_fills_region(self):
        """bbox_to_mask_image: bbox 영역이 white로 채워짐."""
        bbox = {"x": 20, "y": 30, "width": 60, "height": 40}
        mask = bbox_to_mask_image(bbox, 200, 100)
        self.assertEqual(mask.size, (200, 100))
        self.assertGreater(mask.getpixel((50, 50)), 200)  # center of bbox = white
        self.assertEqual(mask.getpixel((0, 0)), 0)         # outside = black
        self.assertEqual(mask.getpixel((199, 99)), 0)

    def test_bbox_coarse_mask_generated_for_non_transparent_object(self):
        """solid 이미지 → bbox coarse mask (psd_alpha 실패 → coarse fallback)."""
        solid = _solid_rgba(200, 300)
        solid_path = os.path.join(os.path.dirname(__file__), "_tmp_solid.png")
        solid.save(solid_path)

        cos = {
            "canvas": {"width": 600, "height": 400},
            "warnings": [],
            "objects": [
                {
                    "id": "obj_solid", "role": "main_image",
                    "imagePath": solid_path,
                    "bbox": {"x": 100, "y": 50, "width": 200, "height": 300},
                    "sourceType": "ai_bbox_crop", "qualityRisk": None,
                },
            ],
        }
        masks, meta = run_segmentation_poc(
            cos, None, 600, 400, None, "test_b",
            extra_flags={"experimentalSegmentation": True},
        )
        self.assertGreater(meta["masksGenerated"], 0)
        m = masks[0]
        self.assertEqual(m["source"], "object_bbox_coarse")
        self.assertIn(m["role"], MASK_ROLES)

        os.unlink(solid_path)


# ─── C. wide visual_context rejection test ────────────────────────────────────

class TestVisualContextRejection(unittest.TestCase):
    def test_wide_large_area_classified_as_visual_context(self):
        """area 66% + ai_bbox_crop → visual_context (not product)."""
        obj = {
            "id": "obj_wide",
            "role": "main_image",
            "imagePath": None,
            "bbox": {"x": 0, "y": 0, "width": 900, "height": 628},
            "sourceType": "ai_bbox_crop",
            "qualityRisk": "high",
        }
        from layout_compiler import score_product_candidate
        score = score_product_candidate(obj, 1200, 628)
        role = _classify_mask_role(obj, score)
        # area 66% + ai_bbox_crop → score very low → visual_context
        self.assertIn(role, {"visual_context", "person_or_hand"})
        self.assertNotEqual(role, "product")

    def test_narrow_isolated_jar_classified_as_product(self):
        """세장형(h/w≥1.5) + small area + no qualityRisk → product."""
        obj = {
            "id": "obj_jar",
            "role": "main_image",
            "imagePath": None,
            "bbox": {"x": 900, "y": 50, "width": 140, "height": 270},
            "sourceType": "psd_layer_smartobject",
            "qualityRisk": None,
        }
        from layout_compiler import score_product_candidate
        score = score_product_candidate(obj, 1200, 628)
        role = _classify_mask_role(obj, score)
        self.assertGreater(score, 60.0)
        self.assertEqual(role, "product")


# ─── D. text/cta mask generation test ────────────────────────────────────────

class TestTextCtaMaskGeneration(unittest.TestCase):
    def test_headline_classified_as_text_mask(self):
        """role=headline → text mask."""
        obj = {
            "id": "obj_hl", "role": "headline",
            "imagePath": None,
            "bbox": {"x": 50, "y": 100, "width": 400, "height": 80},
            "sourceType": "ai_bbox_crop", "qualityRisk": None,
        }
        role = _classify_mask_role(obj, 50.0)
        self.assertEqual(role, "text")

    def test_cta_classified_as_cta_mask(self):
        """role=cta → cta mask."""
        obj = {
            "id": "obj_cta", "role": "cta",
            "imagePath": None,
            "bbox": {"x": 50, "y": 400, "width": 150, "height": 50},
            "sourceType": "ai_bbox_crop", "qualityRisk": None,
        }
        role = _classify_mask_role(obj, 50.0)
        self.assertEqual(role, "cta")

    def test_text_cta_masks_generated_in_poc(self):
        """run_segmentation_poc: text + cta masks 생성 확인."""
        cos = {
            "canvas": {"width": 1200, "height": 628},
            "warnings": [],
            "objects": [
                {
                    "id": "obj_hl", "role": "headline",
                    "imagePath": None,
                    "bbox": {"x": 50, "y": 100, "width": 400, "height": 80},
                    "sourceType": "ai_bbox_crop", "qualityRisk": None,
                },
                {
                    "id": "obj_cta", "role": "cta",
                    "imagePath": None,
                    "bbox": {"x": 50, "y": 400, "width": 150, "height": 50},
                    "sourceType": "ai_bbox_crop", "qualityRisk": None,
                },
            ],
        }
        masks, meta = run_segmentation_poc(
            cos, None, 1200, 628, None, "test_d",
            extra_flags={"experimentalSegmentation": True},
        )
        roles = {m["role"] for m in masks}
        self.assertIn("text", roles)
        self.assertIn("cta", roles)
        self.assertGreaterEqual(meta["masksGenerated"], 2)


# ─── E. local inpaint smoke test ─────────────────────────────────────────────

class TestLocalInpaintSmoke(unittest.TestCase):
    def _make_product_mask(self, w, h) -> dict:
        """단순 product mask dict (중앙 작은 영역)."""
        mask_img = Image.new("L", (w, h), 0)
        # 중앙 10% 영역
        rx, ry = w // 4, h // 4
        rw, rh = w // 2, h // 2
        mask_img.paste(Image.new("L", (rw, rh), 255), (rx, ry))
        return {
            "maskId": "mask_product_001", "objectId": "obj_jar",
            "role": "product", "source": "psd_alpha",
            "bbox": {"x": rx, "y": ry, "width": rw, "height": rh},
            "areaRatio": 0.10, "quality": {}, "maskPath": None,
            "_maskImg": mask_img,
        }

    def test_inpaint_produces_output_image(self):
        """local heuristic inpaint: 출력 이미지가 존재하고 크기가 올바름."""
        bg = _solid_rgba(600, 400, (100, 150, 200, 255))
        mask_dict = self._make_product_mask(600, 400)

        result, meta = run_inpaint_poc(
            bg, [mask_dict], 600, 400, 600, 400,
            None, "test_e",
            extra_flags={"experimentalInpaint": True},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.size, (600, 400))
        self.assertTrue(meta["inpaintPocEnabled"])
        self.assertTrue(meta["inpaintApplied"])
        self.assertEqual(meta["inpaintProvider"], "local_heuristic")
        self.assertGreater(meta["inpaintQualityScore"], 0.0)

    def test_inpaint_no_masked_pixels_skipped(self):
        """mask 영역 없으면 inpaint skip, bg 원본 반환."""
        bg = _solid_rgba(400, 300)
        empty_mask = {
            "maskId": "mask_text_001", "objectId": "obj_hl",
            "role": "text", "source": "object_bbox_coarse",
            "bbox": {"x": 0, "y": 0, "width": 0, "height": 0},
            "areaRatio": 0.0, "quality": {}, "maskPath": None,
            "_maskImg": Image.new("L", (400, 300), 0),  # all black = no mask
        }
        result, meta = run_inpaint_poc(
            bg, [empty_mask], 400, 300, 400, 300,
            None, "test_e2",
            extra_flags={"experimentalInpaint": True},
        )
        self.assertFalse(meta["inpaintApplied"])
        self.assertEqual(result.size, (400, 300))

    def test_inpaint_flag_off_returns_unchanged(self):
        """flag OFF → inpaintApplied=False, 원본 반환."""
        bg = _solid_rgba(400, 300)
        result, meta = run_inpaint_poc(
            bg, [], 400, 300, 400, 300,
            None, "test_e3",
            extra_flags={},
        )
        self.assertFalse(meta.get("inpaintApplied", False))
        self.assertEqual(result.size, (400, 300))


# ─── F. local outpaint smoke test ────────────────────────────────────────────

class TestLocalOutpaintSmoke(unittest.TestCase):
    def test_outpaint_1000x1000_to_1250x560(self):
        """1000x1000 → 1250x560 (비율 차이 큼) → outpaint 동작."""
        src = _solid_rgba(1000, 1000, (80, 120, 200, 255))
        result, meta = run_outpaint_poc(
            src, 1000, 1000, 1250, 560,
            None, "test_f1",
            extra_flags={"experimentalOutpaint": True},
        )
        self.assertEqual(result.size, (1250, 560))
        self.assertTrue(meta["outpaintApplied"])
        self.assertEqual(meta["outpaintProvider"], "local_heuristic")
        self.assertGreater(meta["outpaintQualityScore"], 0.0)

    def test_outpaint_1000x1000_to_1200x628(self):
        """1000x1000 → 1200x628 → outpaint 동작."""
        src = _solid_rgba(1000, 1000)
        result, meta = run_outpaint_poc(
            src, 1000, 1000, 1200, 628,
            None, "test_f2",
            extra_flags={"experimentalOutpaint": True},
        )
        self.assertEqual(result.size, (1200, 628))
        self.assertTrue(meta["outpaintApplied"])

    def test_outpaint_small_ratio_diff_skipped(self):
        """비율 차이 < 1.3 → outpaint skip."""
        src = _solid_rgba(1200, 628)
        result, meta = run_outpaint_poc(
            src, 1200, 628, 1250, 560,
            None, "test_f3",
            extra_flags={"experimentalOutpaint": True},
        )
        # 1200/628=1.91, 1250/560=2.23 → ratio_diff = 2.23/1.91 ≈ 1.17 < 1.3 → skip
        self.assertFalse(meta["outpaintApplied"])

    def test_outpaint_flag_off_returns_unchanged(self):
        """flag OFF → outpaintApplied=False."""
        src = _solid_rgba(1000, 1000)
        result, meta = run_outpaint_poc(
            src, 1000, 1000, 1250, 560,
            None, "test_f4",
            extra_flags={},
        )
        self.assertFalse(meta.get("outpaintApplied", False))


# ─── G. fallback safety test ─────────────────────────────────────────────────

class TestFallbackSafety(unittest.TestCase):
    def test_external_ai_stub_returns_none_without_key(self):
        """외부 AI provider: API key 없으면 None 반환 (providerUnavailable)."""
        bg = _solid_rgba(400, 300)
        mask = Image.new("L", (400, 300), 128)
        result = inpaint_with_external_ai(bg, mask, api_key=None)
        self.assertIsNone(result)

        result_out = outpaint_with_external_ai(bg, 800, 600, api_key=None)
        self.assertIsNone(result_out)

        result_seg = generate_mask_with_external_ai(bg, api_key=None)
        self.assertIsNone(result_seg)

    def test_segmentation_poc_disabled_when_flag_off(self):
        """flag OFF → segmentationPocEnabled=False, masks=[]."""
        cos = {
            "canvas": {"width": 600, "height": 400},
            "warnings": [],
            "objects": [
                {"id": "obj1", "role": "main_image",
                 "imagePath": None,
                 "bbox": {"x": 0, "y": 0, "width": 100, "height": 200},
                 "sourceType": "psd_layer_smartobject", "qualityRisk": None},
            ],
        }
        masks, meta = run_segmentation_poc(
            cos, None, 600, 400, None, "test_g",
            extra_flags={},  # experimentalSegmentation=False
        )
        self.assertEqual(masks, [])
        self.assertFalse(meta.get("segmentationPocEnabled", True))

    def test_inpaint_exception_returns_original_bg(self):
        """inpaint 내부 예외 → 원본 bg 반환, 예외 전파 없음."""
        bg = _solid_rgba(200, 200)
        # 잘못된 mask dict (no _maskImg, no maskPath)
        bad_mask = {
            "maskId": "bad", "objectId": "bad", "role": "product",
            "source": "psd_alpha", "bbox": {}, "areaRatio": 0.0,
            "quality": {}, "maskPath": None, "_maskImg": None,
        }
        # _poc_inpaint_on=True, masks=[bad_mask] → union이 0 pixels → skip
        result, meta = run_inpaint_poc(
            bg, [bad_mask], 200, 200, 200, 200,
            None, "test_g2",
            extra_flags={"experimentalInpaint": True},
        )
        # job must not die — result must be a valid image
        self.assertIsNotNone(result)
        self.assertEqual(result.size, (200, 200))

    def test_run_segmentation_poc_empty_objects(self):
        """objects=[] → masksGenerated=0, 예외 없음."""
        cos = {"canvas": {"width": 600, "height": 400}, "warnings": [], "objects": []}
        masks, meta = run_segmentation_poc(
            cos, None, 600, 400, None, "test_g3",
            extra_flags={"experimentalSegmentation": True},
        )
        self.assertEqual(masks, [])
        self.assertEqual(meta["masksGenerated"], 0)
        self.assertFalse(meta["productMaskSelected"])


# ─── H. metadata fields test ─────────────────────────────────────────────────

class TestMetadataFields(unittest.TestCase):
    def _make_simple_masks(self, canvas_w: int, canvas_h: int) -> list[dict]:
        mask_img = Image.new("L", (canvas_w, canvas_h), 0)
        mask_img.paste(Image.new("L", (100, 100), 255), (50, 50))
        return [
            create_mask_dict(
                "mask_product_001", "obj_jar", "product", "psd_alpha",
                {"x": 50, "y": 50, "width": 100, "height": 100},
                canvas_w, canvas_h,
                mask_img=mask_img,
            ),
        ]

    def test_segmentation_metadata_required_fields(self):
        """run_segmentation_poc metadata 필수 필드 존재 확인."""
        cos = {
            "canvas": {"width": 400, "height": 300},
            "warnings": [],
            "objects": [
                {"id": "obj_hl", "role": "headline",
                 "imagePath": None,
                 "bbox": {"x": 10, "y": 10, "width": 100, "height": 30},
                 "sourceType": "ai_bbox_crop", "qualityRisk": None},
            ],
        }
        _, meta = run_segmentation_poc(
            cos, None, 400, 300, None, "test_h",
            extra_flags={"experimentalSegmentation": True},
        )
        required = [
            "segmentationPocEnabled", "masksGenerated", "productMaskSelected",
            "maskQualityScore", "maskFallbackUsed", "maskWarnings",
        ]
        for key in required:
            self.assertIn(key, meta, f"missing key: {key}")

    def test_inpaint_metadata_required_fields(self):
        """run_inpaint_poc metadata 필수 필드 존재 확인."""
        bg = _solid_rgba(400, 300)
        masks = self._make_simple_masks(400, 300)
        _, meta = run_inpaint_poc(
            bg, masks, 400, 300, 400, 300,
            None, "test_h2",
            extra_flags={"experimentalInpaint": True},
        )
        required = [
            "inpaintPocEnabled", "inpaintApplied", "inpaintProvider",
            "inpaintQualityScore", "inpaintFallbackUsed",
            "cleanBackgroundUsed", "backgroundMaskIds",
        ]
        for key in required:
            self.assertIn(key, meta, f"missing key: {key}")

    def test_outpaint_metadata_required_fields(self):
        """run_outpaint_poc metadata 필수 필드 존재 확인."""
        src = _solid_rgba(800, 800)
        _, meta = run_outpaint_poc(
            src, 800, 800, 1200, 628,
            None, "test_h3",
            extra_flags={"experimentalOutpaint": True},
        )
        required = [
            "outpaintPocEnabled", "outpaintApplied", "outpaintProvider",
            "outpaintQualityScore", "outpaintFallbackUsed",
        ]
        for key in required:
            self.assertIn(key, meta, f"missing key: {key}")

    def test_create_mask_dict_structure(self):
        """create_mask_dict 구조 검증."""
        mask_img = Image.new("L", (200, 200), 128)
        m = create_mask_dict(
            "mask_001", "obj_jar", "product", "psd_alpha",
            {"x": 0, "y": 0, "width": 100, "height": 100},
            200, 200, confidence=0.9, mask_img=mask_img,
        )
        self.assertEqual(m["maskId"], "mask_001")
        self.assertEqual(m["role"], "product")
        self.assertAlmostEqual(m["areaRatio"], 0.25)
        self.assertEqual(m["_maskImg"], mask_img)
        self.assertIn("quality", m)

    def test_compute_mask_quality_structure(self):
        """compute_mask_quality: 필수 키 존재 + 범위 유효."""
        q = compute_mask_quality(
            "psd_alpha", {"x": 0, "y": 0, "width": 100, "height": 100},
            1000, 500, product_score=85.0,
        )
        for key in ("edgeSharpness", "alphaCoverage", "leakRisk",
                    "sourcePriority", "overallScore"):
            self.assertIn(key, q)
            if key != "areaRatio":
                self.assertGreaterEqual(q[key], 0.0)
                self.assertLessEqual(q[key], 1.0)


# ─── Regression: existing Stage 14+15 tests must still pass ──────────────────

class TestRegressionImports(unittest.TestCase):
    def test_layout_compiler_import(self):
        """layout_compiler import 정상."""
        import layout_compiler  # noqa: F401

    def test_background_builder_import(self):
        """background_builder import 정상."""
        import background_builder  # noqa: F401

    def test_safe_zone_import(self):
        """safe_zone import 정상."""
        import safe_zone  # noqa: F401

    def test_layout_compositor_import(self):
        """layout_compositor import 정상."""
        import layout_compositor  # noqa: F401

    def test_stage14_15_isolation_unchanged(self):
        """Stage 14 score_product_candidate가 여전히 jar=97, wide=5."""
        from layout_compiler import score_product_candidate
        jar_obj = {
            "id": "obj_jar", "role": "main_image",
            "imagePath": "4837_Photoroom_jar.png",
            "bbox": {"x": 982, "y": 75, "width": 137, "height": 265},
            "sourceType": "psd_layer_smartobject", "qualityRisk": None,
        }
        wide_obj = {
            "id": "obj_wide", "role": "main_image",
            "imagePath": None,
            "bbox": {"x": 0, "y": 0, "width": 800, "height": 628},
            "sourceType": "ai_bbox_crop", "qualityRisk": "high",
        }
        s_jar = score_product_candidate(jar_obj, 1200, 628)
        s_wide = score_product_candidate(wide_obj, 1200, 628)
        self.assertGreater(s_jar, 80.0)
        self.assertLess(s_wide, 20.0)

    def test_background_naturalness_score_unchanged(self):
        """Stage 15 naturalness score 값 유지."""
        from background_builder import compute_background_naturalness_score
        self.assertEqual(compute_background_naturalness_score("psd_background_cover"), 85.0)
        self.assertEqual(compute_background_naturalness_score("psd_background_extend"), 70.0)
        self.assertEqual(compute_background_naturalness_score("emergency_neutral"), 15.0)


# ─── run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    test_classes = [
        TestPsdAlphaMask,
        TestBboxCoarseMask,
        TestVisualContextRejection,
        TestTextCtaMaskGeneration,
        TestLocalInpaintSmoke,
        TestLocalOutpaintSmoke,
        TestFallbackSafety,
        TestMetadataFields,
        TestRegressionImports,
    ]
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed
    print(f"\n{'='*60}")
    print(f"Stage 16+17 PoC test: {passed}/{total} PASS  ({failed} FAIL)")
    print(f"{'='*60}")

    sys.exit(0 if failed == 0 else 1)
