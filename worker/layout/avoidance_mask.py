"""Stage E P1-C: Subject avoidance mask for layout placement.

Generates avoidance regions from human/face/hand bounding boxes so that
CTA/title layout placement does not occlude important subjects.

Hard fail conditions:
  REQUIRED_SUBJECT_OCCLUDED:  placed bbox fully covers a required subject
  FACE_OCCLUSION_EXCEEDED:    overlap with face bbox exceeds threshold
  HAND_OCCLUSION_EXCEEDED:    overlap with hand bbox exceeds threshold

Logs:
  [SUBJECT_AVOIDANCE_MASK] with coverage metrics
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field


@dataclass
class OcclusionCheckResult:
    """Result of an occlusion validation check."""
    passed: bool = True
    reason_code: str = ""
    overlap_ratio: float = 0.0
    placed_bbox: dict = field(default_factory=dict)
    avoidance_bbox: dict = field(default_factory=dict)


class SubjectAvoidanceMask:
    """Generates and validates avoidance regions for layout placement.

    Default occlusion thresholds:
      face_threshold: 0.10  (10% overlap with face → fail)
      hand_threshold: 0.15  (15% overlap with hand → fail)
    """

    def __init__(
        self,
        canvas_w: int = 1,
        canvas_h: int = 1,
        face_threshold: float = 0.10,
        hand_threshold: float = 0.15,
    ):
        self.canvas_w = canvas_w
        self.canvas_h = canvas_h
        self.face_threshold = face_threshold
        self.hand_threshold = hand_threshold

    def build_avoidance_mask(
        self,
        avoidance_bboxes: list,
        *,
        margin: int = 10,
    ) -> object:
        """Build a binary avoidance mask from subject bboxes.

        Args:
            avoidance_bboxes: list of dicts with x, y, w, h, type
            margin:           extra margin around each bbox (pixels)

        Returns:
            numpy uint8 array (H, W), 255=avoid, 0=safe
        """
        mask = np.zeros((self.canvas_h, self.canvas_w), dtype=np.uint8)
        for bbox in avoidance_bboxes:
            x = max(0, int(bbox.get("x", 0)) - margin)
            y = max(0, int(bbox.get("y", 0)) - margin)
            w = int(bbox.get("w", 0)) + 2 * margin
            h = int(bbox.get("h", 0)) + 2 * margin
            x2 = min(self.canvas_w, x + w)
            y2 = min(self.canvas_h, y + h)
            if x2 > x and y2 > y:
                mask[y:y2, x:x2] = 255
        return mask

    def validate_subject_occlusion(
        self,
        placed_bbox: dict,
        avoidance_bboxes: list,
        *,
        threshold: float | None = None,
    ) -> OcclusionCheckResult:
        """Check if placed_bbox occludes any avoidance bbox above threshold.

        Args:
            placed_bbox:      dict with x, y, w, h of the placed element
            avoidance_bboxes: list of dicts with x, y, w, h, type
            threshold:        override threshold (uses face/hand defaults otherwise)

        Returns:
            OcclusionCheckResult — passed=False raises in caller
        """
        px = int(placed_bbox.get("x", 0))
        py = int(placed_bbox.get("y", 0))
        pw = int(placed_bbox.get("w", 0))
        ph = int(placed_bbox.get("h", 0))
        px2 = px + pw
        py2 = py + ph

        for avoidance in avoidance_bboxes:
            ax = int(avoidance.get("x", 0))
            ay = int(avoidance.get("y", 0))
            aw = int(avoidance.get("w", 0))
            ah = int(avoidance.get("h", 0))
            ax2 = ax + aw
            ay2 = ay + ah
            atype = avoidance.get("type", "subject")

            # Compute intersection
            ix1 = max(px, ax)
            iy1 = max(py, ay)
            ix2 = min(px2, ax2)
            iy2 = min(py2, ay2)
            if ix2 <= ix1 or iy2 <= iy1:
                continue  # no overlap

            intersection = (ix2 - ix1) * (iy2 - iy1)
            avoidance_area = aw * ah if aw * ah > 0 else 1
            overlap_ratio = intersection / avoidance_area

            # Determine threshold for this bbox type
            if threshold is not None:
                t = threshold
                reason = "REQUIRED_SUBJECT_OCCLUDED" if overlap_ratio > t else ""
            elif atype == "face":
                t = self.face_threshold
                reason = "FACE_OCCLUSION_EXCEEDED"
            elif atype == "hand":
                t = self.hand_threshold
                reason = "HAND_OCCLUSION_EXCEEDED"
            else:
                t = self.face_threshold
                reason = "REQUIRED_SUBJECT_OCCLUDED"

            if overlap_ratio > t:
                return OcclusionCheckResult(
                    passed=False,
                    reason_code=reason,
                    overlap_ratio=overlap_ratio,
                    placed_bbox=placed_bbox,
                    avoidance_bbox=avoidance,
                )

        return OcclusionCheckResult(passed=True)


def log_avoidance_mask(
    mask: object,
    avoidance_bboxes: list,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [SUBJECT_AVOIDANCE_MASK] log."""
    coverage = 0.0
    if mask is not None and isinstance(mask, np.ndarray):
        total = mask.size
        if total > 0:
            coverage = float((mask > 0).sum()) / total
    print(
        f"[SUBJECT_AVOIDANCE_MASK] jobId={job_id} specId={spec_id}"
        f" avoidanceBboxCount={len(avoidance_bboxes)}"
        f" avoidanceCoverage={coverage:.4f}",
        flush=True,
    )
