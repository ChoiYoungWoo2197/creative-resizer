"""Native mask vs. external AI mask 선택 정책 (섹션 8 구현).

우선순위 정책:
  A. native = psd_isolated_layer AND nativeScore >= 80
     → 기본 native 유지; externalScore > native + REPLACE_MARGIN 일 때만 교체
     → compareOnly: 절대 교체 안 함
  B. native 부분 품질(partial)
     → external score >= threshold AND hardFail 없으면 사용
  C. native mask 없음
     → external score >= threshold이면 사용
  D. external 결과 여러 개 → 최우선 하나 선택
  E. external 실패 → 기존 경로 유지, warning만
"""

from __future__ import annotations
import os
import io
import base64
import logging

log = logging.getLogger("worker.external_mask_selector")

REPLACE_MARGIN = float(os.environ.get("CREATIVE_SEGMENTATION_REPLACE_MARGIN", "5"))
MASK_SCORE_THRESHOLD = float(os.environ.get("CREATIVE_SEGMENTATION_MASK_SCORE_THRESHOLD", "70"))
COMPARE_ONLY = os.environ.get("CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY", "true").lower() == "true"


def select_best_external_detection(
    detections: list[dict],
    role: str = "product",
    native_bbox: dict | None = None,
    canvas_w: int = 1,
    canvas_h: int = 1,
) -> dict | None:
    """외부 탐지 결과에서 최적 candidate 선택 (정책 D).

    기준: product_evidence → detection_confidence → mask_quality_score → bbox 위치
    반환: 선택된 detection dict | None
    """
    candidates = [d for d in detections if d.get("role") == role]
    if not candidates:
        return None

    def _score_candidate(d: dict) -> float:
        score = 0.0
        score += d.get("detectionConfidence", 0.0) * 40
        score += d.get("maskConfidence", 0.0) * 30
        score += d.get("maskQualityScore", 0.0) / 100 * 20

        # native bbox와 위치 겹침 가점
        if native_bbox:
            iou = _iou(d.get("bbox", {}), native_bbox)
            score += iou * 10

        return score

    candidates.sort(key=_score_candidate, reverse=True)
    return candidates[0]


def decide_mask_source(
    native_source: str,
    native_score: float,
    external_detection: dict | None,
    external_metadata: dict,
    job_id: str | None = None,
) -> dict:
    """native vs external 선택 결정.

    반환 dict:
      selectedMaskSource: str
      useExternal: bool
      externalMaskRejectedReason: str | None
      externalMaskScore: float
      nativeMaskScore: float
      externalSegmentationUsed: bool
    """
    prefix = f"[{job_id or 'job'}][MaskSel]"

    ext_score = 0.0
    rejected_reason = None

    if external_detection is None:
        rejected_reason = "no_external_detection"
        return _native_result(native_source, native_score, ext_score, rejected_reason)

    ext_score = external_detection.get("maskQualityScore", 0.0)
    if external_detection.get("hardFail") or external_detection.get("leakRisk", 0) > 0.7:
        rejected_reason = "hard_fail_or_high_leak_risk"
        log.info("%s external 거부: %s score=%.1f", prefix, rejected_reason, ext_score)
        return _native_result(native_source, native_score, ext_score, rejected_reason)

    # compareOnly: 교체 불가, 비교 metadata만
    if COMPARE_ONLY:
        rejected_reason = "compare_only_mode"
        log.info(
            "%s compareOnly: native_score=%.1f external_score=%.1f",
            prefix, native_score, ext_score,
        )
        return _native_result(native_source, native_score, ext_score, rejected_reason)

    # 정책 A: 우수한 native
    if native_source == "psd_isolated_layer" and native_score >= 80:
        if ext_score > native_score + REPLACE_MARGIN:
            log.info(
                "%s 정책A: external 선택 (native=%.1f external=%.1f margin=%.1f)",
                prefix, native_score, ext_score, REPLACE_MARGIN,
            )
            return _external_result(native_source, native_score, ext_score)
        rejected_reason = f"native_preferred_high_quality:{native_score:.1f}"
        return _native_result(native_source, native_score, ext_score, rejected_reason)

    # 정책 B: native partial
    if native_score >= 50:
        if ext_score >= MASK_SCORE_THRESHOLD and ext_score > native_score:
            log.info(
                "%s 정책B: external 선택 (native_partial=%.1f external=%.1f)",
                prefix, native_score, ext_score,
            )
            return _external_result(native_source, native_score, ext_score)
        rejected_reason = f"native_partial_but_external_insufficient:{ext_score:.1f}<{MASK_SCORE_THRESHOLD}"
        return _native_result(native_source, native_score, ext_score, rejected_reason)

    # 정책 C: native 없음 또는 매우 낮은 품질
    if ext_score >= MASK_SCORE_THRESHOLD:
        log.info(
            "%s 정책C: external 선택 (no_native native_score=%.1f external=%.1f)",
            prefix, native_score, ext_score,
        )
        return _external_result(native_source, native_score, ext_score)

    rejected_reason = f"external_score_below_threshold:{ext_score:.1f}<{MASK_SCORE_THRESHOLD}"
    return _native_result(native_source, native_score, ext_score, rejected_reason)


