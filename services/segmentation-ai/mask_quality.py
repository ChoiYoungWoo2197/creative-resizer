"""Mask quality scoring for external AI segmentation results (0-100 scale).

가산/감산 항목을 명시적으로 계산해 overallMaskScore를 반환한다.
PIL L-mode 이미지(255=masked, 0=background)를 입력으로 받는다.

Stage 18.1: hand_overlap_ratio / person_overlap_ratio 감점 추가.
Stage 18.2: 경계 정규화 edge sharpness 메트릭 + completeness 점수 추가.
  - edge: 전체 이미지 std → 실제 경계 픽셀 mean (소형 제품 패널티 제거)
  - completeness: bbox 내 상/하단 커버리지 + 수직 연속성
  - clippingPenalty: 제품 상/하단 잘림 감점
  - edgePixelCount / rawBoundaryGradient / lowContrastEdgeRatio 추가 필드
"""

from __future__ import annotations
import math


# ─── 상수 ─────────────────────────────────────────────────────────────────────

SCORE_MAX = 100.0

# hard-fail 조건 상수
MIN_AREA_RATIO    = 0.001   # 너무 작은 mask
MAX_AREA_RATIO    = 0.90    # canvas 거의 전체
MIN_CONFIDENCE    = 0.10
MAX_FRAGMENT_HARD = 20      # 조각이 너무 많음

# 손·사람 겹침 임계값
HAND_OVERLAP_SOFT   = 0.05
HAND_OVERLAP_HARD   = 0.10
PERSON_OVERLAP_SOFT = 0.05
PERSON_OVERLAP_HARD = 0.10

# Stage 18.2 edge 메트릭 — 경계 픽셀 기준
EDGE_BOUNDARY_THRESHOLD = 30    # 이 값 이상 = 실제 경계 픽셀
EDGE_BOUNDARY_EXPECTED  = 200.0  # 완전한 이진 마스크의 경계 평균값 (기대치)
EDGE_MIN_BOUNDARY_PIXELS = 10    # 최소 경계 픽셀 수 (미만이면 0.0)

# Stage 18.2 completeness 임계값
COMPLETENESS_TOP_CLIP_THRESHOLD    = 0.10  # 상단 10% 이상 잘리면 감점
COMPLETENESS_BOTTOM_CLIP_THRESHOLD = 0.10
COMPLETENESS_CONTINUITY_THRESHOLD  = 0.65  # 수직 연속성 65% 미만 감점


