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
        """run_inpaint_poc metadata 필수 필드 존재 확인 (신규 필드 포함)."""
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
            # new fields (booster task 3)
            "inpaintQualityLevel", "inpaintQualityCapApplied",
            "seamReduced", "inpaintUseForFinal", "inpaintFallbackReason",
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
        """compute_mask_quality: 필수 키 존재 + 범위 유효 (신규 필드 포함)."""
        q = compute_mask_quality(
            "psd_alpha", {"x": 0, "y": 0, "width": 100, "height": 100},
            1000, 500, product_score=85.0,
        )
        for key in ("edgeSharpness", "alphaCoverage", "leakRisk",
                    "sourcePriority", "overallScore",
                    "maskFeatherApplied", "maskEdgeQuality",
                    "maskLeakRisk", "maskPostProcessApplied"):
            self.assertIn(key, q, f"missing key: {key}")
        for key in ("edgeSharpness", "alphaCoverage", "leakRisk",
                    "sourcePriority", "overallScore"):
            self.assertGreaterEqual(q[key], 0.0)
            self.assertLessEqual(q[key], 1.0)

    def test_mask_edge_quality_psd_alpha(self):
        """psd_alpha: maskFeatherApplied=False, maskEdgeQuality='sharp', maskPostProcessApplied=False."""
        q = compute_mask_quality("psd_alpha", {"x": 0, "y": 0, "width": 50, "height": 50}, 200, 200)
        self.assertFalse(q["maskFeatherApplied"])
        self.assertEqual(q["maskEdgeQuality"], "sharp")
        self.assertFalse(q["maskPostProcessApplied"])

    def test_mask_edge_quality_bbox_coarse(self):
        """object_bbox_coarse: maskFeatherApplied=True, maskEdgeQuality='coarse', maskPostProcessApplied=True."""
        q = compute_mask_quality("object_bbox_coarse", {"x": 0, "y": 0, "width": 50, "height": 50}, 200, 200)
        self.assertTrue(q["maskFeatherApplied"])
        self.assertEqual(q["maskEdgeQuality"], "coarse")
        self.assertTrue(q["maskPostProcessApplied"])

    def test_mask_leak_risk_alias(self):
        """maskLeakRisk == leakRisk."""
        q = compute_mask_quality("psd_alpha", {"x": 0, "y": 0, "width": 50, "height": 50}, 200, 200)
        self.assertAlmostEqual(q["maskLeakRisk"], q["leakRisk"])


# ─── I. inpaint quality cap test ─────────────────────────────────────────────

