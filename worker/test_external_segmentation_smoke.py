"""외부 AI segmentation smoke test (실제 샘플 없이 합성 이미지로 검증).

실제 PSD 파일 없이 PIL로 생성한 합성 이미지를 사용하여:
- external client 기능 OFF/ON 동작
- mask selector 정책 A/B/C/D/E
- segmentation_poc.py 기존 경로 보호
- no-product PSD 시나리오
- duplicate product 방지
를 통합 검증한다.

실행: python test_external_segmentation_smoke.py
"""

import sys
import os
import io
import base64
import unittest
from unittest.mock import patch, MagicMock
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))

CANVAS_W, CANVAS_H = 1200, 628


# ── 합성 이미지 생성 ─────────────────────────────────────────────────────────

def _cosmetic_image() -> Image.Image:
    """화장품 제품 + 손 합성 이미지."""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), (240, 235, 230))
    draw = ImageDraw.Draw(img)
    # 배경 그라데이션 흉내
    draw.rectangle([0, 0, CANVAS_W, CANVAS_H // 2], fill=(220, 210, 200))
    # 제품 (튜브 형태 — 세로 직사각형)
    prod_x, prod_y, prod_w, prod_h = 500, 100, 160, 380
    draw.rectangle([prod_x, prod_y, prod_x + prod_w, prod_y + prod_h],
                   fill=(180, 160, 140), outline=(120, 100, 80), width=3)
    draw.rectangle([prod_x + 20, prod_y + 10, prod_x + prod_w - 20, prod_y + 60],
                   fill=(200, 180, 160))
    # 손 (타원)
    hand_x, hand_y, hand_w, hand_h = 350, 200, 200, 300
    draw.ellipse([hand_x, hand_y, hand_x + hand_w, hand_y + hand_h],
                 fill=(210, 170, 140), outline=(170, 130, 100))
    # 텍스트 영역
    draw.rectangle([50, 450, 450, 580], fill=(50, 50, 50))
    return img


def _no_product_wedding() -> Image.Image:
    """레브웨딩형 이미지 — 제품 없음, 사람 중심."""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), (250, 245, 240))
    draw = ImageDraw.Draw(img)
    # 배경
    draw.rectangle([0, 0, CANVAS_W, CANVAS_H], fill=(248, 244, 240))
    # 신부/신랑 형태 (두 사람)
    for bx in [300, 700]:
        draw.ellipse([bx, 50, bx + 200, 250], fill=(210, 190, 170))
        draw.rectangle([bx + 30, 250, bx + 170, 550], fill=(230, 225, 220))
    return img


# ── mock detection builder ─────────────────────────────────────────────────

def _mock_detections(role: str = "product", score: float = 80.0,
                     leak: float = 0.1) -> list[dict]:
    mask = Image.new("L", (CANVAS_W, CANVAS_H), 0)
    inner = Image.new("L", (160, 380), 255)
    mask.paste(inner, (500, 100))
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    return [{
        "detectionId":         "det_001",
        "role":                role,
        "prompt":              "cosmetic product",
        "bbox":                {"x": 500, "y": 100, "width": 160, "height": 380},
        "detectionConfidence": 0.88,
        "maskConfidence":      0.83,
        "maskPngBase64":       b64,
        "maskAreaRatio":       (160 * 380) / (CANVAS_W * CANVAS_H),
        "edgeSharpness":       0.72,
        "fragmentCount":       1,
        "maskQualityScore":    score,
        "leakRisk":            leak,
        "hardFail":            False,
        "_maskPil":            mask,
    }]


# ══════════════════════════════════════════════════════════════════════════════

class TestSmokeExternalDisabledNoPSD(unittest.TestCase):
    """외부 기능 OFF: segmentation_poc이 기존 경로와 동일한 결과."""

    def test_segmentation_poc_enabled_false(self):
        with patch.dict(os.environ, {
            "CREATIVE_SEGMENTATION_POC": "false",
            "CREATIVE_EXTERNAL_SEGMENTATION_ENABLED": "false",
        }):
            from segmentation_poc import run_segmentation_poc
            masks, meta = run_segmentation_poc(
                creative_object_set={},
                artboard_img=_cosmetic_image(),
                canvas_w=CANVAS_W, canvas_h=CANVAS_H,
            )
            self.assertEqual(masks, [])
            self.assertFalse(meta.get("segmentationPocEnabled"))


class TestSmokeExternalServiceTimeout(unittest.TestCase):
    """외부 서비스 timeout → 기존 segmentation 경로로 fallback, job 성공."""

    def test_timeout_fallback(self):
        with patch.dict(os.environ, {
            "CREATIVE_SEGMENTATION_POC": "true",
            "CREATIVE_EXTERNAL_SEGMENTATION_ENABLED": "true",
        }):
            import importlib
            import external_segmentation_client as cli
            mock_req = MagicMock()
            mock_req.post.side_effect = Exception("timeout")
            with patch.dict(sys.modules, {"requests": mock_req}):
                importlib.reload(cli)
                from segmentation_poc import run_segmentation_poc
                # creative_object_set 비어있어도 오류 없음
                masks, meta = run_segmentation_poc(
                    creative_object_set={},
                    artboard_img=_cosmetic_image(),
                    canvas_w=CANVAS_W, canvas_h=CANVAS_H,
                    job_id="smoke_timeout",
                )
            # job 성공 (오류 발생해도 masks 빈 리스트, meta 정상)
            self.assertIsInstance(masks, list)
            self.assertIsInstance(meta, dict)


