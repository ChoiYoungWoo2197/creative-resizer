"""mask_quality.py 단위 테스트.

Stage 18.1: hand/person overlap penalty
Stage 18.2: edge 경계 정규화 메트릭 + completeness 점수
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mask_quality import score_external_mask, score_native_mask


def _make_mask(w: int, h: int, fill: bool = True):
    """테스트용 PIL L-mode mask 생성."""
    from PIL import Image
    if not fill:
        return Image.new("L", (w, h), 0)
    # 가운데 영역만 채움 (product-like 형태)
    inner_w, inner_h = w // 3, h // 2
    patch = Image.new("L", (inner_w, inner_h), 255)
    bg = Image.new("L", (w, h), 0)
    bg.paste(patch, ((w - inner_w) // 2, (h - inner_h) // 2))
    return bg


def _make_binary_mask(w: int, h: int, x: int, y: int, bw: int, bh: int):
    """완전한 이진 마스크 — 경계가 명확한 직사각형."""
    from PIL import Image
    mask = Image.new("L", (w, h), 0)
    patch = Image.new("L", (bw, bh), 255)
    mask.paste(patch, (x, y))
    return mask


def _make_top_clipped_mask(w: int, h: int):
    """상단이 잘린 마스크 (bbox 기준 상단 30% 비어 있음)."""
    from PIL import Image
    mask = Image.new("L", (w, h), 0)
    # 하단 70%만 채움
    fill_y = int(h * 0.30)
    patch = Image.new("L", (w, h - fill_y), 255)
    mask.paste(patch, (0, fill_y))
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
        detection_confidence=0.05,
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
    full_mask = Image.new("L", (CANVAS_W, CANVAS_H), 255)
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
        bbox={"x": 0, "y": 0, "width": 0, "height": 0},
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
    """overlap=0일 때 hand/person 감점 없음."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    base_kwargs = dict(
        detection_confidence=0.92,
        mask_confidence=0.88,
        mask_pil=mask,
        bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        role="product",
    )
    res_no_overlap   = score_external_mask(**base_kwargs)
    res_zero_overlap = score_external_mask(**base_kwargs, hand_overlap_ratio=0.0, person_overlap_ratio=0.0)
    assert res_no_overlap["overallMaskScore"] == res_zero_overlap["overallMaskScore"]
    assert res_zero_overlap["scoreBreakdown"]["handLeakPenalty"] == 0.0
    assert res_zero_overlap["scoreBreakdown"]["personLeakPenalty"] == 0.0


def test_hand_overlap_soft_penalty():
    """hand overlap 5~10%: 약한 감점 (0 < penalty < 8)."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res_clean = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask, bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    res_hand = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask, bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
        hand_overlap_ratio=0.07,
    )
    penalty = res_clean["overallMaskScore"] - res_hand["overallMaskScore"]
    assert penalty > 0, "soft hand overlap should reduce score"
    assert penalty < 8.0, f"soft penalty should be < 8, got {penalty:.2f}"
    bd = res_hand["scoreBreakdown"]
    assert bd["handLeakPenalty"] < 0, "handLeakPenalty should be negative in breakdown"


def test_hand_overlap_hard_penalty():
    """hand overlap ≥10%: 강한 감점 (≥8점)."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res_clean = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask, bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    res_hand = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask, bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
        hand_overlap_ratio=0.27,
    )
    penalty = res_clean["overallMaskScore"] - res_hand["overallMaskScore"]
    assert penalty >= 8.0, f"hard hand overlap penalty should be >=8, got {penalty:.2f}"


