"""Mask quality scoring for external AI segmentation results (0-100 scale).

가산/감산 항목을 명시적으로 계산해 overallMaskScore를 반환한다.
PIL L-mode 이미지(255=masked, 0=background)를 입력으로 받는다.
"""

from __future__ import annotations
import math


# ─── 상수 ─────────────────────────────────────────────────────────────────────

SCORE_MAX = 100.0

# hard-fail 조건 상수
MIN_AREA_RATIO = 0.001   # 너무 작은 mask
MAX_AREA_RATIO = 0.90    # canvas 거의 전체
MIN_CONFIDENCE = 0.10
MAX_FRAGMENT_HARD = 20   # 조각이 너무 많음


def score_external_mask(
    detection_confidence: float,
    mask_confidence: float,
    mask_pil,                   # PIL Image L-mode | None
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    role: str = "product",
    fragment_count: int = 1,
    native_bbox: dict | None = None,
) -> dict:
    """외부 AI mask 품질 점수 0~100 계산.

    반환 dict 필드:
      edgeSharpness, alphaCoverage, leakRisk, fragmentCount,
      bboxFillRatio, maskAreaRatio, roleConsistencyScore,
      objectCompletenessScore, overallMaskScore,
      hardFail (bool), hardFailReason (str|None)
    """
    canvas_area = canvas_w * canvas_h or 1

    # ── 기본 계측 ──────────────────────────────────────────────────────────────
    bbox_w = max(int(bbox.get("width", 0)), 0)
    bbox_h = max(int(bbox.get("height", 0)), 0)
    bbox_area = bbox_w * bbox_h

    mask_pixel_count = 0
    edge_sharpness = 0.0

    if mask_pil is not None:
        try:
            import numpy as np
            arr = np.array(mask_pil.convert("L"))
            mask_pixel_count = int((arr > 127).sum())

            # Edge sharpness: Sobel magnitude 표준편차
            from PIL import ImageFilter
            edge = mask_pil.filter(ImageFilter.FIND_EDGES)
            edge_arr = np.array(edge).astype(float)
            edge_sharpness = float(min(edge_arr.std() / 64.0, 1.0))  # normalise
        except Exception:
            mask_pixel_count = int(bbox_area * 0.6)
            edge_sharpness = 0.35

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

    # ── 가산 점수 계산 ──────────────────────────────────────────────────────────
    score = 0.0

    # +30: detection confidence (0~1 → 0~30)
    score += detection_confidence * 30

    # +20: mask confidence (0~1 → 0~20)
    score += mask_confidence * 20

    # +12: edge sharpness (높을수록 정밀 mask)
    score += edge_sharpness * 12

    # +10: 단일 연결 객체 (fragment 적을수록 가점)
    if fragment_count == 1:
        score += 10
    elif fragment_count <= 3:
        score += 6
    elif fragment_count <= 6:
        score += 2

    # +8: bbox fill ratio — bbox를 적당히 채움 (0.3~0.85 이상적)
    if 0.30 <= bbox_fill_ratio <= 0.85:
        score += 8
    elif 0.15 <= bbox_fill_ratio < 0.30 or 0.85 < bbox_fill_ratio <= 0.95:
        score += 3

    # +10: 제품형 aspect ratio (키/폭이 극단적이지 않은 경우)
    if role == "product" and bbox_w > 0 and bbox_h > 0:
        ar = bbox_h / bbox_w
        if 0.4 <= ar <= 3.0:
            score += 10
        elif 0.2 <= ar < 0.4 or 3.0 < ar <= 5.0:
            score += 4

    # +8: 기존 product bbox와 위치 유사
    if native_bbox and role == "product":
        overlap = _iou(bbox, native_bbox)
        score += overlap * 8

    # +5: role 일관성
    role_consistency = _role_consistency(role, detection_confidence)
    score += role_consistency * 5

    # ── 감산 점수 ──────────────────────────────────────────────────────────────
    # 감점 항목 — 합리적 상한 내에서만

    # -15: 배경 누출 의심 (canvas fill ratio가 매우 높음)
    if mask_area_ratio > 0.60:
        score -= 15
    elif mask_area_ratio > 0.40:
        score -= 5

    # -10: bbox 면적 대부분 차지 (잘림/배경 누출 위험)
    if bbox_fill_ratio > 0.95:
        score -= 10

    # -8: 조각 과다
    if fragment_count > 6:
        score -= min((fragment_count - 6) * 2, 12)

    # -12: 제품 잘림 의심 (bbox가 canvas 가장자리에 매우 가까움)
    edge_clip_penalty = _compute_edge_clip_penalty(bbox, canvas_w, canvas_h)
    score -= edge_clip_penalty * 12

    alpha_coverage = min(bbox_fill_ratio, 1.0)
    leak_risk = max(0.0, mask_area_ratio - 0.3) / 0.7  # 0.3 이상부터 증가

    overall = max(0.0, min(SCORE_MAX, score))

    return {
        "edgeSharpness":          round(edge_sharpness, 3),
        "alphaCoverage":          round(alpha_coverage, 3),
        "leakRisk":               round(leak_risk, 3),
        "fragmentCount":          fragment_count,
        "bboxFillRatio":          round(bbox_fill_ratio, 3),
        "maskAreaRatio":          round(mask_area_ratio, 4),
        "roleConsistencyScore":   round(role_consistency, 3),
        "objectCompletenessScore": round(min(bbox_fill_ratio * 1.1, 1.0), 3),
        "overallMaskScore":       round(overall, 2),
        "hardFail":               False,
        "hardFailReason":         None,
        "detectionConfidence":    round(detection_confidence, 4),
        "maskConfidence":         round(mask_confidence, 4),
    }


def score_native_mask(
    source: str,
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    product_score: float = 50.0,
) -> float:
    """기존 native mask에 대한 0~100 점수 반환.

    source 우선순위 기반 점수화. segmentation_poc.py 의 compute_mask_quality와 연동.
    """
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
    # product_score 가중치 (±10)
    score = base + (product_score - 50.0) * 0.20
    return round(max(0.0, min(100.0, score)), 2)


# ─── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _zero_result(
    area_ratio: float, bbox_fill: float, edge_sh: float,
    frags: int, hf: bool, hf_reason: str | None,
) -> dict:
    return {
        "edgeSharpness":           round(edge_sh, 3),
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
