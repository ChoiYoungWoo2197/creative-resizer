"""Bundle D-1: Data models for full-image semantic scene cleanup."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# Background generation mode constants
SOURCE_FAITHFUL_REPAIR = "source_faithful_repair"
SEMANTIC_SCENE_CLEANUP = "semantic_scene_cleanup"

# Mask strategy
MASK_STRATEGY_FULL_CANVAS = "full_canvas_semantic"
MASK_STRATEGY_NONE = "none"

# Transform strategy
TRANSFORM_STRATEGY_COVER_CROP = "cover_crop"

# Provider input source
PROVIDER_INPUT_FULL_COMPOSITE = "full_composite"
PROVIDER_INPUT_BACKGROUND_PLATE = "background_plate"


@dataclass
class FullImageSource:
    """Full advertisement composite as Provider input — NOT background-plate-only."""
    image: object          # PIL Image — not serialized
    source_path: str
    source_type: str       # "psd" | "png" | "jpg" | "unknown"
    source_file_sha256: str
    composite_sha256: str
    width: int
    height: int
    has_native_layers: bool
    composite_render_method: str  # "psd_composite" | "png" | "jpg" | ...


@dataclass
class SceneCanvasTransform:
    """Deterministic mapping: source → provider_input → target.

    Single unified coordinate system — no split BG/FG scales.
    cover_crop: source scaled to fill target exactly, center-cropped.
    outpaint_required: always False for cover_crop (fills without gaps).
    """
    strategy: str = TRANSFORM_STRATEGY_COVER_CROP
    source_w: int = 0
    source_h: int = 0
    canvas_w: int = 0   # = target_w
    canvas_h: int = 0   # = target_h
    scale: float = 1.0  # uniform scale applied to source
    crop_x: int = 0     # left offset in scaled-source coordinates
    crop_y: int = 0     # top offset in scaled-source coordinates
    outpaint_required: bool = False
    mask_strategy: str = MASK_STRATEGY_FULL_CANVAS


@dataclass
class SemanticSceneCleanupResult:
    """Result of run_semantic_scene_cleanup() — no PIL Image in serialized form."""
    success: bool
    failure_reason: str = ""
    # Provider provenance
    provider_name: str = ""
    provider_model: str = ""
    provider_input_source: str = PROVIDER_INPUT_FULL_COMPOSITE
    prompt_version: str = ""
    prompt_sha256: str = ""
    # Scene plate provenance
    scene_plate_sha256: str = ""
    scene_plate_image: object = None   # PIL Image — excluded from serialization
    scene_plate_path: str = ""
    # Canvas transform
    canvas_transform: Optional[SceneCanvasTransform] = None
    # Execution counters
    attempt_count: int = 0
    actual_provider_request_count: int = 0
    # Flattened input flag (D-2 not implemented)
    d2_required: bool = False
    d2_reason: str = ""
    # Dimensions
    source_w: int = 0
    source_h: int = 0
    target_w: int = 0
    target_h: int = 0