def test_person_overlap_penalty():
    """person overlap ≥10%: 감점이 발생해야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res_clean = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask, bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    res_person = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask, bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
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
        mask_pil=mask, bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    res_clean  = score_external_mask(**kwargs)
    res_hand   = score_external_mask(**kwargs, hand_overlap_ratio=0.20)
    res_person = score_external_mask(**kwargs, person_overlap_ratio=0.20)
    res_both   = score_external_mask(**kwargs, hand_overlap_ratio=0.20, person_overlap_ratio=0.20)

    penalty_hand   = res_clean["overallMaskScore"] - res_hand["overallMaskScore"]
    penalty_person = res_clean["overallMaskScore"] - res_person["overallMaskScore"]
    penalty_both   = res_clean["overallMaskScore"] - res_both["overallMaskScore"]

    assert penalty_both > penalty_hand,   "combined penalty should exceed hand-only"
    assert penalty_both > penalty_person, "combined penalty should exceed person-only"


def test_score_breakdown_fields():
    """scoreBreakdown에 필수 키가 모두 있어야 한다 (Stage 18.2 키 이름 포함)."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res = score_external_mask(
        detection_confidence=0.80, mask_confidence=0.75,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 350},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
        hand_overlap_ratio=0.05, person_overlap_ratio=0.03,
    )
    required_keys = {
        "confidenceScore", "maskConfidenceScore", "edgeAlignmentScore",
        "completenessScore", "topBottomCoverageScore",
        "fragmentScore", "bboxFillScore", "aspectRatioScore",
        "nativeBboxScore", "roleConsistencyScore",
        "areaLeakPenalty", "bboxFillPenalty", "fragmentPenalty", "edgeClipPenalty",
        "handLeakPenalty", "personLeakPenalty", "clippingPenalty", "totalScore",
    }
    missing = required_keys - set(res["scoreBreakdown"].keys())
    assert not missing, f"scoreBreakdown missing keys: {missing}"
    assert res["scoreBreakdown"]["totalScore"] == res["overallMaskScore"]


def test_score_breakdown_in_hard_fail():
    """hard fail 시에도 scoreBreakdown이 반환돼야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=False)
    res = score_external_mask(
        detection_confidence=0.9, mask_confidence=0.9,
        mask_pil=mask, bbox={"x": 100, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    assert res["hardFail"] is True
    assert "scoreBreakdown" in res, "scoreBreakdown must exist even on hard fail"
    assert res["scoreBreakdown"]["totalScore"] == 0.0


def test_overlap_max_clamped():
    """overlap=1.0일 때 점수는 0 이상이어야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.88,
        mask_pil=mask, bbox={"x": 400, "y": 100, "width": 300, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
        hand_overlap_ratio=1.0, person_overlap_ratio=1.0,
    )
    assert res["overallMaskScore"] >= 0.0, "score must not go negative"


# ── Stage 18.2: edge 경계 정규화 메트릭 테스트 ─────────────────────────────────

def test_edge_boundary_metric_binary_mask_high_sharpness():
    """완전한 이진 직사각형 마스크 → edgeSharpness가 0.5 이상이어야 한다."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    assert res["edgeSharpness"] >= 0.5, (
        f"binary mask should have high edgeSharpness, got {res['edgeSharpness']:.4f}"
    )


def test_edge_boundary_metric_old_std_comparison():
    """새 메트릭이 소형 제품에서도 0 이상의 sharpness를 반환해야 한다.

    (구 arr.std()/64.0는 소형 마스크에서 0.197로 매우 낮았음)
    """
    # 캔버스 대비 0.3% 면적 소형 마스크
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 580, 200, 60, 100)
    res = score_external_mask(
        detection_confidence=0.85, mask_confidence=0.80,
        mask_pil=mask, bbox={"x": 580, "y": 200, "width": 60, "height": 100},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    # 구 메트릭은 0.197, 새 메트릭은 0.5 이상이어야 함
    assert res["edgeSharpness"] >= 0.50, (
        f"small product should still get good edgeSharpness, got {res['edgeSharpness']:.4f}"
    )


def test_edge_pixel_count_positive_for_binary_mask():
    """이진 마스크에서 edgePixelCount > 0이어야 한다."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    assert "edgePixelCount" in res, "edgePixelCount must be in result"
    assert res["edgePixelCount"] > 0, f"binary mask must have edge pixels, got {res['edgePixelCount']}"


