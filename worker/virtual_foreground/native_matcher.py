"""D-2: Match virtual detections against native PSD layers (native-first policy).

Spec Section 10: if a virtual detection overlaps a native layer by >= IoU threshold,
the virtual detection is skipped. Native layers always win.
"""
from __future__ import annotations

from virtual_foreground.models import FlattenedObjectDetection

_DEFAULT_IOU_THRESHOLD = 0.50


def _iou(a: dict, b: dict) -> float:
    """Compute Intersection-over-Union for two {x, y, width, height} bboxes."""
    ax1 = a.get("x", 0)
    ay1 = a.get("y", 0)
    ax2 = ax1 + a.get("width", 0)
    ay2 = ay1 + a.get("height", 0)

    bx1 = b.get("x", 0)
    by1 = b.get("y", 0)
    bx2 = bx1 + b.get("width", 0)
    by2 = by1 + b.get("height", 0)

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter = inter_w * inter_h

    if inter == 0:
        return 0.0

    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def filter_virtual_detections(
    detections: list[FlattenedObjectDetection],
    native_layers: list[dict],
    iou_threshold: float = _DEFAULT_IOU_THRESHOLD,
    job_id: str = "",
) -> tuple[list[FlattenedObjectDetection], list[dict]]:
    """Remove virtual detections that overlap native PSD layers.

    Returns (kept_detections, match_logs).
    Native layers always win — detections with IoU >= threshold are dropped.
    """
    if not native_layers:
        return list(detections), []

    native_bboxes: list[tuple[dict, str]] = [
        (layer.get("bbox") or {}, layer.get("role", ""))
        for layer in native_layers
    ]

    kept: list[FlattenedObjectDetection] = []
    logs: list[dict] = []

    for det in detections:
        max_iou = 0.0
        matched_role = ""
        for nb, nr in native_bboxes:
            iou = _iou(det.bbox, nb)
            if iou > max_iou:
                max_iou = iou
                matched_role = nr

        skipped = max_iou >= iou_threshold
        if skipped:
            print(
                f"[D2_NATIVE_MATCH] SKIP detId={det.detection_id}"
                f" role={det.semantic_role} iou={max_iou:.3f}"
                f" matchedNativeRole={matched_role} jobId={job_id}",
                flush=True,
            )
        else:
            kept.append(det)

        logs.append({
            "detection_id": det.detection_id,
            "semantic_role": det.semantic_role,
            "skipped": skipped,
            "reason": "NATIVE_LAYER_OVERLAP" if skipped else "",
            "maxIou": round(max_iou, 3),
            "matchedNativeRole": matched_role,
        })

    return kept, logs