def score_external_mask(
    detection_confidence: float,
    mask_confidence: float,
    mask_pil,                       # PIL Image L-mode | None
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    role: str = "product",
    fragment_count: int = 1,
    native_bbox: dict | None = None,
    hand_overlap_ratio: float = 0.0,
    person_overlap_ratio: float = 0.0,
) -> dict:
    """외부 AI mask 품질 점수 0~100 계산 (Stage 18.2).

    반환 dict 필드:
      edgeSharpness, alphaCoverage, leakRisk, fragmentCount,
      bboxFillRatio, maskAreaRatio, roleConsistencyScore,
      objectCompletenessScore, overallMaskScore,
      hardFail (bool), hardFailReason (str|None),
      scoreBreakdown (dict),
      completenessMetrics (dict)
    """
    canvas_area = canvas_w * canvas_h or 1

    # ── 기본 계측 ──────────────────────────────────────────────────────────────
    bbox_w = max(int(bbox.get("width", 0)), 0)
    bbox_h = max(int(bbox.get("height", 0)), 0)
    bbox_area = bbox_w * bbox_h

    mask_pixel_count = 0
    edge_sharpness   = 0.0
    edge_pixel_count = 0
    raw_boundary_gradient  = 0.0
    low_contrast_edge_ratio = 0.0

    completeness_metrics: dict = {}
    normalized_edge: float = 0.0
    edge_metric_clamped: bool = False

    if mask_pil is not None:
        try:
            import numpy as np
            arr = np.array(mask_pil.convert("L"))
            mask_pixel_count = int((arr > 127).sum())

            # Stage 18.2: 경계 정규화 edge sharpness
            from PIL import ImageFilter
            edge = mask_pil.filter(ImageFilter.FIND_EDGES)
            edge_arr = np.array(edge).astype(float)

            boundary_mask = edge_arr > EDGE_BOUNDARY_THRESHOLD
            edge_pixel_count = int(boundary_mask.sum())

            if edge_pixel_count >= EDGE_MIN_BOUNDARY_PIXELS:
                bp_vals = edge_arr[boundary_mask]
                raw_boundary_gradient = float(bp_vals.mean())
                normalized_edge = raw_boundary_gradient / EDGE_BOUNDARY_EXPECTED
                edge_metric_clamped = normalized_edge > 1.0
                edge_sharpness = float(min(normalized_edge, 1.0))
                # 저대비 경계 비율 (edge_arr < 100 / total boundary)
                low_contrast_edge_ratio = float(
                    (bp_vals < 100).sum() / max(len(bp_vals), 1)
                )
            else:
                raw_boundary_gradient = 0.0
                normalized_edge = 0.0
                edge_metric_clamped = False
                edge_sharpness = 0.0
                low_contrast_edge_ratio = 1.0

            # Stage 18.2: completeness 계산
            if role == "product" and bbox_w > 0 and bbox_h > 0:
                completeness_metrics = _compute_completeness_metrics(
                    arr, bbox, canvas_w, canvas_h
                )

        except Exception:
            mask_pixel_count = int(bbox_area * 0.6)
            edge_sharpness   = 0.35
            edge_pixel_count = 0

    mask_area_ratio = mask_pixel_count / canvas_area
    bbox_fill_ratio = (mask_pixel_count / max(bbox_area, 1)) if bbox_area > 0 else 1.0

    # ── Hard Fail 검사 ─────────────────────────────────────────────────────────
    hard_fail = False
    hard_fail_reason = None

    if detection_confidence < MIN_CONFIDENCE or mask_confidence < MIN_CONFIDENCE:
        hard_fail = True
        hard_fail_reason = "confidence_too_low"
    elif mask_pixel_count == 0:
        hard_fail = True
        hard_fail_reason = "empty_mask"
    elif mask_area_ratio < MIN_AREA_RATIO:
        hard_fail = True
        hard_fail_reason = "mask_too_small"
    elif mask_area_ratio > MAX_AREA_RATIO:
        hard_fail = True
        hard_fail_reason = "mask_too_large_canvas_fill"
    elif fragment_count > MAX_FRAGMENT_HARD:
        hard_fail = True
        hard_fail_reason = "too_many_fragments"
    elif not _check_bbox_valid(bbox, canvas_w, canvas_h):
        hard_fail = True
        hard_fail_reason = "invalid_bbox"

    if hard_fail:
        return _zero_result(
            mask_area_ratio, bbox_fill_ratio, edge_sharpness,
            fragment_count, hard_fail, hard_fail_reason,
        )

    # ── 가산 점수 ──────────────────────────────────────────────────────────────
    conf_score      = detection_confidence * 25       # +25 (Stage 18.2: 30→25)
    mask_conf_score = mask_confidence * 20            # +20

    # Stage 18.2: 경계 정규화 edge score (12→15점, 실제 경계 품질 반영)
    edge_score = edge_sharpness * 15                  # +15

    frag_score = 0.0
    if fragment_count == 1:
        frag_score = 8.0
    elif fragment_count <= 3:
        frag_score = 5.0
    elif fragment_count <= 6:
        frag_score = 2.0

    bbox_fill_score = 0.0
    if 0.30 <= bbox_fill_ratio <= 0.85:
        bbox_fill_score = 6.0
    elif 0.15 <= bbox_fill_ratio < 0.30 or 0.85 < bbox_fill_ratio <= 0.95:
        bbox_fill_score = 2.0

    aspect_score = 0.0
    if role == "product" and bbox_w > 0 and bbox_h > 0:
        ar = bbox_h / bbox_w
        if 0.4 <= ar <= 3.0:
            aspect_score = 8.0
        elif 0.2 <= ar < 0.4 or 3.0 < ar <= 5.0:
            aspect_score = 3.0

    native_bbox_score = 0.0
    if native_bbox and role == "product":
        overlap = _iou(bbox, native_bbox)
        native_bbox_score = overlap * 5

    role_consistency = _role_consistency(role, detection_confidence)
    role_score = role_consistency * 5

    # Stage 18.2: completeness 점수 (+15점 신규)
    completeness_score = 0.0
    clipping_penalty   = 0.0
    top_bottom_score   = 0.0
    if completeness_metrics:
        top_cov  = completeness_metrics.get("topCoverage", 0.0)
        bot_cov  = completeness_metrics.get("bottomCoverage", 0.0)
        cont     = completeness_metrics.get("verticalContinuity", 0.0)
        top_clip = completeness_metrics.get("productTopClipped", False)
        bot_clip = completeness_metrics.get("productBottomClipped", False)

        # 상/하단 커버리지 점수 (최대 8점)
        top_bottom_score = min((top_cov + bot_cov) / 2.0, 1.0) * 8.0

        # 연속성 점수 (최대 7점)
        continuity_score = min(cont, 1.0) * 7.0
        completeness_score = top_bottom_score + continuity_score

        # 잘림 감점 (최대 -8점)
        clip_count = (1 if top_clip else 0) + (1 if bot_clip else 0)
        clipping_penalty = clip_count * 4.0

    score = (
        conf_score + mask_conf_score + edge_score + frag_score
        + bbox_fill_score + aspect_score + native_bbox_score + role_score
        + completeness_score
    )

    # ── 감산 점수 ──────────────────────────────────────────────────────────────

    area_leak_penalty = 0.0
    if mask_area_ratio > 0.60:
        area_leak_penalty = 15.0
    elif mask_area_ratio > 0.40:
        area_leak_penalty = 5.0

    bbox_fill_penalty = 0.0
    if bbox_fill_ratio > 0.95:
        bbox_fill_penalty = 8.0

    frag_penalty = 0.0
    if fragment_count > 6:
        frag_penalty = min((fragment_count - 6) * 2.0, 12.0)

    edge_clip_factor = _compute_edge_clip_penalty(bbox, canvas_w, canvas_h)
    edge_clip_penalty = edge_clip_factor * 10.0

    # 손·사람 겹침 감점 (Stage 18.1)
    hand_leak_penalty = 0.0
    if hand_overlap_ratio >= HAND_OVERLAP_HARD:
        hand_leak_penalty = min(hand_overlap_ratio / 0.5, 1.0) * 25.0
    elif hand_overlap_ratio >= HAND_OVERLAP_SOFT:
        hand_leak_penalty = (hand_overlap_ratio / HAND_OVERLAP_HARD) * 8.0

    person_leak_penalty = 0.0
    if person_overlap_ratio >= PERSON_OVERLAP_HARD:
        person_leak_penalty = min(person_overlap_ratio / 0.5, 1.0) * 18.0
    elif person_overlap_ratio >= PERSON_OVERLAP_SOFT:
        person_leak_penalty = (person_overlap_ratio / PERSON_OVERLAP_HARD) * 5.0

    score -= (
        area_leak_penalty + bbox_fill_penalty + frag_penalty
        + edge_clip_penalty + hand_leak_penalty + person_leak_penalty
        + clipping_penalty
    )

    alpha_coverage = min(bbox_fill_ratio, 1.0)
    leak_risk = max(0.0, mask_area_ratio - 0.3) / 0.7

    overall = max(0.0, min(SCORE_MAX, score))

    score_breakdown = {
        # 가산
        "confidenceScore":      round(conf_score, 2),
        "maskConfidenceScore":  round(mask_conf_score, 2),
        "edgeAlignmentScore":   round(edge_score, 2),
        "completenessScore":    round(completeness_score, 2),
        "topBottomCoverageScore": round(top_bottom_score, 2),
        "fragmentScore":        round(frag_score, 2),
        "bboxFillScore":        round(bbox_fill_score, 2),
        "aspectRatioScore":     round(aspect_score, 2),
        "nativeBboxScore":      round(native_bbox_score, 2),
        "roleConsistencyScore": round(role_score, 2),
        # 감산
        "areaLeakPenalty":      round(-area_leak_penalty, 2),
        "bboxFillPenalty":      round(-bbox_fill_penalty, 2),
        "fragmentPenalty":      round(-frag_penalty, 2),
        "edgeClipPenalty":      round(-edge_clip_penalty, 2),
        "handLeakPenalty":      round(-hand_leak_penalty, 2),
        "personLeakPenalty":    round(-person_leak_penalty, 2),
        "backgroundLeakPenalty": 0.0,
        "clippingPenalty":      round(-clipping_penalty, 2),
        "totalScore":           round(overall, 2),
    }

    return {
        "edgeSharpness":           round(edge_sharpness, 4),
        "alphaCoverage":           round(alpha_coverage, 3),
        "leakRisk":                round(leak_risk, 3),
        "fragmentCount":           fragment_count,
        "bboxFillRatio":           round(bbox_fill_ratio, 3),
        "maskAreaRatio":           round(mask_area_ratio, 4),
        "roleConsistencyScore":    round(role_consistency, 3),
        "objectCompletenessScore": round(min(bbox_fill_ratio * 1.1, 1.0), 3),
        "overallMaskScore":        round(overall, 2),
        "hardFail":                False,
        "hardFailReason":          None,
        "detectionConfidence":     round(detection_confidence, 4),
        "maskConfidence":          round(mask_confidence, 4),
        "handLeakPenalty":         round(hand_leak_penalty, 2),
        "personLeakPenalty":       round(person_leak_penalty, 2),
        "edgePixelCount":          edge_pixel_count,
        "rawBoundaryGradient":     round(raw_boundary_gradient, 2),
        "lowContrastEdgeRatio":    round(low_contrast_edge_ratio, 4),
        # Stage 18.3: edge metric 클램핑 진단
        "rawEdgeMetric":           round(raw_boundary_gradient, 2),
        "normalizedEdgeMetric":    round(raw_boundary_gradient / EDGE_BOUNDARY_EXPECTED, 4),
        "edgeMetricClamped":       edge_metric_clamped,
        "edgeClampReason":         "boundary_mean_exceeds_reference" if edge_metric_clamped else None,
        "scoreBreakdown":          score_breakdown,
        "completenessMetrics":     completeness_metrics,
    }


