"""Stage 21 Bundle C-1: Verdict data models.

Source-type-independent verdict structure.  Every verdict result carries
status, reasonCodes, evidence, and metrics so downstream consumers can
act on structured data rather than parsing log strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Status constants ──────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
NOT_TESTED = "NOT_TESTED"
NOT_APPLICABLE = "NOT_APPLICABLE"

VALID_STATUSES = frozenset({PASS, FAIL, NOT_TESTED, NOT_APPLICABLE})

# ── sourceType enum values ────────────────────────────────────────────────────

SOURCE_TYPE_PSD_LAYER = "psd_layer"
SOURCE_TYPE_PSD_COMPOSITE_CROP = "psd_composite_crop"
SOURCE_TYPE_RASTER_CROP = "raster_crop"
SOURCE_TYPE_AI_SEGMENTATION = "ai_segmentation"
SOURCE_TYPE_OCR_RENDER = "ocr_render"
SOURCE_TYPE_GENERATED_SHAPE = "generated_shape"
SOURCE_TYPE_UNKNOWN = "unknown"

VALID_SOURCE_TYPES = frozenset({
    SOURCE_TYPE_PSD_LAYER,
    SOURCE_TYPE_PSD_COMPOSITE_CROP,
    SOURCE_TYPE_RASTER_CROP,
    SOURCE_TYPE_AI_SEGMENTATION,
    SOURCE_TYPE_OCR_RENDER,
    SOURCE_TYPE_GENERATED_SHAPE,
    SOURCE_TYPE_UNKNOWN,
})

# ── compositionOwner enum values ──────────────────────────────────────────────

OWNER_FOREGROUND_REFLOW = "foreground_reflow"
OWNER_SCENE_PLATE = "scene_plate"
OWNER_BACKGROUND = "background"
OWNER_EXCLUDED = "excluded"

VALID_COMPOSITION_OWNERS = frozenset({
    OWNER_FOREGROUND_REFLOW,
    OWNER_SCENE_PLATE,
    OWNER_BACKGROUND,
    OWNER_EXCLUDED,
})

VERDICT_VERSION = "c1.0"


# ── Core verdict result ───────────────────────────────────────────────────────

@dataclass
class VerdictResult:
    """Single-dimension verdict with structured reason codes and evidence."""
    name: str = ""
    status: str = NOT_TESTED
    required: bool = True
    reasonCodes: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    checkedAt: str = ""
    version: str = VERDICT_VERSION


# ── Unified object model ──────────────────────────────────────────────────────

@dataclass
class UnifiedObject:
    """Source-type-independent representation of one compositable object.

    PIL Image is intentionally excluded — only serializable metadata is stored.
    sourceRef can hold a file path or cache key for the actual pixel data.
    """
    objectId: str = ""
    sourceType: str = SOURCE_TYPE_PSD_LAYER
    sourceRef: str = ""
    sourceLayerId: str = ""
    sourceLayerName: str = ""
    semanticRole: str = ""
    layoutRole: str = ""
    required: bool = False
    priority: int = 0
    confidence: float = 1.0
    sourceBBox: dict = field(default_factory=dict)
    originalTargetBBox: dict = field(default_factory=dict)
    targetBBox: dict = field(default_factory=dict)
    width: int = 0
    height: int = 0
    aspectRatio: float = 0.0
    zIndex: int = 0
    sourcePixelSha256: str = ""
    maskSha256: str = ""
    textContent: str = ""
    compositionOwner: str = OWNER_FOREGROUND_REFLOW
    layoutLocked: bool = False
    extractionMethod: str = "psd_layer_composite"
    extractionWarnings: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class UnifiedObjectManifest:
    """Container for all unified objects in one job+spec pair.

    manifestSha256 is computed from canonical serializable metadata only
    (no PIL memory addresses, no random ordering).
    """
    sourceType: str = SOURCE_TYPE_PSD_LAYER
    inputObjectCount: int = 0
    uniqueObjectCount: int = 0
    requiredObjectCount: int = 0
    objects: list = field(default_factory=list)      # list[UnifiedObject]
    duplicateObjectIds: list = field(default_factory=list)
    invalidObjectIds: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    manifestSha256: str = ""


# ── Top-level verdict summary ─────────────────────────────────────────────────

@dataclass
class Stage21VerdictSummary:
    """Aggregated verdict across all C-1 dimensions.

    C-1 required verdicts: technical, extraction, composition, layout.
    visualVerdict is present but required=False in C-1.

    overall PASS only when:
      - All required verdicts are PASS
    overall FAIL when:
      - Any required verdict is FAIL
      - Any required verdict is NOT_TESTED
      - All required verdicts are NOT_APPLICABLE simultaneously
      - Verdict pipeline raised an exception
    NOT_APPLICABLE for individual verdicts is allowed when genuinely inapplicable
    (e.g., PNG input → extraction/composition/layout = NOT_APPLICABLE).
    """
    technicalVerdict: VerdictResult = field(default_factory=lambda: VerdictResult(name="technicalVerdict"))
    extractionVerdict: VerdictResult = field(default_factory=lambda: VerdictResult(name="extractionVerdict"))
    compositionVerdict: VerdictResult = field(default_factory=lambda: VerdictResult(name="compositionVerdict"))
    layoutVerdict: VerdictResult = field(default_factory=lambda: VerdictResult(name="layoutVerdict"))
    visualVerdict: VerdictResult = field(default_factory=lambda: VerdictResult(
        name="visualVerdict", required=False, status=NOT_TESTED,
        reasonCodes=["VISUAL_NOT_TESTED"],
    ))
    overallStatus: str = NOT_TESTED
    overallReasonCodes: list = field(default_factory=list)
    requiredVerdicts: list = field(default_factory=list)
    failedVerdicts: list = field(default_factory=list)
    notTestedVerdicts: list = field(default_factory=list)
    version: str = VERDICT_VERSION