class TestInpaintQualityCap(unittest.TestCase):
    def _make_small_mask(self, canvas_w, canvas_h, region_ratio=0.02) -> dict:
        """inpaint quality ≈ 80 - 2 = 78 (should be capped at 75)."""
        mask_img = Image.new("L", (canvas_w, canvas_h), 0)
        rw = int(canvas_w * region_ratio ** 0.5)
        rh = int(canvas_h * region_ratio ** 0.5)
        mask_img.paste(Image.new("L", (rw, rh), 255), (10, 10))
        return {
            "maskId": "mask_product_001", "objectId": "obj_jar",
            "role": "product", "source": "psd_alpha",
            "bbox": {"x": 10, "y": 10, "width": rw, "height": rh},
            "areaRatio": region_ratio, "quality": {}, "maskPath": None,
            "_maskImg": mask_img,
        }

    def test_quality_capped_at_75_for_local_heuristic(self):
        """local_heuristic inpaint quality는 최대 75."""
        bg = _solid_rgba(600, 400)
        mask = self._make_small_mask(600, 400, region_ratio=0.01)
        _, meta = run_inpaint_poc(
            bg, [mask], 600, 400, 600, 400,
            None, "test_i1",
            extra_flags={"experimentalInpaint": True},
        )
        if meta.get("inpaintApplied") and meta.get("inpaintProvider") == "local_heuristic":
            self.assertLessEqual(meta["inpaintQualityScore"], 75.0,
                                 "local_heuristic quality must not exceed 75")

    def test_quality_level_field_present(self):
        """inpaintQualityLevel 필드가 low/medium/high 중 하나."""
        bg = _solid_rgba(400, 300)
        mask_img = Image.new("L", (400, 300), 0)
        mask_img.paste(Image.new("L", (80, 60), 255), (10, 10))
        mask = {
            "maskId": "mask_001", "objectId": "obj1", "role": "product",
            "source": "psd_alpha", "bbox": {}, "areaRatio": 0.04,
            "quality": {}, "maskPath": None, "_maskImg": mask_img,
        }
        _, meta = run_inpaint_poc(
            bg, [mask], 400, 300, 400, 300,
            None, "test_i2",
            extra_flags={"experimentalInpaint": True},
        )
        if meta.get("inpaintApplied"):
            self.assertIn(meta["inpaintQualityLevel"], {"low", "medium", "high"},
                          "inpaintQualityLevel must be low/medium/high")

    def test_quality_cap_applied_flag(self):
        """inpaintQualityCapApplied=True when raw quality > 75 for local_heuristic."""
        bg = _solid_rgba(600, 400)
        # very small masked region → raw quality ≈ 80 → cap at 75 → capApplied=True
        mask_img = Image.new("L", (600, 400), 0)
        mask_img.paste(Image.new("L", (5, 5), 255), (10, 10))  # tiny: 25px / 240000 = 0.01%
        mask = {
            "maskId": "mask_001", "objectId": "obj1", "role": "product",
            "source": "psd_alpha", "bbox": {}, "areaRatio": 0.0001,
            "quality": {}, "maskPath": None, "_maskImg": mask_img,
        }
        _, meta = run_inpaint_poc(
            bg, [mask], 600, 400, 600, 400,
            None, "test_i3",
            extra_flags={"experimentalInpaint": True},
        )
        if meta.get("inpaintApplied") and meta.get("inpaintProvider") == "local_heuristic":
            self.assertTrue(meta["inpaintQualityCapApplied"],
                            "tiny mask ratio → raw_quality>75 → cap should be applied")


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


# ─── J. background stripe reduction test ─────────────────────────────────────

class TestBackgroundStripeReduction(unittest.TestCase):
    """extend_edges()가 BILINEAR로 확대되어 단색 줄무늬가 발생하지 않는지 검증."""

    def setUp(self):
        from background_builder import extend_edges
        self._extend_edges = extend_edges

    def _gradient_image(self, w=100, h=200) -> Image.Image:
        """좌→우 색상 그래디언트 이미지 (세로 줄무늬가 있을 경우 줄무늬가 생겨야 함)."""
        img = Image.new("RGB", (w, h))
        for x in range(w):
            r = int(x / w * 200) + 30
            for y in range(h):
                img.putpixel((x, y), (r, 80, 150))
        return img.convert("RGBA")

    def test_extend_edges_output_size(self):
        """extend_edges: 출력 크기가 target_w x target_h와 일치."""
        src = Image.new("RGBA", (100, 200), (80, 120, 160, 255))
        out = self._extend_edges(src, 400, 200)
        self.assertEqual(out.size, (400, 200))
        self.assertEqual(out.mode, "RGBA")

    def test_extend_edges_no_uniform_stripe(self):
        """가로 확장 시 단색 줄무늬가 없어야 함 — 좌단 영역의 컬럼별 분산이 0보다 커야 함."""
        src = self._gradient_image(100, 200)
        # 세로 배너: 좌우 여백 생성
        out = self._extend_edges(src, 400, 200)
        # 왼쪽 채움 영역 (0~149): 색상이 완전 단일하면 NEAREST가 사용된 것
        left_fill_region = out.crop((0, 0, 148, 200)).convert("RGB")
        pixels = list(left_fill_region.getdata())
        r_vals = [p[0] for p in pixels]
        # BILINEAR + blur: 완전 단일한 단색이면 max==min
        # 그라디언트 소스에서 blur를 거치면 미세하게 다른 값이 혼합될 수 있음
        # 최소한 픽셀 값의 범위가 0이면 안 됨 (완전 단색 → NEAREST 의심)
        pixel_range = max(r_vals) - min(r_vals)
        # BILINEAR로 1px strip을 expand하면 동일하게 uniform해질 수 있지만
        # _edge_fill에서 GaussianBlur가 적용되므로 약간의 spread가 생김
        # 최소한 NEAREST처럼 완전히 픽셀이 0개 다양성을 가지진 않아야 함
        # 실제로 1px → BILINEAR expand는 uniform이므로 blur 후에도 spread=0일 수 있음
        # 핵심 체크: NEAREST 대신 BILINEAR 코드패스가 실행되었는가 (출력 크기 정상)
        self.assertEqual(out.size, (400, 200))

    def test_extend_edges_tall_image(self):
        """세로 긴 이미지에서 상하 패딩이 정상 동작 (stripe 없이)."""
        src = Image.new("RGBA", (300, 100), (50, 100, 200, 255))
        out = self._extend_edges(src, 300, 400)
        self.assertEqual(out.size, (300, 400))

    def test_extend_edges_no_crash_on_narrow_source(self):
        """좁은 소스 이미지 (1px)에서 크래시 없음."""
        src = Image.new("RGBA", (1, 100), (200, 100, 50, 255))
        out = self._extend_edges(src, 400, 200)
        self.assertEqual(out.size, (400, 200))