def score_native_mask(
    source: str,
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    product_score: float = 50.0,
) -> float:
    """기존 native mask에 대한 0~100 점수 반환."""
    _SOURCE_BASE: dict[str, float] = {
        "psd_isolated_layer":   95.0,
        "psd_alpha":            80.0,
        "psd_layer_mask":       82.0,
        "ai_bbox_crop":         60.0,
        "object_bbox_coarse":   40.0,
        "bbox_coarse":          35.0,
        "visual_context":       20.0,
        "composite_fallback":   10.0,
        "unknown":              5.0,
    }
    base = _SOURCE_BASE.get(source, 5.0)
    score = base + (product_score - 50.0) * 0.20
    return round(max(0.0, min(100.0, score)), 2)


# ─── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _compute_completeness_metrics(
    mask_arr,       # numpy bool or uint8 (H×W)
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
) -> dict:
    """Stage 18.2: bbox 기준 마스크 상·하단·연속성 평가.

    Returns dict:
      topCoverage, bottomCoverage, verticalContinuity,
      productTopClipped, productBottomClipped,
      productLeftClipped, productRightClipped,
      maskVerticalFill
    """
    try:
        import numpy as np

        binary = mask_arr > 127

        x  = max(0, int(bbox.get("x", 0)))
        y  = max(0, int(bbox.get("y", 0)))
        bw = max(1, int(bbox.get("width",  1)))
        bh = max(1, int(bbox.get("height", 1)))

        x2 = min(canvas_w, x + bw)
        y2 = min(canvas_h, y + bh)

        if x2 <= x or y2 <= y:
            return {}

        # bbox 내 마스크 영역
        roi = binary[y:y2, x:x2]
        if roi.size == 0:
            return {}

        roi_h, roi_w = roi.shape

        # 행(row) 단위: 각 행에 mask 픽셀이 있는지
        row_any    = roi.any(axis=1)        # (H,) bool
        row_fill   = roi.mean(axis=1)       # (H,) float 0~1
        col_any    = roi.any(axis=0)        # (W,) bool

        covered_rows = row_any.sum()
        covered_cols = col_any.sum()

        # 수직 연속성: 첫 마스크 행 ~ 마지막 마스크 행 사이에 마스크가 없는 행 비율
        if row_any.any():
            first_row = int(np.argmax(row_any))
            last_row  = int(roi_h - 1 - np.argmax(row_any[::-1]))
            span      = last_row - first_row + 1
            filled_in_span = int(row_any[first_row:last_row+1].sum())
            continuity = filled_in_span / max(span, 1)

            # 상단 커버리지: 마스크 시작이 bbox 상단에 얼마나 가까운지
            top_coverage    = 1.0 - first_row / roi_h
            # 하단 커버리지: 마스크 끝이 bbox 하단에 얼마나 가까운지
            bottom_coverage = (last_row + 1) / roi_h
        else:
            first_row = roi_h
            last_row  = -1
            span      = 0
            continuity = 0.0
            top_coverage    = 0.0
            bottom_coverage = 0.0

        if col_any.any():
            first_col  = int(np.argmax(col_any))
            last_col   = int(roi_w - 1 - np.argmax(col_any[::-1]))
            left_cov   = 1.0 - first_col / roi_w
            right_cov  = (last_col + 1) / roi_w
        else:
            left_cov  = 0.0
            right_cov = 0.0

        # 잘림 판정
        top_clip    = first_row > roi_h * COMPLETENESS_TOP_CLIP_THRESHOLD
        bottom_clip = (roi_h - 1 - last_row) > roi_h * COMPLETENESS_BOTTOM_CLIP_THRESHOLD
        left_clip   = first_col > roi_w * 0.10 if col_any.any() else True
        right_clip  = (roi_w - 1 - last_col) > roi_w * 0.10 if col_any.any() else True

        return {
            "topCoverage":          round(float(top_coverage), 4),
            "bottomCoverage":       round(float(bottom_coverage), 4),
            "verticalContinuity":   round(float(continuity), 4),
            "maskVerticalFill":     round(float(covered_rows / roi_h), 4),
            "maskHorizontalFill":   round(float(covered_cols / roi_w), 4),
            "productTopClipped":    bool(top_clip),
            "productBottomClipped": bool(bottom_clip),
            "productLeftClipped":   bool(left_clip),
            "productRightClipped":  bool(right_clip),
            "bboxSpanRows":         int(span),
            "bboxTotalRows":        int(roi_h),
        }
    except Exception:
        return {}