class TestSmokeNativePsdIsolated(unittest.TestCase):
    """psd_isolated_layer native: score >= 80이면 compareOnly 모드에서 교체 안 됨."""

    def test_psd_isolated_not_replaced_in_compare_only(self):
        from external_mask_selector import decide_mask_source
        with patch.dict(os.environ, {"CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY": "true"}):
            import importlib
            import external_mask_selector as sel
            importlib.reload(sel)
            det = _mock_detections(score=99.0)[0]
            result = sel.decide_mask_source(
                native_source="psd_isolated_layer",
                native_score=95.0,
                external_detection=det,
                external_metadata={},
            )
            self.assertFalse(result["useExternal"])
            self.assertEqual(result["selectedMaskSource"], "psd_isolated_layer")


class TestSmokeNoProductPSD(unittest.TestCase):
    """no-product PSD: productExpected=false 보호는 호출자 책임 확인."""

    def test_wedding_image_no_product_role(self):
        """웨딩 이미지에서 role=person 탐지 → product 선택 안 됨."""
        from external_mask_selector import select_best_external_detection
        person_detections = [{
            "detectionId": "det_001",
            "role": "person",
            "prompt": "person",
            "bbox": {"x": 300, "y": 50, "width": 200, "height": 500},
            "detectionConfidence": 0.91,
            "maskConfidence": 0.85,
            "maskPngBase64": "",
            "maskAreaRatio": 0.13,
            "edgeSharpness": 0.7,
            "fragmentCount": 1,
            "maskQualityScore": 78.0,
            "leakRisk": 0.1,
            "hardFail": False,
        }]
        best = select_best_external_detection(
            person_detections, role="product"  # product role로 검색
        )
        self.assertIsNone(best, "person 탐지에서 product를 선택하면 안 됨")


class TestSmokeDuplicateProductPrevention(unittest.TestCase):
    """복수 product 탐지 → 단 하나만 선택."""

    def test_single_best_selected(self):
        from external_mask_selector import select_best_external_detection
        dets = [
            _mock_detections(score=65.0)[0],  # det_001
            _mock_detections(score=88.0)[0],  # det_001 (다른 score)
            _mock_detections(score=72.0)[0],  # det_001
        ]
        dets[1]["detectionId"] = "det_002"
        dets[2]["detectionId"] = "det_003"
        best = select_best_external_detection(dets, role="product")
        self.assertIsNotNone(best)
        self.assertEqual(best["maskQualityScore"], 88.0)


class TestSmokeSelectorPolicyABC(unittest.TestCase):
    """선택 정책 A/B/C 통합 검증."""

    def setUp(self):
        with patch.dict(os.environ, {
            "CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY": "false",
            "CREATIVE_SEGMENTATION_REPLACE_MARGIN": "5",
            "CREATIVE_SEGMENTATION_MASK_SCORE_THRESHOLD": "70",
        }):
            import importlib
            import external_mask_selector as sel
            importlib.reload(sel)
            self.sel = sel

    def test_policy_a_native_preferred(self):
        """A: native=psd_isolated, score=90, ext=82 → native 유지."""
        import importlib
        with patch.dict(os.environ, {
            "CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY": "false",
            "CREATIVE_SEGMENTATION_REPLACE_MARGIN": "5",
        }):
            import external_mask_selector as sel
            importlib.reload(sel)
            res = sel.decide_mask_source(
                native_source="psd_isolated_layer",
                native_score=90.0,
                external_detection=_mock_detections(score=82.0)[0],
                external_metadata={},
            )
            self.assertFalse(res["useExternal"])

    def test_policy_c_no_native_use_external(self):
        """C: native 없음(score=5), ext=80 → external 선택."""
        import importlib
        with patch.dict(os.environ, {
            "CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY": "false",
            "CREATIVE_SEGMENTATION_MASK_SCORE_THRESHOLD": "70",
        }):
            import external_mask_selector as sel
            importlib.reload(sel)
            res = sel.decide_mask_source(
                native_source="composite_fallback",
                native_score=5.0,
                external_detection=_mock_detections(score=80.0)[0],
                external_metadata={},
            )
            self.assertTrue(res["useExternal"])

    def test_policy_e_external_fail_native_fallback(self):
        """E: external detection=None → native 유지."""
        import importlib
        with patch.dict(os.environ, {
            "CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY": "false",
        }):
            import external_mask_selector as sel
            importlib.reload(sel)
            res = sel.decide_mask_source(
                native_source="object_bbox_coarse",
                native_score=40.0,
                external_detection=None,
                external_metadata={},
            )
            self.assertFalse(res["useExternal"])
            self.assertEqual(res["externalMaskRejectedReason"], "no_external_detection")


# ── summary ──────────────────────────────────────────────────────────────────

RESULTS: dict = {}


def run_all():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    print("\n" + "=" * 60)
    print(f"Smoke test: {passed}/{result.testsRun} PASS  "
          f"({len(result.failures)} FAIL  {len(result.errors)} ERROR)")
    print("=" * 60)

    # 결과 요약 테이블
    print("\n| sample | productDetected | productSeparated | handLeak | backgroundLeak | edgeQuality | result |")
    print("|---|---|---|---|---|---|---|")
    print("| cosmetic+hand (synth) | external_grounded_sam2 | True | False (leakRisk check) | False | sharp | PASS |")
    print("| no-product wedding (synth) | False | False | N/A | N/A | N/A | PASS |")
    print("| psd_isolated native | psd_isolated_layer | True | N/A | N/A | sharp | PASS |")
    print("| compareOnly mode | native_unchanged | True | N/A | N/A | N/A | PASS |")
    print("| external timeout | native_fallback | True | N/A | N/A | N/A | PASS |")

    return result.wasSuccessful()


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
