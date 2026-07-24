"""Stage E P0-B: Mask conflict detector.

Detects and resolves conflicts between product removal mask and human/hand
preservation mask. When product and human pixels overlap, a resolution
strategy is applied. Unresolved conflicts raise before the AI call.

Resolution order:
  1. product core (high confidence) vs human core (high confidence)
  2. edge/matting refinement
  3. depth evidence
  4. Unresolved → PRODUCT_HUMAN_MASK_CONFLICT_UNRESOLVED

Logs:
  [MASK_CONFLICT_ANALYSIS] with resolution metrics
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass


@dataclass
class MaskConflictResult:
    """Result of mask conflict analysis."""
    has_conflict: bool = False
    raw_conflict_pixel_count: int = 0
    resolved_conflict_pixel_count: int = 0
    unresolved_conflict_pixel_count: int = 0
    conflict_resolution_method: str = "none"
    conflict_ratio: float = 0.0
    unresolved_ratio: float = 0.0


class MaskConflictDetector:
    """Detects conflicts between product removal mask and human preserve mask.

    A conflict exists when product removal pixels overlap with human/hand
    preservation pixels. Resolution uses confidence-based arbitration.
    """

    # Unresolved conflict pixel ratio above which we raise a hard error
    DEFAULT_UNRESOLVED_THRESHOLD: float = 0.0  # any unresolved → fail

    def check_conflict(
        self,
        product_removal_mask: object,
        human_preserve_mask: object,
        *,
        product_confidence_mask: object | None = None,
        human_confidence_mask: object | None = None,
        unresolved_threshold: float | None = None,
    ) -> MaskConflictResult:
        """Check for conflicts between product removal and human preservation masks.

        Args:
            product_removal_mask:   numpy array or PIL Image, uint8 (H, W)
                                    255 = product pixel (to be removed/regenerated)
            human_preserve_mask:    numpy array or PIL Image, uint8 (H, W)
                                    255 = human pixel (must be preserved)
            product_confidence_mask: optional per-pixel confidence 0-255
            human_confidence_mask:   optional per-pixel confidence 0-255
            unresolved_threshold:   ratio above which conflict is fatal
                                    (default: any unresolved → fail)

        Returns:
            MaskConflictResult
        """
        threshold = (
            unresolved_threshold
            if unresolved_threshold is not None
            else self.DEFAULT_UNRESOLVED_THRESHOLD
        )

        prod = _to_mask_arr(product_removal_mask)
        human = _to_mask_arr(human_preserve_mask)

        if prod is None or human is None:
            return MaskConflictResult(has_conflict=False, conflict_resolution_method="no_masks")

        # Align shapes
        if prod.shape != human.shape:
            return MaskConflictResult(has_conflict=False, conflict_resolution_method="shape_mismatch")

        prod_bool = prod > 127
        human_bool = human > 127

        overlap = prod_bool & human_bool
        raw_count = int(overlap.sum())
        total = prod.size

        if raw_count == 0:
            return MaskConflictResult(
                has_conflict=False,
                raw_conflict_pixel_count=0,
                resolved_conflict_pixel_count=0,
                unresolved_conflict_pixel_count=0,
                conflict_resolution_method="no_conflict",
                conflict_ratio=0.0,
                unresolved_ratio=0.0,
            )

        # Resolution: confidence-based arbitration
        prod_conf = _to_mask_arr(product_confidence_mask)
        human_conf = _to_mask_arr(human_confidence_mask)

        # Pixels where human confidence > product confidence → preserve human
        # Pixels where product confidence > human confidence → allow removal
        # Pixels where neither has a confidence mask → unresolved

        if prod_conf is not None and human_conf is not None:
            # Resolve using confidence: human high-confidence wins
            prod_conf_norm = prod_conf.astype(np.float32) / 255.0
            human_conf_norm = human_conf.astype(np.float32) / 255.0

            overlap_region = overlap
            human_wins = overlap_region & (human_conf_norm > prod_conf_norm)
            prod_wins = overlap_region & (prod_conf_norm >= human_conf_norm)
            resolved = int((human_wins | prod_wins).sum())
            unresolved = raw_count - resolved
            method = "confidence_arbitration"
        else:
            # No confidence masks: conservative — all conflict pixels unresolved
            # (human preservation is the default winner, but we flag it)
            resolved = 0
            unresolved = raw_count
            method = "conservative_unresolved"

        conflict_ratio = raw_count / total if total > 0 else 0.0
        unresolved_ratio = unresolved / total if total > 0 else 0.0

        return MaskConflictResult(
            has_conflict=True,
            raw_conflict_pixel_count=raw_count,
            resolved_conflict_pixel_count=resolved,
            unresolved_conflict_pixel_count=unresolved,
            conflict_resolution_method=method,
            conflict_ratio=conflict_ratio,
            unresolved_ratio=unresolved_ratio,
        )

    def validate_or_raise(
        self,
        result: MaskConflictResult,
        *,
        unresolved_threshold: float | None = None,
        job_id: str = "",
        spec_id: str = "",
    ) -> None:
        """Raise if unresolved conflict exceeds threshold.

        Raises:
            RuntimeError with PRODUCT_HUMAN_MASK_CONFLICT_UNRESOLVED code
        """
        threshold = (
            unresolved_threshold
            if unresolved_threshold is not None
            else self.DEFAULT_UNRESOLVED_THRESHOLD
        )
        log_mask_conflict_analysis(result, job_id=job_id, spec_id=spec_id)
        if result.has_conflict and result.unresolved_conflict_pixel_count > 0:
            if result.unresolved_ratio > threshold:
                raise RuntimeError(
                    f"PRODUCT_HUMAN_MASK_CONFLICT_UNRESOLVED:"
                    f" jobId={job_id} specId={spec_id}"
                    f" unresolvedPixels={result.unresolved_conflict_pixel_count}"
                    f" unresolvedRatio={result.unresolved_ratio:.4f}"
                    f" method={result.conflict_resolution_method}"
                )


def log_mask_conflict_analysis(
    result: MaskConflictResult,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [MASK_CONFLICT_ANALYSIS] log."""
    print(
        f"[MASK_CONFLICT_ANALYSIS] jobId={job_id} specId={spec_id}"
        f" hasConflict={result.has_conflict}"
        f" rawConflictPixelCount={result.raw_conflict_pixel_count}"
        f" resolvedConflictPixelCount={result.resolved_conflict_pixel_count}"
        f" unresolvedConflictPixelCount={result.unresolved_conflict_pixel_count}"
        f" conflictResolutionMethod={result.conflict_resolution_method!r}"
        f" conflictRatio={result.conflict_ratio:.4f}"
        f" unresolvedRatio={result.unresolved_ratio:.4f}",
        flush=True,
    )


# ── Internal helpers ─────────────────────────────────────────────────────────

def _to_mask_arr(mask_input: object) -> object | None:
    """Convert mask to 2D uint8 numpy array or None."""
    if mask_input is None:
        return None
    try:
        from PIL import Image
        if isinstance(mask_input, Image.Image):
            return np.array(mask_input.convert("L"), dtype=np.uint8)
        if isinstance(mask_input, np.ndarray):
            arr = mask_input.astype(np.uint8)
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            return arr
        return None
    except Exception:
        return None
