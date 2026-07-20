#!/usr/bin/env python3
"""Stage 18 segmentation 결과 분석 및 debug artifact 생성.

verify_stage18_server.sh 에서 호출됨.
PIL/numpy 없을 때도 JSON 분석은 정상 동작 (이미지 생성만 스킵).

Stage 18.1 변경:
  - handLeakRisk: 픽셀 overlap(handOverlapRatio) 우선, bbox IoU fallback
  - personLeakRisk: 동일
  - handSubtractApplied 필드 읽기
  - flattenMethod / scoreBreakdown 보고서 포함
  - psd-tools 정보 기록

사용법:
  python3 scripts/verify_stage18_server.py \
    --segment-json /path/to/segmentation.json \
    --health-json  /path/to/health.json \
    --image-path   /path/to/original.png \
    --output-dir   /path/to/artifacts/
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys

# PIL optional — 없으면 이미지 생성 스킵
try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ─── 상수 ─────────────────────────────────────────────────────────────────────

MASK_SCORE_THRESHOLD = 70.0
BBOX_FALLBACK_WARNINGS = {"sam2_unavailable_bbox_mask_used"}
REAL_SAM2_SOURCES = {"real_sam2", ""}
BBOX_FALLBACK_SOURCES = {"external_bbox_fallback", "bbox_fallback"}

ROLE_COLORS = {
    "product": (0, 200, 0),
    "hand":    (0, 100, 255),
    "person":  (255, 100, 0),
    "logo":    (200, 0, 200),
}


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment-json", required=True)
    parser.add_argument("--health-json",   default=None)
    parser.add_argument("--image-path",    default=None)
    parser.add_argument("--output-dir",    required=True)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # ── JSON 로드 ──────────────────────────────────────────────────────────────
    seg_data: dict = {}
    health_data: dict = {}

    if os.path.exists(args.segment_json):
        with open(args.segment_json, encoding="utf-8") as f:
            seg_data = json.load(f)

    if args.health_json and os.path.exists(args.health_json):
        with open(args.health_json, encoding="utf-8") as f:
            health_data = json.load(f)

    # ── Health 정보 ────────────────────────────────────────────────────────────
    real_inference: bool = health_data.get("realInferenceAvailable", False)
    gdino_model_id: str  = health_data.get("groundingDinoModelId", "N/A")
    sam2_model_id: str   = health_data.get("sam2ModelId", "N/A")
    device: str          = health_data.get("device", "N/A")

    # ── Detections 파싱 ────────────────────────────────────────────────────────
    detections: list[dict] = seg_data.get("detections", [])
    warnings:   list[str]  = seg_data.get("warnings", [])
    flatten_method: str    = seg_data.get("flattenMethod", "unknown")

    product_dets = [d for d in detections if d.get("role") == "product"]
    hand_dets    = [d for d in detections if d.get("role") == "hand"]
    person_dets  = [d for d in detections if d.get("role") == "person"]

    # ── SAM2 vs bbox fallback 구분 ──────────────────────────────────────────────
    bbox_fallback_used: bool = (
        any(w in BBOX_FALLBACK_WARNINGS for w in warnings)
        or any(
            d.get("maskSource", "") in BBOX_FALLBACK_SOURCES
            for d in product_dets
        )
    )

    gdino_detected:  bool = len(product_dets) > 0
    sam2_generated:  bool = (
        gdino_detected
        and not bbox_fallback_used
        and any(
            d.get("maskSource", "") in REAL_SAM2_SOURCES
            for d in product_dets
        )
    )

    # ── 최고 점수 product detection ────────────────────────────────────────────
    best_product: dict | None = None
    if product_dets:
        best_product = max(
            product_dets,
            key=lambda d: d.get("maskQualityScore", 0.0),
        )

    ext_mask_score:   float = best_product.get("maskQualityScore", 0.0)   if best_product else 0.0
    mask_source:      str   = best_product.get("maskSource", "N/A")        if best_product else "N/A"
    edge_sharpness:   float = best_product.get("edgeSharpness", 0.0)       if best_product else 0.0
    fragment_count:   int   = best_product.get("fragmentCount", 0)          if best_product else 0
    mask_area_ratio:  float = best_product.get("maskAreaRatio", 0.0)        if best_product else 0.0
    detection_conf:   float = best_product.get("detectionConfidence", 0.0)  if best_product else 0.0
    mask_conf:        float = best_product.get("maskConfidence", 0.0)       if best_product else 0.0
    hand_subtract:    bool  = bool(best_product.get("handSubtractApplied", False)) if best_product else False
    score_breakdown:  dict  = best_product.get("scoreBreakdown", {})        if best_product else {}

    # ── Leak risk 계산 ─────────────────────────────────────────────────────────
    # 픽셀 overlap 우선 (Stage 18.1 provider가 handOverlapRatio 계산)
    # 없으면 bbox IoU fallback
    if best_product and "handOverlapRatio" in best_product:
        hand_leak_risk   = float(best_product.get("handOverlapRatio", 0.0))
        person_leak_risk = float(best_product.get("personOverlapRatio", 0.0))
    else:
        hand_leak_risk   = _compute_overlap_risk(best_product, hand_dets + person_dets)
        person_leak_risk = _compute_overlap_risk(best_product, person_dets)

    bg_leak_risk:   float = best_product.get("leakRisk", 0.0) if best_product else 0.0
    text_leak_risk: float = 0.0

    # ── Product completeness ───────────────────────────────────────────────────
    product_completeness = _score_completeness(ext_mask_score)

    # ── selectedMaskSource (compareOnly=true → native 항상 우선) ───────────────
    if not bbox_fallback_used and ext_mask_score >= MASK_SCORE_THRESHOLD:
        selected_mask_source = "external_real_sam2_available"
    else:
        selected_mask_source = "native_preferred"

    ext_mask_rejected_reason: str | None = None
    if not bbox_fallback_used and ext_mask_score < MASK_SCORE_THRESHOLD:
        ext_mask_rejected_reason = f"score_{ext_mask_score:.1f}_below_{MASK_SCORE_THRESHOLD}"
    elif bbox_fallback_used:
        ext_mask_rejected_reason = "bbox_fallback_not_real_sam2"

    # ── Segmentation verdict ────────────────────────────────────────────────────
    if not gdino_detected:
        seg_verdict = "FAIL_NO_DETECTION"
    elif bbox_fallback_used:
        seg_verdict = "PARTIAL_BBOX_FALLBACK"
    elif ext_mask_score >= MASK_SCORE_THRESHOLD:
        seg_verdict = "PASS"
    else:
        seg_verdict = f"PARTIAL_SCORE_{int(ext_mask_score)}"

    # ── Debug 이미지 생성 ──────────────────────────────────────────────────────
    images_generated: list[str] = []
    if PIL_AVAILABLE and args.image_path and os.path.exists(args.image_path):
        images_generated = _generate_debug_images(
            args.image_path, detections, args.output_dir
        )

    # ── JSON 보고서 ────────────────────────────────────────────────────────────
    report = {
        # 모델 정보
        "groundingDinoModelId":       gdino_model_id,
        "sam2ModelId":                sam2_model_id,
        "device":                     device,
        "externalModelRealInference": real_inference,
        # 입력 처리
        "flattenMethod":              flatten_method,
        # 탐지 결과
        "groundingDinoDetected":      gdino_detected,
        "groundingDinoPrompt":        "cosmetic tube . skincare product . cosmetic product . cream tube . product bottle",
        "detectionConfidence":        round(detection_conf, 4),
        "sam2MaskGenerated":          sam2_generated,
        "maskSource":                 mask_source,
        "bboxFallbackUsed":           bbox_fallback_used,
        "handSubtractApplied":        hand_subtract,
        # 품질
        "externalMaskScore":          round(ext_mask_score, 2),
        "handLeakRisk":               round(hand_leak_risk, 4),
        "personLeakRisk":             round(person_leak_risk, 4),
        "backgroundLeakRisk":         round(bg_leak_risk, 4),
        "textLeakRisk":               round(text_leak_risk, 4),
        "productCompleteness":        product_completeness,
        "edgeSharpness":              round(edge_sharpness, 4),
        "fragmentCount":              fragment_count,
        "maskAreaRatio":              round(mask_area_ratio, 4),
        "maskConfidence":             round(mask_conf, 4),
        # 점수 구성 (Stage 18.1)
        "scoreBreakdown":             score_breakdown,
        # 선택 결과
        "selectedMaskSource":         selected_mask_source,
        "externalMaskRejectedReason": ext_mask_rejected_reason,
        # 집계
        "segmentationVerdict":        seg_verdict,
        "totalDetections":            len(detections),
        "productDetections":          len(product_dets),
        "handDetections":             len(hand_dets),
        "personDetections":           len(person_dets),
        "serviceWarnings":            warnings,
        "pilAvailable":               PIL_AVAILABLE,
        "debugImagesGenerated":       images_generated,
    }

    # JSON 저장
    report_path = os.path.join(args.output_dir, "stage18-server-report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # stdout 출력 (bash script가 읽음)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    return 0


# ─── 헬퍼 함수 ─────────────────────────────────────────────────────────────────

def _compute_overlap_risk(
    product_det: dict | None,
    other_dets: list[dict],
) -> float:
    """product bbox와 hand/person bbox의 최대 IoU (bbox fallback)."""
    if not product_det or not other_dets:
        return 0.0
    pb = product_det.get("bbox", {})
    max_iou = 0.0
    for od in other_dets:
        ob = od.get("bbox", {})
        iou = _iou(pb, ob)
        max_iou = max(max_iou, iou)
    return round(min(max_iou, 1.0), 4)


def _iou(a: dict, b: dict) -> float:
    ax1, ay1 = a.get("x", 0), a.get("y", 0)
    ax2, ay2 = ax1 + a.get("width", 0), ay1 + a.get("height", 0)
    bx1, by1 = b.get("x", 0), b.get("y", 0)
    bx2, by2 = bx1 + b.get("width", 0), by1 + b.get("height", 0)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = (
        (ax2 - ax1) * (ay2 - ay1)
        + (bx2 - bx1) * (by2 - by1)
        - inter
    )
    return inter / max(union, 1)


def _score_completeness(score: float) -> str:
    if score >= 70:
        return "pass"
    if score >= 50:
        return "partial"
    if score > 0:
        return "fail"
    return "N/A"


def _generate_debug_images(
    image_path: str,
    detections: list[dict],
    output_dir: str,
) -> list[str]:
    """debug 이미지 생성. PIL 있는 경우만 실행."""
    generated = []
    try:
        img = Image.open(image_path).convert("RGB")

        # ── detections.png ───────────────────────────────────────────────────
        det_img = img.copy()
        draw = ImageDraw.Draw(det_img)
        for d in detections:
            b = d.get("bbox", {})
            color = ROLE_COLORS.get(d.get("role", ""), (200, 200, 0))
            x, y, w, h = b.get("x",0), b.get("y",0), b.get("width",0), b.get("height",0)
            draw.rectangle([x, y, x+w, y+h], outline=color, width=3)
            score = d.get("maskQualityScore", 0.0)
            conf  = d.get("detectionConfidence", 0.0)
            src   = d.get("maskSource", "?")
            sub   = " [sub]" if d.get("handSubtractApplied") else ""
            label = f"{d.get('role','')} c={conf:.2f} q={score:.0f} [{src}]{sub}"
            draw.text((x+4, y+4), label, fill=color)
        det_path = os.path.join(output_dir, "detections.png")
        det_img.save(det_path)
        generated.append("detections.png")

        # ── product mask / cutout / overlay ──────────────────────────────────
        product_dets = [d for d in detections if d.get("role") == "product"]
        if product_dets:
            best = max(product_dets, key=lambda d: d.get("maskQualityScore", 0.0))
            mask_b64 = best.get("maskPngBase64", "")
            if mask_b64:
                mask_bytes = base64.b64decode(mask_b64)
                mask_pil = Image.open(io.BytesIO(mask_bytes)).convert("L")

                mask_path = os.path.join(output_dir, "product.mask.png")
                mask_pil.save(mask_path)
                generated.append("product.mask.png")

                rgba = img.convert("RGBA")
                rgba.putalpha(
                    mask_pil.resize(img.size, Image.LANCZOS)
                    if mask_pil.size != img.size else mask_pil
                )
                cutout_path = os.path.join(output_dir, "product.cutout.png")
                rgba.save(cutout_path)
                generated.append("product.cutout.png")

                overlay = img.copy().convert("RGBA")
                tinted = Image.new("RGBA", img.size, (0, 200, 0, 80))
                mask_resized = (
                    mask_pil.resize(img.size, Image.LANCZOS)
                    if mask_pil.size != img.size else mask_pil
                )
                overlay.paste(tinted, mask=mask_resized)
                overlay_path = os.path.join(output_dir, "product.overlay.png")
                overlay.convert("RGB").save(overlay_path)
                generated.append("product.overlay.png")

    except Exception as e:
        print(f"[WARN] debug image 생성 실패: {e}", file=sys.stderr)

    return generated


# ─── 진입점 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.exit(main())