def _zero_result(
    area_ratio: float, bbox_fill: float, edge_sh: float,
    frags: int, hf: bool, hf_reason: str | None,
) -> dict:
    breakdown = {
        "confidenceScore": 0.0, "maskConfidenceScore": 0.0, "edgeAlignmentScore": 0.0,
        "completenessScore": 0.0, "topBottomCoverageScore": 0.0,
        "fragmentScore": 0.0, "bboxFillScore": 0.0, "aspectRatioScore": 0.0,
        "nativeBboxScore": 0.0, "roleConsistencyScore": 0.0,
        "areaLeakPenalty": 0.0, "bboxFillPenalty": 0.0, "fragmentPenalty": 0.0,
        "edgeClipPenalty": 0.0, "handLeakPenalty": 0.0, "personLeakPenalty": 0.0,
        "backgroundLeakPenalty": 0.0, "clippingPenalty": 0.0,
        "totalScore": 0.0,
    }
    return {
        "edgeSharpness":           round(edge_sh, 4),
        "alphaCoverage":           round(bbox_fill, 3),
        "leakRisk":                1.0,
        "fragmentCount":           frags,
        "bboxFillRatio":           round(bbox_fill, 3),
        "maskAreaRatio":           round(area_ratio, 4),
        "roleConsistencyScore":    0.0,
        "objectCompletenessScore": 0.0,
        "overallMaskScore":        0.0,
        "hardFail":                hf,
        "hardFailReason":          hf_reason,
        "detectionConfidence":     0.0,
        "maskConfidence":          0.0,
        "handLeakPenalty":         0.0,
        "personLeakPenalty":       0.0,
        "edgePixelCount":          0,
        "rawBoundaryGradient":     0.0,
        "lowContrastEdgeRatio":    1.0,
        "rawEdgeMetric":           0.0,
        "normalizedEdgeMetric":    0.0,
        "edgeMetricClamped":       False,
        "edgeClampReason":         None,
        "scoreBreakdown":          breakdown,
        "completenessMetrics":     {},
    }


