"""Stage 21 Bundle B: Layout plan data models."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ObjectPlacement:
    objectId: str = ""
    semanticRole: str = ""
    layoutRole: str = ""
    sourceBBox: dict = field(default_factory=dict)
    originalTargetBBox: dict = field(default_factory=dict)
    targetBBox: dict = field(default_factory=dict)
    scale: float = 1.0
    anchor: str = "top-left"
    required: bool = False
    safeZoneRequired: bool = False
    safeZonePassed: bool = False
    clippingRatio: float = 0.0
    overlapObjectIds: list = field(default_factory=list)
    reason: str = ""


@dataclass
class CandidateScore:
    candidateId: str = ""
    hardFailureCount: int = 0
    safeZoneViolationCount: int = 0
    clippingViolationCount: int = 0
    overlapViolationCount: int = 0
    sizePenalty: float = 0.0
    balancePenalty: float = 0.0
    originalRelationPenalty: float = 0.0
    # D-3: preservation-aware scoring
    originalPreservationPenalty: float = 0.0
    targetAdaptationPenalty: float = 0.0
    readabilityPenalty: float = 0.0
    total: float = 0.0  # lower is better


@dataclass
class LayoutPlanResult:
    success: bool = False
    selectedCandidateId: str = ""
    safeZoneAvailable: bool = False
    safeZoneEnforced: bool = False
    safeZoneRect: dict = field(default_factory=dict)

    inputObjectCount: int = 0
    uniqueObjectCount: int = 0
    requiredObjectCount: int = 0
    placedObjectCount: int = 0
    skippedObjectCount: int = 0
    duplicateCount: int = 0

    allRequiredObjectsPlaced: bool = False
    allUniqueObjectsPlaced: bool = False
    noDuplicateComposition: bool = False
    allObjectsCompositedOnce: bool = False

    safeZoneViolationCount: int = 0
    clippingViolationCount: int = 0
    overlapViolationCount: int = 0

    hardFailReasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    objectPlacements: list = field(default_factory=list)  # list[ObjectPlacement]
    candidateScores: list = field(default_factory=list)   # list[CandidateScore]
