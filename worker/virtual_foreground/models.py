"""Stage 21 Bundle D-2: Virtual foreground extraction data models."""
from __future__ import annotations

from dataclasses import dataclass, field

# Semantic role constants
ROLE_PRODUCT = "product"
ROLE_TITLE = "title"
ROLE_HEADLINE = "headline"
ROLE_BODY_TEXT = "body_text"
ROLE_LOGO = "logo"
ROLE_CTA = "cta"
ROLE_BADGE = "badge"
ROLE_DECORATIVE = "decorative"
ROLE_HUMAN_SUBJECT = "human_subject"

# Roles eligible for virtual extraction (spec Section 12)
EXTRACTABLE_ROLES = frozenset({
    ROLE_PRODUCT, ROLE_TITLE, ROLE_HEADLINE, ROLE_BODY_TEXT,
    ROLE_LOGO, ROLE_CTA, ROLE_BADGE, ROLE_DECORATIVE,
})

# Spec Section 36: FORBIDDEN — human_subject must NOT be extracted as virtual layer
FORBIDDEN_VIRTUAL_ROLES = frozenset({ROLE_HUMAN_SUBJECT})

# Roles marked as required for composition
REQUIRED_ROLES = frozenset({ROLE_PRODUCT, ROLE_TITLE, ROLE_HEADLINE, ROLE_BODY_TEXT})

# Composition owners
OWNER_FOREGROUND_REFLOW = "foreground_reflow"
OWNER_SCENE_PLATE = "scene_plate"

# Extraction methods
METHOD_PAIRED_DIFFERENCE = "paired_difference"
METHOD_NATIVE_LAYER = "native_layer"

# D-2 applicability reason codes
D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS = "has_native_layers"
D2_REASON_NOT_APPLICABLE_PSD_INPUT = "psd_input_use_native_layers"
D2_REASON_APPLICABLE_FLATTENED_PNG = "flattened_png_no_native_layers"
D2_REASON_APPLICABLE_FLATTENED_JPG = "flattened_jpg_no_native_layers"
D2_REASON_APPLICABLE_FLATTENED_UNKNOWN = "flattened_unknown_source_type"


@dataclass
class FlattenedObjectDetection:
    """One advertisement element detected in a flattened image."""
    detection_id: str = ""
    semantic_role: str = ""
    layout_role: str = ""
    bbox: dict = field(default_factory=dict)   # {x, y, width, height} source pixels
    confidence: float = 1.0
    required: bool = False
    priority: int = 0
    text_content: str = ""
    group_id: str = ""
    parent_id: str = ""
    z_order: int = 0
    composition_owner: str = OWNER_FOREGROUND_REFLOW
    contains_text: bool = False
    contains_logo: bool = False
    contains_product: bool = False


@dataclass
class FlattenedObjectMap:
    """Full object detection result for one flattened source image."""
    source_width: int = 0
    source_height: int = 0
    source_sha256: str = ""
    analysis_provider: str = ""
    analysis_model: str = ""
    analysis_version: str = "d2-object-analysis-v1"
    detections: list = field(default_factory=list)   # list[FlattenedObjectDetection]
    warnings: list = field(default_factory=list)
    object_map_sha256: str = ""


@dataclass
class VirtualObjectExtraction:
    """One virtual foreground object extraction attempt result."""
    detection_id: str = ""
    object_id: str = ""
    semantic_role: str = ""
    layout_role: str = ""
    extraction_success: bool = False
    failure_reason: str = ""
    rejection_reason: str = ""
    rgba_image: object = None               # PIL Image RGBA (not serialized)
    source_bbox: dict = field(default_factory=dict)
    alpha_coverage_ratio: float = 0.0
    opaque_coverage_ratio: float = 0.0
    border_alpha_ratio: float = 0.0
    component_count: int = 0
    background_contamination_score: float = 0.0
    extraction_confidence: float = 0.0
    extraction_method: str = METHOD_PAIRED_DIFFERENCE
    mask_sha256: str = ""
    pixel_sha256: str = ""
    warnings: list = field(default_factory=list)


@dataclass
class VirtualForegroundExtractionResult:
    """Full result of D-2 virtual foreground extraction for one job."""
    success: bool = False
    failure_reason: str = ""
    source_type: str = ""
    source_sha256: str = ""
    d2_applicable: bool = False
    d2_reason: str = ""
    object_analysis_succeeded: bool = False
    detected_object_count: int = 0
    virtual_extracted_count: int = 0
    virtual_rejected_count: int = 0
    extracted_objects: list = field(default_factory=list)  # list[VirtualObjectExtraction]
    fg_layers: list = field(default_factory=list)          # extract_foreground_layers() format
    source_aligned_reference_sha256: str = ""
    provider_request_count: int = 0
    d2_implemented: bool = True
    final_recomposition_possible: bool = False
    warnings: list = field(default_factory=list)
