"""Unified Pipeline V2: data structures for GPT analysis results and internal manifest."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class V2DetectedObject:
    """One ad object detected by GPT Vision in the canonical source image."""
    object_id: str
    role: str               # product/title/headline/body_text/logo/cta/badge/decorative/human_subject
    bbox: dict              # {x, y, width, height} in source pixel coords
    polygon: list           # [[x, y], ...] in source pixel coords; may be empty
    text_content: str       # visible text inside object; empty for non-text roles
    confidence: float       # 0.0–1.0


@dataclass
class V2FgLayer:
    """RGBA-extracted foreground layer ready for compositing."""
    object_id: str
    role: str
    image: object           # PIL.Image RGBA at bbox size
    source_bbox: dict       # original source coords {x, y, width, height}
    bbox: dict              # target canvas coords; updated by layout engine
    mask_sha256: str
    pixel_sha256: str


@dataclass
class V2Manifest:
    """Job-level manifest: canonical source info + per-object extraction results."""
    canonical_sha: str
    source_w: int
    source_h: int
    gpt_model: str
    detected_objects: list = field(default_factory=list)  # list[V2DetectedObject]
    fg_layers: list = field(default_factory=list)         # list[V2FgLayer]
    detection_count: int = 0
    extraction_count: int = 0
