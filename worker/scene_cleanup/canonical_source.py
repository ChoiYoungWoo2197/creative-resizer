"""Stage E-1: Canonical Source Image — single semantic authority for all input formats.

PSD/PNG/JPG are all normalized to a CanonicalSourceImage before any
semantic decision. PSD layer information is never used as semantic authority.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class CanonicalSourceImage:
    """Normalized full-image representation used as the sole semantic authority.

    Regardless of input format (PSD/PNG/JPG), all downstream decisions
    (D-2 extraction, scene cleanup, foreground compositing) operate on this.
    PSD layer hierarchy is explicitly excluded from the semantic path.
    """
    image: object                    # PIL.Image.Image
    width: int
    height: int
    source_type: str                 # "psd", "png", "jpg", "image"
    source_file_sha256: str
    canonical_image_sha256: str
    original_filename: str
    input_format: str                # file extension or detected format
    semantic_authority: str = "full_image"
    psd_layer_authority: bool = False
    pipeline_policy: str = "full-image-semantic-v1"
    input_normalization_version: str = "canonical-source-v1"


def build_canonical_source(
    image: object,
    source_type: str,
    source_file_sha256: str,
    original_filename: str,
    input_format: str = "",
) -> CanonicalSourceImage:
    """Build a CanonicalSourceImage from a loaded PIL image."""
    from PIL import Image as _PIL
    _img = image
    _sha = _sha256_image(_img)
    return CanonicalSourceImage(
        image=_img,
        width=_img.width,
        height=_img.height,
        source_type=source_type,
        source_file_sha256=source_file_sha256,
        canonical_image_sha256=_sha,
        original_filename=original_filename,
        input_format=input_format or source_type,
        semantic_authority="full_image",
        psd_layer_authority=False,
        pipeline_policy="full-image-semantic-v1",
        input_normalization_version="canonical-source-v1",
    )


def _sha256_image(img: object) -> str:
    """SHA-256 hex of RGBA pixel bytes."""
    from PIL import Image
    if img is None or not isinstance(img, Image.Image):
        return ""
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    return hashlib.sha256(rgba.tobytes()).hexdigest()[:16]


def log_canonical_source(cs: CanonicalSourceImage, job_id: str = "") -> None:
    print(
        f"[CANONICAL_SOURCE] jobId={job_id}"
        f" inputFormat={cs.input_format!r}"
        f" sourceType={cs.source_type!r}"
        f" sourceSize={cs.width}x{cs.height}"
        f" sourceFileSha256={cs.source_file_sha256[:16]}"
        f" canonicalImageSha256={cs.canonical_image_sha256}"
        f" semanticAuthority={cs.semantic_authority}"
        f" psdLayerAuthorityUsed=false"
        f" pipelinePolicy={cs.pipeline_policy}",
        flush=True,
    )


def log_psd_layer_authority(
    job_id: str = "",
    enabled: bool = False,
    runtime_decision_count: int = 0,
) -> None:
    print(
        f"[PSD_LAYER_AUTHORITY] jobId={job_id}"
        f" enabled={str(enabled).lower()}"
        f" runtimeDecisionCount={runtime_decision_count}",
        flush=True,
    )