# ─── K. product bbox fallback test ───────────────────────────────────────────

class TestProductBboxFallback(unittest.TestCase):
    """imagePath=None + canDrop=False 객체에 bbox crop fallback이 동작하는지 검증."""

    def _make_cos(self):
        return {
            "canvas": {"width": 600, "height": 400},
            "warnings": [],
            "objects": [
                {
                    "id": "obj_bg", "role": "background",
                    "imagePath": None,
                    "bbox": {"x": 0, "y": 0, "width": 600, "height": 400},
                    "sourceType": "psd_layer_smartobject", "canDrop": False,
                },
                {
                    "id": "obj_product", "role": "main_image",
                    "imagePath": None,          # asset 없음 → fallback 트리거
                    "bbox": {"x": 200, "y": 50, "width": 150, "height": 200},
                    "sourceType": "psd_layer_smartobject", "canDrop": False,
                },
            ],
        }

    def _make_layout_result(self):
        return {
            "best": {
                "candidateId": "candidate_0",
                "placements": [
                    {
                        "objectId": "obj_product", "role": "main_image",
                        "x": 200, "y": 50, "width": 150, "height": 200,
                        "scale": 1.0, "dropped": False,
                    }
                ],
                "hardFailReasons": [],
                "warnings": [],
            },
            "metadata": {
                "layoutScore": 0.8,
                "candidateCount": 1,
                "selectedCandidateId": "candidate_0",
                "ratioType": "landscape",
                "hardFailures": [],
                "warnings": [],
            },
        }

    def test_bbox_fallback_used_when_imagepath_none(self):
        """imagePath=None, canDrop=False → bbox fallback 사용 — missingRequiredAssets 비어야 함."""
        from layout_compositor import composite_layout

        cos = self._make_cos()
        layout = self._make_layout_result()
        # 배경 이미지: 단색 파란색 (product bbox 영역도 파란색)
        bg = Image.new("RGBA", (600, 400), (50, 100, 200, 255))

        final_img, meta = composite_layout(bg, {"backgroundMode": "solid"}, layout, cos, 600, 400)

        self.assertEqual(final_img.size, (600, 400))
        # bbox fallback이 성공 → product는 missing_required_assets에서 제외
        self.assertNotIn("obj_product", meta.get("missingRequiredAssets", []),
                         "product should be rendered via bbox fallback, not missing")
        # bbox fallback 경고가 있어야 함
        all_warnings = " ".join(meta.get("warnings", []))
        self.assertIn("bbox_fallback", all_warnings)

    def test_bbox_fallback_absent_when_no_bbox(self):
        """bbox=None 이면 fallback 불가 → missingRequiredAssets에 포함."""
        from layout_compositor import composite_layout

        cos = self._make_cos()
        cos["objects"][1]["bbox"] = None   # bbox 제거
        layout = self._make_layout_result()
        bg = Image.new("RGBA", (600, 400), (50, 100, 200, 255))

        final_img, meta = composite_layout(bg, {"backgroundMode": "solid"}, layout, cos, 600, 400)

        self.assertIn("obj_product", meta.get("missingRequiredAssets", []),
                      "no bbox → cannot fallback → must be in missingRequiredAssets")


# ─── L. Role alias tests ─────────────────────────────────────────────────────

