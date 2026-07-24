"""Stage 3: Semantic scene inventory — determine expected required roles before extraction.

The inventory is built from D-2 fg_layers BEFORE passing to build_semantic_manifest.
Only high-quality layers (confidence>0, semanticEvidence not empty, maskRef not empty)
contribute to expectedRequiredRoles.

This prevents circular required-role derivation: previously, required roles were
derived from extracted objects, so a confidence=0 object with no evidence could
still be marked required=True in the manifest.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# Roles that are always architecturally significant when detected with sufficient quality
INHERENTLY_REQUIRED_ROLES = frozenset({
    "product", "product_primary", "product_secondary",
    "human_subject",
    "title", "title_text",
    "cta", "cta_text",
    "brand_logo",
})


@dataclass
class SemanticSceneInventory:
    """Pre-extraction semantic inventory of expected object roles.

    expectedRequiredRoles: roles that exist with high-quality evidence.
    detectedRoles:         all roles in the raw D-2 output (including low-quality).
    highQualityLayers:     layers that pass quality thresholds (use for manifest).
    rejectedLayers:        layers that failed quality check (confidence=0 etc.).
    rejectionReasons:      {objectId: [reason_code, ...]} for rejected layers.
    """
    expectedRequiredRoles: list = field(default_factory=list)
    detectedRoles: list = field(default_factory=list)
    highQualityLayers: list = field(default_factory=list)
    rejectedLayers: list = field(default_factory=list)
    rejectionReasons: dict = field(default_factory=dict)


def _layer_role(layer: dict) -> str:
    return (
        layer.get("role")
        or layer.get("semanticRole")
        or layer.get("semantic_role")
        or ""
    ).lower()


def _layer_objectid(layer: dict) -> str:
    return layer.get("objectId") or layer.get("object_id") or ""


def _quality_reasons(layer: dict) -> list:
    """Return list of quality-failure reason codes. Empty = high quality."""
    if not isinstance(layer, dict):
        return ["NOT_A_DICT"]
    reasons = []
    confidence = float(layer.get("confidence") or 0.0)
    evidence = (
        layer.get("semanticEvidence")
        or layer.get("semantic_evidence")
        or []
    )
    mask_ref = (
        layer.get("maskRef")
        or layer.get("mask_sha256")
        or layer.get("mask_ref")
        or ""
    )
    if confidence <= 0:
        reasons.append("CONFIDENCE_ZERO")
    if not evidence:
        reasons.append("NO_SEMANTIC_EVIDENCE")
    if not mask_ref:
        reasons.append("NO_MASK_REF")
    return reasons


def build_semantic_inventory(
    fg_layers: list,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> SemanticSceneInventory:
    """Build SemanticSceneInventory from D-2 fg_layers before extraction.

    Quality criteria for a layer to contribute to expectedRequiredRoles:
      - confidence > 0
      - semanticEvidence not empty
      - maskRef not empty

    Args:
        fg_layers: raw D-2 fg_layers list (may contain contaminated objects)
        job_id:    for logging
        spec_id:   for logging

    Returns:
        SemanticSceneInventory with highQualityLayers suitable for build_semantic_manifest
    """
    if not fg_layers:
        print(
            f"[SEMANTIC_INVENTORY_BUILD] jobId={job_id} specId={spec_id}"
            f" detectedCount=0 highQualityCount=0 rejectedCount=0"
            f" expectedRequiredRoles=[]",
            flush=True,
        )
        return SemanticSceneInventory()

    high_quality: list = []
    rejected: list = []
    rejection_reasons: dict = {}

    for layer in fg_layers:
        if not isinstance(layer, dict):
            continue
        reasons = _quality_reasons(layer)
        if reasons:
            rejected.append(layer)
            obj_id = _layer_objectid(layer)
            if obj_id:
                rejection_reasons[obj_id] = reasons
        else:
            high_quality.append(layer)

    detected_roles = sorted(set(
        _layer_role(l)
        for l in fg_layers
        if isinstance(l, dict) and _layer_role(l)
    ))

    expected_required_roles = sorted(set(
        _layer_role(l)
        for l in high_quality
        if isinstance(l, dict)
        and _layer_role(l)
        and _layer_role(l) in INHERENTLY_REQUIRED_ROLES
    ))

    print(
        f"[SEMANTIC_INVENTORY_BUILD] jobId={job_id} specId={spec_id}"
        f" detectedCount={len(fg_layers)}"
        f" highQualityCount={len(high_quality)}"
        f" rejectedCount={len(rejected)}"
        f" expectedRequiredRoles={expected_required_roles}",
        flush=True,
    )

    if rejected:
        for layer in rejected:
            obj_id = _layer_objectid(layer)
            role = _layer_role(layer)
            reasons = rejection_reasons.get(obj_id, ["UNKNOWN"])
            print(
                f"[SEMANTIC_INVENTORY_REJECT] jobId={job_id} specId={spec_id}"
                f" objectId={obj_id!r} role={role!r}"
                f" reasonCodes={reasons}",
                flush=True,
            )

    return SemanticSceneInventory(
        expectedRequiredRoles=expected_required_roles,
        detectedRoles=detected_roles,
        highQualityLayers=high_quality,
        rejectedLayers=rejected,
        rejectionReasons=rejection_reasons,
    )
