"""mask_quality.py 단위 테스트."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mask_quality import score_external_mask, score_native_mask


def _make_mask(w: int, h: int, fill: bool = True):
    """테스트용 PIL L-mode mask 생성."""
    from PIL import Image
    mask = Image.new("L", (w, h), 255 if fill else 0)
    if fill:
        # 가운데 영역만 채움 (product-like 형태)
        inner_w, inner_h = w // 3, h // 2
        patch = Image.new("L", (inner_w, inner_h), 255)
        bg = Image.new("L", (w, h), 0)
        bg.paste(patch, ((w - inner_w) // 2, (h - inner_h) // 2))
        return bg
    return mask


CANVAS_W, CANVAS_H = 1200, 628


# ── hard fail 테스트 ────────────────────────────────────────────────────────────

def test_empty_mask_hard_fail():
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=False)
    res = score_external_mask(
        detection_confidence=0.9,
        mask_confidence=0.9,
        mask_pil=mask,
        bbox={"x": 100, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    assert res["hardFail"] is True, f"expected hardFail: {res}"
    assert res["hardFailReason"] == "empty_mask"
    assert res["overallMaskScore"] == 0.0


def test_low_confidence_hard_fail():
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res = score_external_mask(
        detection_confidence=0.05,  # 아래 MIN_CONFIDENCE
        mask_confidence=0.9,
        mask_pil=mask,
        bbox={"x": 100, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    assert res["hardFail"] is True
    assert "confidence" in res["hardFailReason"]


def test_canvas_fill_hard_fail():
    from PIL import Image
    full_mask = Image.new("L", (CANVAS_W, CANVAS_H), 255)  # 100% 채움
    res = score_external_mask(
        detection_confidence=0.9,
        mask_confidence=0.9,
        mask_pil=full_mask,
        bbox={"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    assert res["hardFail"] is True
    assert "large" in res["hardFailReason"]


def test_invalid_bbox_hard_fail():
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res = score_external_mask(
        detection_confidence=0.9,
        mask_confidence=0.9,
        mask_pil=mask,
        bbox={"x": 0, "y": 0, "width": 0, "height": 0},  # width=0
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    assert res["hardFail"] is True
    assert "bbox" in res["hardFailReason"]


# ── 정상 score 테스트 ──────────────────────────────────────────────────────────

def test_good_detection_score_positive():
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res = score_external_mask(
        detection_confidence=0.92,
        mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    assert not res["hardFail"], f"unexpected hardFail: {res}"
    assert res["overallMaskScore"] > 0, "score should be positive"
    print(f"good_detection score={res['overallMaskScore']:.2f}")


def test_native_mask_score_psd_isolated():
    score = score_native_mask(
        source="psd_isolated_layer",
        bbox={"x": 100, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        product_score=80.0,
    )
    assert score >= 90.0, f"psd_isolated score should be >=90, got {score}"


def test_native_mask_score_bbox_coarse_low():
    score = score_native_mask(
        source="object_bbox_coarse",
        bbox={"x": 100, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        product_score=50.0,
    )
    assert score < 60.0, f"bbox_coarse score should be <60, got {score}"


if __name__ == "__main__":
    test_empty_mask_hard_fail()
    test_low_confidence_hard_fail()
    test_canvas_fill_hard_fail()
    test_invalid_bbox_hard_fail()
    test_good_detection_score_positive()
    test_native_mask_score_psd_isolated()
    test_native_mask_score_bbox_coarse_low()
    print("ALL mask_quality tests PASS")