def test_raw_boundary_gradient_in_result():
    """rawBoundaryGradient 필드가 결과에 포함되어야 한다."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    assert "rawBoundaryGradient" in res
    assert res["rawBoundaryGradient"] > 0.0, "boundary gradient should be positive for binary mask"


# ── Stage 18.2: completeness 테스트 ────────────────────────────────────────────

def test_completeness_metrics_in_result():
    """completenessMetrics 필드가 product 역할에서 반환되어야 한다."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    assert "completenessMetrics" in res
    cm = res["completenessMetrics"]
    required = {"topCoverage", "bottomCoverage", "verticalContinuity",
                "productTopClipped", "productBottomClipped"}
    missing = required - set(cm.keys())
    assert not missing, f"completenessMetrics missing: {missing}"


def test_completeness_full_product_high_coverage():
    """bbox를 꽉 채운 마스크 → topCoverage / bottomCoverage / continuity 모두 높아야 한다."""
    # 마스크가 bbox와 정확히 일치 (300x400 직사각형)
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    cm = res["completenessMetrics"]
    assert cm.get("topCoverage", 0) >= 0.8, f"topCoverage should be high: {cm}"
    assert cm.get("bottomCoverage", 0) >= 0.8, f"bottomCoverage should be high: {cm}"
    assert cm.get("verticalContinuity", 0) >= 0.8, f"continuity should be high: {cm}"
    assert cm.get("productTopClipped") is False, "full mask should not be top-clipped"
    assert cm.get("productBottomClipped") is False, "full mask should not be bottom-clipped"


