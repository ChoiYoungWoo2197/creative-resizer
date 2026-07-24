"""Stage E P1-B: Subject-preserving outpaint transform.

Policy: BACKGROUND_SCENE_TRANSFORM=subject-preserving-outpaint

Instead of a simple cover crop (which can clip required subjects),
this module computes a contain-like source mapping that:
  1. Identifies immutable/important subject bounding boxes
  2. Selects a scale that keeps all required subjects visible in target canvas
  3. Maps source region onto target with contain semantics
  4. Returns masks for the pipeline:
     - sourceMappedRegionMask:   where source pixels appear in target space
     - newCanvasRegionMask:      empty canvas regions that need AI fill
     - allowedGenerationMask:    sourceMappedRegionMask OR newCanvasRegionMask (generation allowed here)
     - immutableMaskInTargetSpace: non-generated source pixels that must not change

Forbidden: blur, mirror, stretch fills in empty canvas regions.
Raises REQUIRED_SUBJECT_CROPPED if any required subject is clipped.
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field


@dataclass
class TransformResult:
    """Result of subject-preserving transform computation."""
    scale: float = 1.0
    offset_x: int = 0
    offset_y: int = 0
    source_w: int = 0
    source_h: int = 0
    target_w: int = 0
    target_h: int = 0
    # Masks (numpy uint8 arrays in target space, 255=active, 0=inactive)
    source_mapped_region_mask: object = None     # where source pixels land
    new_canvas_region_mask: object = None        # empty outpaint regions
    immutable_mask_in_target_space: object = None  # must-not-change pixels
    allowed_generation_mask: object = None       # AI can touch these pixels
    # Validation
    required_subjects_cropped: bool = False
    cropped_subject_ids: list = field(default_factory=list)
    background_scene_transform: str = "subject-preserving-outpaint"


class SubjectPreservingTransform:
    """Computes target-space masks for subject-preserving outpaint.

    The transform is "contain"-style: the source is scaled so all required
    subjects fit within the target, with empty regions outpainted by AI.
    """

    def compute(
        self,
        source_w: int,
        source_h: int,
        target_w: int,
        target_h: int,
        subject_bboxes: list | None = None,
        *,
        required_subject_ids: list | None = None,
    ) -> TransformResult:
        """Compute the contain mapping and target-space masks.

        Args:
            source_w, source_h:   source image dimensions
            target_w, target_h:   target canvas dimensions
            subject_bboxes:       list of dicts with keys:
                                    id, x, y, w, h, required (bool)
            required_subject_ids: IDs that must not be cropped

        Returns:
            TransformResult with all masks and validation results
        """
        # Contain scale: fit source into target without cropping anything
        scale_x = target_w / source_w if source_w > 0 else 1.0
        scale_y = target_h / source_h if source_h > 0 else 1.0
        scale = min(scale_x, scale_y)  # contain (letterbox)

        # Center the scaled source in the target canvas
        scaled_w = int(source_w * scale)
        scaled_h = int(source_h * scale)
        offset_x = (target_w - scaled_w) // 2
        offset_y = (target_h - scaled_h) // 2

        # Source mapped region mask: where scaled source pixels land
        source_mapped = np.zeros((target_h, target_w), dtype=np.uint8)
        x1 = max(0, offset_x)
        y1 = max(0, offset_y)
        x2 = min(target_w, offset_x + scaled_w)
        y2 = min(target_h, offset_y + scaled_h)
        if x2 > x1 and y2 > y1:
            source_mapped[y1:y2, x1:x2] = 255

        # New canvas region mask: everything NOT covered by source
        new_canvas = (255 - source_mapped).clip(0, 255).astype(np.uint8)

        # Allowed generation mask: source + new canvas (AI can use both)
        allowed_gen = np.where(source_mapped > 0, 255, new_canvas).astype(np.uint8)

        # Immutable mask: source-mapped region (pixels that come from source)
        # This is the same as source_mapped — pixels outside are the AI's domain
        immutable = source_mapped.copy()

        # Validate that required subjects are not cropped
        required_ids = set(required_subject_ids or [])
        cropped_ids: list[str] = []
        bboxes = subject_bboxes or []

        for bbox in bboxes:
            bid = bbox.get("id", "")
            is_req = bbox.get("required", False) or bid in required_ids
            if not is_req:
                continue
            # Check if this subject's bbox is fully visible in target space
            bx = int(bbox.get("x", 0) * scale) + offset_x
            by = int(bbox.get("y", 0) * scale) + offset_y
            bw = int(bbox.get("w", 0) * scale)
            bh = int(bbox.get("h", 0) * scale)
            bx2 = bx + bw
            by2 = by + bh
            if bx < 0 or by < 0 or bx2 > target_w or by2 > target_h:
                cropped_ids.append(bid)

        if cropped_ids:
            raise RuntimeError(
                f"REQUIRED_SUBJECT_CROPPED:"
                f" ids={cropped_ids}"
                f" scale={scale:.4f}"
                f" targetSize={target_w}x{target_h}"
            )

        return TransformResult(
            scale=scale,
            offset_x=offset_x,
            offset_y=offset_y,
            source_w=source_w,
            source_h=source_h,
            target_w=target_w,
            target_h=target_h,
            source_mapped_region_mask=source_mapped,
            new_canvas_region_mask=new_canvas,
            immutable_mask_in_target_space=immutable,
            allowed_generation_mask=allowed_gen,
            required_subjects_cropped=False,
            cropped_subject_ids=[],
            background_scene_transform="subject-preserving-outpaint",
        )


def validate_subject_not_cropped(
    subject_bboxes: list,
    source_w: int,
    source_h: int,
    scale: float,
    offset_x: int,
    offset_y: int,
    target_w: int,
    target_h: int,
) -> list[str]:
    """Check that all required subjects fit within the target canvas.

    Returns list of cropped required subject IDs (empty = all visible).
    """
    cropped = []
    for bbox in subject_bboxes:
        if not bbox.get("required", False):
            continue
        bx = int(bbox.get("x", 0) * scale) + offset_x
        by = int(bbox.get("y", 0) * scale) + offset_y
        bw = int(bbox.get("w", 0) * scale)
        bh = int(bbox.get("h", 0) * scale)
        if bx < 0 or by < 0 or (bx + bw) > target_w or (by + bh) > target_h:
            cropped.append(bbox.get("id", "unknown"))
    return cropped


def log_subject_preserving_transform(
    result: TransformResult,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [SUBJECT_PRESERVING_TRANSFORM] log."""
    source_coverage = 0.0
    new_canvas_coverage = 0.0
    if result.source_mapped_region_mask is not None:
        total = result.target_w * result.target_h
        if total > 0:
            source_coverage = float(
                (result.source_mapped_region_mask > 0).sum()
            ) / total
            new_canvas_coverage = float(
                (result.new_canvas_region_mask > 0).sum()
            ) / total
    print(
        f"[SUBJECT_PRESERVING_TRANSFORM] jobId={job_id} specId={spec_id}"
        f" scale={result.scale:.4f}"
        f" offsetX={result.offset_x} offsetY={result.offset_y}"
        f" sourceW={result.source_w} sourceH={result.source_h}"
        f" targetW={result.target_w} targetH={result.target_h}"
        f" sourceMappedCoverage={source_coverage:.4f}"
        f" newCanvasCoverage={new_canvas_coverage:.4f}"
        f" requiredSubjectsCropped={result.required_subjects_cropped}"
        f" transform={result.background_scene_transform!r}",
        flush=True,
    )