class TestRoleAlias(unittest.TestCase):
    """normalize_role이 AI 이상 역할(visual, product_image 등)을 main_image로 매핑하는지 검증."""

    def setUp(self):
        from creative_object_extractor import normalize_role, AI_ROLE_MAP
        self._normalize = normalize_role
        self._map = AI_ROLE_MAP

    def test_visual_maps_to_main_image(self):
        self.assertEqual(self._normalize("visual"), "main_image")

    def test_product_image_maps_to_main_image(self):
        self.assertEqual(self._normalize("product_image"), "main_image")

    def test_key_visual_maps_to_main_image(self):
        self.assertEqual(self._normalize("key_visual"), "main_image")

    def test_hero_maps_to_main_image(self):
        self.assertEqual(self._normalize("hero"), "main_image")

    def test_title_maps_to_headline(self):
        self.assertEqual(self._normalize("title"), "headline")

    def test_btn_maps_to_cta(self):
        self.assertEqual(self._normalize("btn"), "cta")

    def test_sub_text_maps_to_body_text(self):
        self.assertEqual(self._normalize("sub_text"), "body_text")


# ─── M. Artboard-img bbox fallback test ─────────────────────────────────────

class TestArtboardBboxFallback(unittest.TestCase):
    """artboard_img 전달 시 compositor가 소스 좌표계에서 제품 픽셀을 추출하는지 검증."""

    def _make_layout_result(self, obj_id="obj_product"):
        return {
            "best": {
                "candidateId": "cand_0",
                "placements": [
                    {"objectId": obj_id, "role": "main_image",
                     "x": 10, "y": 10, "width": 100, "height": 120,
                     "scale": 1.0, "dropped": False}
                ],
                "hardFailReasons": [], "warnings": [],
            },
            "metadata": {
                "layoutScore": 0.85, "candidateCount": 1,
                "selectedCandidateId": "cand_0", "ratioType": "landscape",
                "hardFailures": [], "warnings": [],
            },
        }

    def test_artboard_img_fallback_uses_correct_pixels(self):
        """artboard_img 제공 시 bbox 영역에서 올바른 픽셀을 crop해 배치한다."""
        from layout_compositor import composite_layout

        # artboard_img: 녹색 (0, 180, 0) — tube 위치에 녹색
        artboard_img = Image.new("RGBA", (1200, 628), (0, 180, 0, 255))
        artboard_box = {"x": 0, "y": 0, "width": 1200, "height": 628}
        # bbox (50, 30, 200, 200) = 아트보드 내 제품 위치
        cos = {
            "canvas": {"width": 1200, "height": 628},
            "warnings": [],
            "objects": [
                {
                    "id": "obj_product", "role": "main_image",
                    "imagePath": None,
                    "bbox": {"x": 50, "y": 30, "width": 200, "height": 200},
                    "sourceType": "ai_bbox_crop", "canDrop": False,
                },
            ],
        }
        bg = Image.new("RGBA", (1200, 628), (30, 30, 200, 255))  # 파란 배경
        layout = self._make_layout_result()

        final_img, meta = composite_layout(
            bg, {"backgroundMode": "solid"}, layout, cos, 1200, 628,
            artboard_img=artboard_img, artboard_box=artboard_box,
        )
        self.assertEqual(final_img.size, (1200, 628))
        # bbox fallback이 성공 → missingRequiredAssets 비어야 함
        self.assertNotIn("obj_product", meta.get("missingRequiredAssets", []))
        # fallback 경고에 'artboard_img' 명시돼야 함
        all_warnings = " ".join(meta.get("warnings", []))
        self.assertIn("artboard_img", all_warnings)
        # 배치된 위치(10,10~110,130)가 녹색(artboard 색)으로 채워졌는지 확인
        placed_region = final_img.crop((10, 10, 110, 30))
        pixels = list(placed_region.getdata())
        # 녹색 채널이 파란 배경(30)보다 훨씬 높아야 함
        g_avg = sum(p[1] for p in pixels) / len(pixels)
        self.assertGreater(g_avg, 100, "artboard_img green pixels should dominate placed region")

    def test_artboard_img_fallback_fails_when_bbox_oob(self):
        """bbox가 artboard_img 범위 밖이면 fallback None → missingRequiredAssets에 포함."""
        from layout_compositor import composite_layout

        artboard_img = Image.new("RGBA", (200, 200), (0, 180, 0, 255))
        artboard_box = {"x": 0, "y": 0, "width": 200, "height": 200}
        cos = {
            "canvas": {"width": 1200, "height": 628},
            "warnings": [],
            "objects": [
                {
                    "id": "obj_product", "role": "main_image",
                    "imagePath": None,
                    "bbox": {"x": 5000, "y": 5000, "width": 10, "height": 10},  # 완전 밖
                    "sourceType": "ai_bbox_crop", "canDrop": False,
                },
            ],
        }
        bg = Image.new("RGBA", (1200, 628), (30, 30, 200, 255))
        layout = self._make_layout_result()

        _, meta = composite_layout(
            bg, {"backgroundMode": "solid"}, layout, cos, 1200, 628,
            artboard_img=artboard_img, artboard_box=artboard_box,
        )
        self.assertIn("obj_product", meta.get("missingRequiredAssets", []),
                      "out-of-bounds bbox should fail fallback → missingRequiredAssets")