def test_completeness_top_clipped_mask():
    """상단 30%가 비어 있는 마스크 → productTopClipped=True."""
    # 캔버스 전체에서 상단 30%가 비어 있는 마스크
    from PIL import Image
    mask = Image.new("L", (CANVAS_W, CANVAS_H), 0)
    from PIL import Image as PIL_Image
    fill_y = int(CANVAS_H * 0.30)
    patch = PIL_Image.new("L", (CANVAS_W, CANVAS_H - fill_y), 255)
    mask.paste(patch, (0, fill_y))

    res = score_external_mask(
        detection_confidence=0.88, mask_confidence=0.82,
        mask_pil=mask,
        bbox={"x": 0, "y": 0, "width": CANVAS_W, "height": int(CANVAS_H * 0.5)},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    if not res["hardFail"]:
        cm = res["completenessMetrics"]
        assert cm.get("productTopClipped") is True, f"should detect top clip: {cm}"


def test_completeness_score_in_breakdown():
    """scoreBreakdown에 completenessScore가 포함되어야 한다."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    bd = res["scoreBreakdown"]
    assert "completenessScore" in bd
    assert bd["completenessScore"] >= 0.0


def test_completeness_score_contributes_to_total():
    """bbox를 꽉 채운 마스크는 completenessScore > 0이어야 한다."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    bd = res["scoreBreakdown"]
    assert bd["completenessScore"] > 0.0, (
        f"full-bbox mask should have completenessScore > 0, got {bd['completenessScore']}"
    )


def test_clipping_penalty_field_in_breakdown():
    """scoreBreakdown에 clippingPenalty 필드가 있어야 한다."""
    mask = _make_mask(CANVAS_W, CANVAS_H, fill=True)
    res = score_external_mask(
        detection_confidence=0.85, mask_confidence=0.80,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 350},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    bd = res["scoreBreakdown"]
    assert "clippingPenalty" in bd, "scoreBreakdown must have clippingPenalty"


def test_full_bbox_binary_mask_score_above_70():
    """이진 마스크 + 높은 신뢰도 + bbox 꽉 참 → overallMaskScore ≥ 70."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.92, mask_confidence=0.90,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    assert not res["hardFail"]
    assert res["overallMaskScore"] >= 70.0, (
        f"expected >=70 for clean binary mask, got {res['overallMaskScore']:.2f}\n"
        f"breakdown: {res['scoreBreakdown']}"
    )


# ── Stage 18.2: psd-tools import 테스트 ───────────────────────────────────────

def test_psd_tools_importable():
    """psd-tools 패키지가 import 가능해야 한다 (requirements-cpu.txt에 선언)."""
    try:
        import psd_tools  # noqa: F401
    except ImportError as e:
        assert False, f"psd-tools import 실패: {e}"


def test_psd_tools_psdimage_importable():
    """PSDImage 클래스가 import 가능해야 한다."""
    try:
        from psd_tools import PSDImage  # noqa: F401
    except ImportError as e:
        assert False, f"psd_tools.PSDImage import 실패: {e}"


def test_psd_tools_version_available():
    """importlib.metadata로 psd-tools 버전을 조회할 수 있어야 한다."""
    import importlib.metadata
    version = importlib.metadata.version("psd-tools")
    assert version is not None and len(version) > 0, "psd-tools version should be available"
    print(f"psd-tools version: {version}")


# ── Stage 18.2: completeness_metrics 구조 테스트 ──────────────────────────────

def test_completeness_metrics_all_expected_keys():
    """completenessMetrics의 모든 예상 키가 있어야 한다."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    cm = res["completenessMetrics"]
    expected = {
        "topCoverage", "bottomCoverage", "verticalContinuity",
        "maskVerticalFill", "productTopClipped", "productBottomClipped",
    }
    missing = expected - set(cm.keys())
    assert not missing, f"completenessMetrics missing keys: {missing}"


def test_vertical_continuity_for_continuous_mask():
    """연속적인 마스크 → verticalContinuity ≥ 0.9."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    cm = res["completenessMetrics"]
    assert cm.get("verticalContinuity", 0) >= 0.9, (
        f"solid rectangle should have near-perfect continuity: {cm['verticalContinuity']:.4f}"
    )


def test_low_contrast_edge_ratio_in_result():
    """lowContrastEdgeRatio 필드가 결과에 있어야 한다."""
    mask = _make_binary_mask(CANVAS_W, CANVAS_H, 300, 100, 400, 400)
    res = score_external_mask(
        detection_confidence=0.90, mask_confidence=0.85,
        mask_pil=mask, bbox={"x": 300, "y": 100, "width": 400, "height": 400},
        canvas_w=CANVAS_W, canvas_h=CANVAS_H, role="product",
    )
    assert "lowContrastEdgeRatio" in res
    # 완전한 이진 마스크의 low-contrast ratio는 낮아야 함
    assert res["lowContrastEdgeRatio"] <= 0.5, (
        f"binary mask should have low lowContrastEdgeRatio: {res['lowContrastEdgeRatio']:.4f}"
    )


if __name__ == "__main__":
    # Stage 18.1
    test_empty_mask_hard_fail()
    test_low_confidence_hard_fail()
    test_canvas_fill_hard_fail()
    test_invalid_bbox_hard_fail()
    test_good_detection_score_positive()
    test_native_mask_score_psd_isolated()
    test_native_mask_score_bbox_coarse_low()
    test_no_overlap_no_penalty()
    test_hand_overlap_soft_penalty()
    test_hand_overlap_hard_penalty()
    test_person_overlap_penalty()
    test_combined_overlap_penalty_cumulative()
    test_score_breakdown_fields()
    test_score_breakdown_in_hard_fail()
    test_overlap_max_clamped()
    # Stage 18.2: edge metric
    test_edge_boundary_metric_binary_mask_high_sharpness()
    test_edge_boundary_metric_old_std_comparison()
    test_edge_pixel_count_positive_for_binary_mask()
    test_raw_boundary_gradient_in_result()
    # Stage 18.2: completeness
    test_completeness_metrics_in_result()
    test_completeness_full_product_high_coverage()
    test_completeness_top_clipped_mask()
    test_completeness_score_in_breakdown()
    test_completeness_score_contributes_to_total()
    test_clipping_penalty_field_in_breakdown()
    test_full_bbox_binary_mask_score_above_70()
    # Stage 18.2: psd-tools
    test_psd_tools_importable()
    test_psd_tools_psdimage_importable()
    test_psd_tools_version_available()
    # Stage 18.2: completeness structure
    test_completeness_metrics_all_expected_keys()
    test_vertical_continuity_for_continuous_mask()
    test_low_contrast_edge_ratio_in_result()
    print("ALL mask_quality tests PASS")
