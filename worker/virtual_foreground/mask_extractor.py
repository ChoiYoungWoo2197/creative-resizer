"""D-2: Extract virtual foreground objects via paired difference masking.

Source image vs source-aligned reference → luminance+chroma difference
→ adaptive threshold → alpha mask → RGBA cutout at bbox size.

Numpy/Pillow only — no OpenCV dependency.

Spec Section 11: paired difference extraction.
Spec Section 36: FORBIDDEN — opaque bbox crop as virtual layer.
"""
from __future__ import annotations

import hashlib

import numpy as np
from PIL import Image

_MIN_ALPHA_COVERAGE = 0.02   # at least 2% of bbox must be foreground
_MAX_ALPHA_COVERAGE = 0.97   # at most 97% opaque (else opaque bbox crop suspected)
_BASE_THRESHOLD = 15.0       # minimum difference threshold
_MAX_THRESHOLD = 45.0        # adaptive threshold cap
_DEFAULT_PADDING = 8         # pixels added around bbox for better edge capture


def extract_object_mask(
    source_image: Image.Image,
    reference_image: Image.Image,
    bbox: dict,
    padding: int = _DEFAULT_PADDING,
    job_id: str = "",
    detection_id: str = "",
) -> tuple[Image.Image | None, dict]:
    """Extract RGBA cutout via paired source/reference difference.

    Args:
        source_image: Full advertisement composite (RGB or RGBA)
        reference_image: Source-aligned clean reference at same size (RGB or RGBA)
        bbox: {x, y, width, height} in source pixel coordinates
        padding: Extra pixels around bbox to capture edges

    Returns:
        (rgba_image, metrics_dict) on success  — rgba at bbox size
        (None, error_dict)         on failure
    """
    if source_image.size != reference_image.size:
        return None, {
            "error": "SIZE_MISMATCH",
            "sourceSize": list(source_image.size),
            "refSize": list(reference_image.size),
        }

    src_w, src_h = source_image.size
    x = int(bbox.get("x", 0))
    y = int(bbox.get("y", 0))
    w = int(bbox.get("width", 0))
    h = int(bbox.get("height", 0))

    if w <= 0 or h <= 0:
        return None, {"error": "INVALID_BBOX", "bbox": bbox}

    # Padded crop region clamped to image bounds
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(src_w, x + w + padding)
    y2 = min(src_h, y + h + padding)
    crop_w = x2 - x1
    crop_h = y2 - y1

    if crop_w <= 0 or crop_h <= 0:
        return None, {"error": "CROP_ZERO_SIZE"}

    src_crop = source_image.crop((x1, y1, x2, y2)).convert("RGB")
    ref_crop = reference_image.crop((x1, y1, x2, y2)).convert("RGB")

    src_arr = np.array(src_crop, dtype=np.float32)
    ref_arr = np.array(ref_crop, dtype=np.float32)

    # Luminance difference (ITU-R BT.601 weights)
    lum_weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    src_lum = (src_arr * lum_weights).sum(axis=2)
    ref_lum = (ref_arr * lum_weights).sum(axis=2)
    lum_diff = np.abs(src_lum - ref_lum)

    # Chroma difference (Euclidean / sqrt(3) to normalize to [0, 255])
    chroma_diff = np.sqrt(np.sum((src_arr - ref_arr) ** 2, axis=2)) / 1.7321

    # Combined: weighted max of luminance and chroma
    combined = np.maximum(lum_diff, chroma_diff * 0.7)

    # Adaptive threshold: mean + 1σ, clamped to [base, max]
    mean_val = float(combined.mean())
    std_val = float(combined.std())
    threshold = float(min(max(_BASE_THRESHOLD, mean_val + std_val), _MAX_THRESHOLD))

    # Binary mask in padded region
    raw_alpha_padded = (combined > threshold).astype(np.uint8) * 255

    # Crop alpha back to original bbox region (remove padding)
    bbox_x_in_crop = x - x1
    bbox_y_in_crop = y - y1
    bbox_x2_in_crop = bbox_x_in_crop + w
    bbox_y2_in_crop = bbox_y_in_crop + h

    raw_alpha_bbox = raw_alpha_padded[
        bbox_y_in_crop:bbox_y2_in_crop,
        bbox_x_in_crop:bbox_x2_in_crop,
    ].copy()

    # Guard: ensure shape matches bbox
    if raw_alpha_bbox.shape != (h, w):
        actual_h, actual_w = raw_alpha_bbox.shape
        if actual_h == 0 or actual_w == 0:
            return None, {"error": "ALPHA_CROP_ZERO_SHAPE",
                          "shape": list(raw_alpha_bbox.shape)}
        # Accept mismatched shape — resize alpha to match bbox
        alpha_pil = Image.fromarray(raw_alpha_bbox, mode="L").resize((w, h), Image.NEAREST)
        raw_alpha_bbox = np.array(alpha_pil)

    bbox_total = w * h
    alpha_total = int(np.sum(raw_alpha_bbox > 0))
    alpha_coverage = alpha_total / bbox_total if bbox_total > 0 else 0.0

    metrics: dict = {
        "threshold": round(threshold, 3),
        "diffMean": round(mean_val, 3),
        "diffStd": round(std_val, 3),
        "alphaCoverageRatio": round(alpha_coverage, 4),
        "bboxTotal": bbox_total,
        "alphaTotal": alpha_total,
    }

    # Spec Section 36: FORBIDDEN — opaque bbox crop as virtual layer
    if alpha_coverage >= _MAX_ALPHA_COVERAGE:
        metrics["error"] = "OPAQUE_BBOX_DETECTED"
        return None, metrics

    # Empty mask — no foreground detected
    if alpha_coverage < _MIN_ALPHA_COVERAGE:
        metrics["error"] = "ALPHA_TOO_LOW"
        return None, metrics

    # Border alpha ratio — heuristic for edge contamination
    if h >= 4 and w >= 4:
        top = int(np.sum(raw_alpha_bbox[0, :] > 0))
        bot = int(np.sum(raw_alpha_bbox[-1, :] > 0))
        lft = int(np.sum(raw_alpha_bbox[:, 0] > 0))
        rgt = int(np.sum(raw_alpha_bbox[:, -1] > 0))
        border_total = top + bot + lft + rgt
        border_ratio = border_total / max(1, alpha_total)
    else:
        border_ratio = 0.0
    metrics["borderAlphaRatio"] = round(border_ratio, 4)

    # Build RGBA at bbox size
    src_bbox_crop = source_image.crop((x, y, x + w, y + h)).convert("RGBA")
    alpha_img = Image.fromarray(raw_alpha_bbox, mode="L")
    if alpha_img.size != (w, h):
        alpha_img = alpha_img.resize((w, h), Image.NEAREST)
    src_bbox_crop.putalpha(alpha_img)

    # Provenance hashes
    try:
        mask_sha = hashlib.sha256(raw_alpha_bbox.tobytes()).hexdigest()
        pixel_sha = hashlib.sha256(np.array(src_bbox_crop).tobytes()).hexdigest()
    except Exception:
        mask_sha = ""
        pixel_sha = ""

    metrics["maskSha256"] = mask_sha
    metrics["pixelSha256"] = pixel_sha

    print(
        f"[D2_MASK_EXTRACT]"
        f" detId={detection_id} jobId={job_id}"
        f" bbox={x},{y} {w}x{h}"
        f" threshold={threshold:.1f}"
        f" coverage={alpha_coverage:.3f}"
        f" border={border_ratio:.3f}",
        flush=True,
    )

    return src_bbox_crop, metrics