def extract_mask_png_from_detection(detection: dict):
    """detection의 maskPngBase64 → PIL L-mode Image."""
    b64 = detection.get("maskPngBase64", "")
    if not b64:
        return None
    try:
        from PIL import Image
        data = base64.b64decode(b64)
        img = Image.open(io.BytesIO(data)).convert("L")
        return img
    except Exception as e:
        log.warning("mask PNG decode 실패: %s", e)
        return None


def build_external_mask_metadata(
    detection: dict | None,
    selector_result: dict,
    processing_ms: int = 0,
    service_device: str = "unknown",
) -> dict:
    """resizer.py result에 병합할 외부 segmentation metadata."""
    meta: dict = {
        "externalSegmentationAttempted": True,
        "externalSegmentationUsed":      selector_result.get("useExternal", False),
        "externalSegmentationCompareOnly": COMPARE_ONLY,
        "segmentationProvider":          "grounded-sam2",
        "segmentationDevice":            service_device,
        "nativeMaskScore":               selector_result.get("nativeMaskScore", 0.0),
        "externalMaskScore":             selector_result.get("externalMaskScore", 0.0),
        "selectedMaskSource":            selector_result.get("selectedMaskSource", "native"),
        "externalMaskRejectedReason":    selector_result.get("externalMaskRejectedReason"),
        "externalProcessingMs":          processing_ms,
    }
    if detection:
        meta["externalDetectedPrompt"]     = detection.get("prompt", "")
        meta["externalDetectedBbox"]       = detection.get("bbox", {})
        meta["externalDetectionConfidence"] = detection.get("detectionConfidence", 0.0)
        meta["externalMaskConfidence"]     = detection.get("maskConfidence", 0.0)
        meta["externalMaskPath"]           = detection.get("maskPath")
    return meta


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _native_result(
    native_source: str, native_score: float,
    ext_score: float, rejected_reason: str | None,
) -> dict:
    return {
        "selectedMaskSource":         native_source,
        "useExternal":                False,
        "externalMaskRejectedReason": rejected_reason,
        "externalMaskScore":          ext_score,
        "nativeMaskScore":            native_score,
        "externalSegmentationUsed":   False,
    }


def _external_result(
    native_source: str, native_score: float, ext_score: float,
) -> dict:
    return {
        "selectedMaskSource":         "external_grounded_sam2",
        "useExternal":                True,
        "externalMaskRejectedReason": None,
        "externalMaskScore":          ext_score,
        "nativeMaskScore":            native_score,
        "externalSegmentationUsed":   True,
    }


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
