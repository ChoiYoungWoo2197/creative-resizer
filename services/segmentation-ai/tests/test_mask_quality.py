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


# ── Stage 18.1: overlap penalty 테스트 ────────────────────────────────────────

def test_no_overlap_no_penalty():
    """overlap=0일 때 hand/person 감점 없음 — 기존 테스트와 점수 동일해야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    base_kwargs = dict(
        detection_confidence=0.92,
        mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    res_no_overlap = score_external_mask(**base_kwargs)
    res_zero_overlap = score_external_mask(
        **base_kwargs,
        hand_overlap_ratio=0.0,
        person_overlap_ratio=0.0,
    )
    assert res_no_overlap["overallMaskScore"] == res_zero_overlap["overallMaskScore"], (
        "zero overlap should not change score"
    )
    assert res_zero_overlap["scoreBreakdown"]["handLeakPenalty"] == 0.0
    assert res_zero_overlap["scoreBreakdown"]["personLeakPenalty"] == 0.0


def test_hand_overlap_soft_penalty():
    """hand overlap 5~10%: 약한 감점 (0 < penalty < 8)."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res_clean = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    res_hand = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
        hand_overlap_ratio=0.07,  # 5~10% 구간
    )
    penalty = res_clean["overallMaskScore"] - res_hand["overallMaskScore"]
    assert penalty > 0, "soft hand overlap should reduce score"
    assert penalty < 8.0, f"soft penalty should be < 8, got {penalty:.2f}"
    bd = res_hand["scoreBreakdown"]
    assert bd["handLeakPenalty"] < 0, "handLeakPenalty should be negative in breakdown"


def test_hand_overlap_hard_penalty():
    """hand overlap ≥10%: 강한 감점 (≥8점 이상 차이)."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res_clean = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    res_hand = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
        hand_overlap_ratio=0.27,  # 10% 이상 → 강한 감점
    )
    penalty = res_clean["overallMaskScore"] - res_hand["overallMaskScore"]
    assert penalty >= 8.0, f"hard hand overlap penalty should be >=8, got {penalty:.2f}"


def test_person_overlap_penalty():
    """person overlap ≥10%: 감점이 발생해야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res_clean = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    res_person = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
        person_overlap_ratio=0.30,
    )
    penalty = res_clean["overallMaskScore"] - res_person["overallMaskScore"]
    assert penalty > 0, "person overlap should reduce score"
    bd = res_person["scoreBreakdown"]
    assert bd["personLeakPenalty"] < 0, "personLeakPenalty should be negative in breakdown"


def test_combined_overlap_penalty_cumulative():
    """hand + person 동시 overlap → 각각보다 감점이 커야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    kwargs = dict(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    res_clean  = score_external_mask(**kwargs)
    res_hand   = score_external_mask(**kwargs, hand_overlap_ratio=0.20)
    res_person = score_external_mask(**kwargs, person_overlap_ratio=0.20)
    res_both   = score_external_mask(**kwargs, hand_overlap_ratio=0.20, person_overlap_ratio=0.20)

    penalty_hand   = res_clean["overallMaskScore"] - res_hand["overallMaskScore"]
    penalty_person = res_clean["overallMaskScore"] - res_person["overallMaskScore"]
    penalty_both   = res_clean["overallMaskScore"] - res_both["overallMaskScore"]

    assert penalty_both > penalty_hand, "combined penalty should exceed hand-only"
    assert penalty_both > penalty_person, "combined penalty should exceed person-only"


def test_score_breakdown_fields():
    """scoreBreakdown에 필수 키가 모두 있어야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res = score_external_mask(
        detection_confidence=0.80, mask_confidence=0.75,
        mask_pil=mask,
        bbox={"x": 300, "y": 100, "width": 400, "height": 350},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
        hand_overlap_ratio=0.05,
        person_overlap_ratio=0.03,
    )
    required_keys = {
        "confidenceScore", "maskConfScore", "edgeScore", "fragmentScore",
        "bboxFillScore", "aspectRatioScore", "nativeBboxScore", "roleConsistencyScore",
        "areaLeakPenalty", "bboxFillPenalty", "fragmentPenalty", "edgeClipPenalty",
        "handLeakPenalty", "personLeakPenalty", "totalScore",
    }
    missing = required_keys - set(res["scoreBreakdown"].keys())
    assert not missing, f"scoreBreakdown missing keys: {missing}"
    assert res["scoreBreakdown"]["totalScore"] == res["overallMaskScore"]


def test_score_breakdown_in_hard_fail():
    """hard fail 시에도 scoreBreakdown이 반환돼야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=False)
    res = score_external_mask(
        detection_confidence=0.9, mask_confidence=0.9,
        mask_pil=mask,
        bbox={"x": 100, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    assert res["hardFail"] is True
    assert "scoreBreakdown" in res, "scoreBreakdown must exist even on hard fail"
    assert res["scoreBreakdown"]["totalScore"] == 0.0


def test_overlap_max_clamped():
    """overlap=1.0 (완전 겹침)일 때 점수는 0 이상이어야 한다 (clamp)."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
        hand_overlap_ratio=1.0,
        person_overlap_ratio=1.0,
    )
    assert res["overallMaskScore"] >= 0.0, "score must not go negative"


if __name__ == "__main__":
    test_empty_mask_hard_fail()
    test_low_confidence_hard_fail()
    test_canvas_fill_hard_fail()
    test_invalid_bbox_hard_fail()
    test_good_detection_score_positive()
    test_native_mask_score_psd_isolated()
    test_native_mask_score_bbox_coarse_low()
    # Stage 18.1 overlap penalty
    test_no_overlap_no_penalty()
    test_hand_overlap_soft_penalty()
    test_hand_overlap_hard_penalty()
    test_person_overlap_penalty()
    test_combined_overlap_penalty_cumulative()
    test_score_breakdown_fields()
    test_score_breakdown_in_hard_fail()
    test_overlap_max_clamped()
    print("ALL mask_quality tests PASS")