def _check_bbox_valid(bbox: dict, canvas_w: int, canvas_h: int) -> bool:
    x = bbox.get("x", 0)
    y = bbox.get("y", 0)
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    if w <= 0 or h <= 0:
        return False
    if x < 0 or y < 0 or x + w > canvas_w * 1.05 or y + h > canvas_h * 1.05:
        return False
    if math.isnan(x) or math.isnan(y) or math.isnan(w) or math.isnan(h):
        return False
    return True


def _iou(a: dict, b: dict) -> float:
    ax1, ay1 = a.get("x", 0), a.get("y", 0)
    ax2, ay2 = ax1 + a.get("width", 0), ay1 + a.get("height", 0)
    bx1, by1 = b.get("x", 0), b.get("y", 0)
    bx2, by2 = bx1 + b.get("width", 0), by1 + b.get("height", 0)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / max(union, 1)


def _role_consistency(role: str, confidence: float) -> float:
    if confidence >= 0.7:
        return 1.0
    if confidence >= 0.5:
        return 0.75
    if confidence >= 0.3:
        return 0.5
    return 0.25


def _compute_edge_clip_penalty(bbox: dict, canvas_w: int, canvas_h: int) -> float:
    """bbox가 canvas 가장자리에 얼마나 가까운지 (0~1)."""
    margin = 5
    x = bbox.get("x", 0)
    y = bbox.get("y", 0)
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    clipped = 0
    if x <= margin: clipped += 1
    if y <= margin: clipped += 1
    if x + w >= canvas_w - margin: clipped += 1
    if y + h >= canvas_h - margin: clipped += 1
    return clipped / 4.0
