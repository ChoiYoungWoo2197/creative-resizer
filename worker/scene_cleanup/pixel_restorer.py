"""Stage E P0-B: Default immutable pixel policy enforcer.

Applies canonical pixel restoration outside the allowed generation mask.

Policy:
  allowedGenerationMask = resolvedRemovalMask OR newCanvasRegionMask

  - Inside allowedGenerationMask: use AI result pixels
  - Outside allowedGenerationMask (immutable region): restore canonical original pixels
  - Human/hand detection failure never permits change outside removal mask

This ensures that even if the AI model regenerates the whole scene, only
removal/outpaint regions actually change in the final output.
"""
from __future__ import annotations

import numpy as np


def apply_default_immutable_policy(
    canonical_img: object,
    ai_result: object,
    allowed_generation_mask_arr: object | None,
) -> object:
    """Restore canonical pixels everywhere outside the allowed generation mask.

    Args:
        canonical_img:               PIL Image — canonical original source
        ai_result:                   PIL Image — AI-generated scene plate
        allowed_generation_mask_arr: numpy uint8 array (H, W), 255=allowed, 0=immutable
                                     If None, a full-white mask is used (all pixels allowed)
                                     and the AI result is returned as-is.

    Returns:
        PIL Image — composite with:
          - allowed mask region: AI result pixels
          - immutable region:    canonical original pixels
    """
    from PIL import Image

    if canonical_img is None or ai_result is None:
        return ai_result

    if not isinstance(canonical_img, Image.Image) or not isinstance(ai_result, Image.Image):
        return ai_result

    cw, ch = canonical_img.size
    rw, rh = ai_result.size

    # Fail-closed: size mismatch means canonical_at_target was not built correctly.
    # Callers must pass build_canonical_at_target() output, not the raw source image.
    if (cw, ch) != (rw, rh):
        raise RuntimeError(
            f"PIXEL_RESTORE_CANONICAL_SIZE_MISMATCH:"
            f" canonical={cw}x{ch} result={rw}x{rh}"
            f" — pass build_canonical_at_target(source, canvas_transform) as canonical_img"
        )

    # If no mask provided: all pixels are in allowed region (legacy behavior)
    if allowed_generation_mask_arr is None:
        return ai_result

    mask = _normalize_mask(allowed_generation_mask_arr, cw, ch)
    if mask is None:
        return ai_result

    canonical_arr = np.array(canonical_img.convert("RGB"), dtype=np.uint8)
    ai_arr = np.array(ai_result.convert("RGB"), dtype=np.uint8)

    # mask: 255 = allowed (AI), 0 = immutable (canonical)
    # Expand mask to (H, W, 1) for broadcasting
    alpha = (mask > 127).astype(np.uint8)[:, :, np.newaxis]  # (H, W, 1)

    # composite = ai * alpha + canonical * (1 - alpha)
    composite = (ai_arr.astype(np.float32) * alpha
                 + canonical_arr.astype(np.float32) * (1 - alpha))
    composite = composite.clip(0, 255).astype(np.uint8)

    mode = ai_result.mode if ai_result.mode in ("RGB", "RGBA") else "RGB"
    return Image.fromarray(composite, "RGB").convert(mode)


def compute_immutable_metrics(
    canonical_img: object,
    ai_result: object,
    allowed_mask: object | None,
    *,
    change_threshold: int = 10,
) -> dict:
    """Compute pixel-level integrity metrics for the immutable policy.

    Returns dict with:
      allowedGenerationCoverage:        fraction of pixels in allowed (generation) region
      outsideAllowedChangedPixelRatio:  fraction of immutable pixels that AI changed
      restoredOriginalPixelCount:       absolute count of pixels that were restored
    """
    from PIL import Image

    empty = {
        "allowedGenerationCoverage": 1.0,
        "outsideAllowedChangedPixelRatio": 0.0,
        "restoredOriginalPixelCount": 0,
    }
    if canonical_img is None or ai_result is None:
        return empty
    if not isinstance(canonical_img, Image.Image) or not isinstance(ai_result, Image.Image):
        return empty

    cw, ch = canonical_img.size
    rw, rh = ai_result.size
    if (cw, ch) != (rw, rh):
        raise RuntimeError(
            f"PIXEL_METRICS_CANONICAL_SIZE_MISMATCH:"
            f" canonical={cw}x{ch} result={rw}x{rh}"
            f" — pass build_canonical_at_target(source, canvas_transform) as canonical_img"
        )
    if allowed_mask is None:
        return empty

    mask = _normalize_mask(allowed_mask, cw, ch)
    if mask is None:
        return empty

    canonical_arr = np.array(canonical_img.convert("RGB"), dtype=np.int32)
    ai_arr = np.array(ai_result.convert("RGB"), dtype=np.int32)

    allowed_bool = mask > 127     # True = allowed (generation region)
    immutable_bool = ~allowed_bool  # True = immutable

    total_pixels = cw * ch
    allowed_count = int(allowed_bool.sum())
    immutable_count = int(immutable_bool.sum())

    # Pixel change in immutable region
    delta = np.abs(canonical_arr - ai_arr)
    changed = np.any(delta > change_threshold, axis=2)   # (H, W)
    changed_in_immutable = int((changed & immutable_bool).sum())

    outside_changed_ratio = (
        changed_in_immutable / immutable_count
        if immutable_count > 0 else 0.0
    )

    return {
        "allowedGenerationCoverage": allowed_count / total_pixels if total_pixels > 0 else 1.0,
        "outsideAllowedChangedPixelRatio": outside_changed_ratio,
        "restoredOriginalPixelCount": changed_in_immutable,
    }


def log_default_immutable_policy(
    metrics: dict,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [DEFAULT_IMMUTABLE_POLICY] log."""
    print(
        f"[DEFAULT_IMMUTABLE_POLICY] jobId={job_id} specId={spec_id}"
        f" allowedGenerationCoverage={metrics.get('allowedGenerationCoverage', 1.0):.4f}"
        f" outsideAllowedChangedPixelRatio={metrics.get('outsideAllowedChangedPixelRatio', 0.0):.4f}"
        f" restoredOriginalPixelCount={metrics.get('restoredOriginalPixelCount', 0)}",
        flush=True,
    )


# ── Internal helpers ─────────────────────────────────────────────────────────

def _normalize_mask(mask_input: object, expected_w: int, expected_h: int) -> object | None:
    """Convert mask to 2D uint8 numpy array (H, W), or None on failure."""
    if mask_input is None:
        return None

    try:
        import numpy as np
        from PIL import Image

        if isinstance(mask_input, Image.Image):
            m = np.array(mask_input.convert("L"), dtype=np.uint8)
        elif isinstance(mask_input, np.ndarray):
            m = mask_input.astype(np.uint8)
            if m.ndim == 3:
                m = m[:, :, 0]
        else:
            return None

        # Ensure correct shape
        mh, mw = m.shape[:2]
        if (mw, mh) != (expected_w, expected_h):
            from PIL import Image as _PIL
            mask_img = _PIL.fromarray(m, "L").resize((expected_w, expected_h), _PIL.NEAREST)
            m = np.array(mask_img, dtype=np.uint8)

        return m
    except Exception:
        return None