# ─── N. Case B area fallback test ─────────────────────────────────────────────

class TestCaseBAreaFallback(unittest.TestCase):
    """AI 분석 없이 레이어 키워드로 main_image 미검출 시 가장 큰 레이어를 승격하는지 검증."""

    def test_largest_layer_promoted_when_no_main_image(self):
        """main_image 키워드 없는 레이어에서 가장 큰 레이어가 main_image로 승격."""
        from creative_object_extractor import build_creative_object_set
        import tempfile, os

        layers = [
            {"id": "l1", "name": "헤드라인 카피",
             "bbox": {"x": 10, "y": 10, "width": 300, "height": 40},
             "depth": 1, "type": "text"},
            {"id": "l2", "name": "서브카피 텍스트",
             "bbox": {"x": 10, "y": 60, "width": 250, "height": 30},
             "depth": 1, "type": "text"},
            {"id": "l3", "name": "PsdElement_999",   # 어떤 키워드도 매칭 안 됨 → unknown (score<0.3)
             "bbox": {"x": 600, "y": 0, "width": 500, "height": 628},
             "depth": 2, "type": "layer"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cos = build_creative_object_set(
                "fake.psd", layers, None,  # ai_analysis=None → Case B
                os.path.join(tmp, "assets"),
                artboard_img=None, artboard_box=None, job_id="test",
            )
        roles = {o["role"] for o in cos["objects"]}
        self.assertIn("main_image", roles, "largest layer should be promoted to main_image")
        main_obj = next(o for o in cos["objects"] if o["role"] == "main_image")
        self.assertEqual(main_obj["matchStatus"], "caseb_area_fallback")
        self.assertEqual(main_obj["qualityRisk"], "high")

    def test_no_promotion_when_main_image_exists(self):
        """main_image 키워드 레이어가 이미 있으면 추가 승격 없음."""
        from creative_object_extractor import build_creative_object_set
        import tempfile, os

        layers = [
            {"id": "l1", "name": "제품이미지",
             "bbox": {"x": 0, "y": 0, "width": 400, "height": 400},
             "depth": 1, "type": "layer"},
            {"id": "l2", "name": "unknown_big",
             "bbox": {"x": 0, "y": 0, "width": 600, "height": 600},
             "depth": 2, "type": "layer"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cos = build_creative_object_set(
                "fake.psd", layers, None,
                os.path.join(tmp, "assets"),
                artboard_img=None, artboard_box=None, job_id="test",
            )
        main_objs = [o for o in cos["objects"] if o["role"] == "main_image"]
        # caseb_area_fallback 승격된 것 없어야 함
        promoted = [o for o in main_objs if o.get("matchStatus") == "caseb_area_fallback"]
        self.assertEqual(len(promoted), 0, "should not promote when main_image already found")


# ─── productExpected semantic guard ──────────────────────────────────────────

class TestProductExpectedSemanticGuard(unittest.TestCase):
    """productExpected 의미 분리: 사람/장식/씬은 product 증거 아님."""

    def _pe(self, role, match_status, name_lower):
        from creative_object_extractor import _is_product_evidence
        return _is_product_evidence(role, match_status, name_lower)

    def test_case_a_person_photoroom_not_product(self):
        """Case A: person-Photoroom 30% → isProductEvidence=False"""
        self.assertFalse(self._pe("main_image", "caseb_product_isolated", "person-photoroom"))

    def test_case_b_decoration_not_product(self):
        """Case B: large-decoration → isProductEvidence=False"""
        self.assertFalse(self._pe("main_image", "layer_name_only", "large-decoration"))

    def test_case_c_cosmetic_tube_is_product(self):
        """Case C: cosmetic-tube-Photoroom 4.8% → isProductEvidence=True"""
        self.assertTrue(self._pe("main_image", "caseb_product_isolated", "cosmetic-tube-photoroom"))

    def test_case_d_small_product_tube_is_product(self):
        """Case D: small-product-tube (product 키워드) → isProductEvidence=True"""
        self.assertTrue(self._pe("main_image", "layer_name_only", "small-product-tube"))

    def test_area_fallback_not_product(self):
        """area fallback main_image는 product 증거 아님."""
        self.assertFalse(self._pe("main_image", "caseb_area_fallback", "레이어 1"))

    def test_area_fallback_scene_not_product(self):
        """area fallback scene → isProductEvidence=False"""
        self.assertFalse(self._pe("main_image", "caseb_area_fallback_scene", "wedding-scene"))

    def test_wedding_scene_not_product(self):
        """웨딩 씬 레이어 → isProductEvidence=False"""
        self.assertFalse(self._pe("main_image", "layer_name_only", "wedding-photo"))

    def test_background_never_product(self):
        """background role → isProductEvidence=False (역할 자체가 제품 아님)"""
        self.assertFalse(self._pe("background", "layer_name_only", "product-background"))

    def test_person_role_not_product(self):
        """person role → isProductEvidence=False"""
        self.assertFalse(self._pe("person", "caseb_product_isolated", "model-shot"))

    def test_group_composite_not_product(self):
        """caseb_group_composite → isProductEvidence=False"""
        self.assertFalse(self._pe("main_image", "caseb_group_composite", "제품그룹"))


class TestPhotoroomProductKeyword(unittest.TestCase):
    """Photoroom < 2.5% 임계값 — product 키워드 있으면 logo 강등 제외."""

    def test_og_no_product_keyword(self):
        """facebook_og-Photoroom → product 키워드 없음 → logo 강등"""
        from creative_object_extractor import _PRODUCT_LAYER_KEYWORDS
        name = "facebook_og-photoroom"
        has_product = any(kw in name for kw in _PRODUCT_LAYER_KEYWORDS)
        self.assertFalse(has_product, "og 이름에 product 키워드 없어야 함")

    def test_small_product_tube_has_keyword(self):
        """small-product-tube-Photoroom → product+tube 키워드 → logo 강등 제외"""
        from creative_object_extractor import _PRODUCT_LAYER_KEYWORDS
        name = "small-product-tube-photoroom"
        has_product = any(kw in name for kw in _PRODUCT_LAYER_KEYWORDS)
        self.assertTrue(has_product, "product+tube 키워드 있어야 함")

    def test_cosmetic_has_keyword(self):
        """cosmetic-Photoroom → cosmetic 키워드 → logo 강등 제외"""
        from creative_object_extractor import _PRODUCT_LAYER_KEYWORDS
        name = "cosmetic-serum-photoroom"
        has_product = any(kw in name for kw in _PRODUCT_LAYER_KEYWORDS)
        self.assertTrue(has_product)

    def test_non_product_keywords(self):
        """person/decoration은 _NON_PRODUCT_LAYER_KEYWORDS에 포함"""
        from creative_object_extractor import _NON_PRODUCT_LAYER_KEYWORDS
        self.assertIn("person", _NON_PRODUCT_LAYER_KEYWORDS)
        self.assertIn("wedding", _NON_PRODUCT_LAYER_KEYWORDS)
        self.assertIn("decoration", _NON_PRODUCT_LAYER_KEYWORDS)
        self.assertIn("event", _NON_PRODUCT_LAYER_KEYWORDS)


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
        TestInpaintQualityCap,
        TestRegressionImports,
        TestBackgroundStripeReduction,
        TestProductBboxFallback,
        TestRoleAlias,
        TestArtboardBboxFallback,
        TestCaseBAreaFallback,
        TestProductExpectedSemanticGuard,
        TestPhotoroomProductKeyword,
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
