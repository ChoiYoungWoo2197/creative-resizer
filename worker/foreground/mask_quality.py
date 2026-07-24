"""Stage 4: Foreground mask quality checker — reject contaminated fg_layers.

A layer is contaminated when it lacks the minimum evidence required to be
composited over the AI background:
  - confidence=0:            object not detected with meaningful certainty
  - semanticEvidence=[]:     no GDINO/SAM2 evidence backing the detection
  - maskRef='':              no mask available for pixel-level extraction
  - recompose=False:         object not flagged for recomposition

Contaminated layers are rejected (not composited) to prevent:
  - phantom objects with zero confidence appearing in the output
  - layout plans that position invisible or missing objects
  - verdict pipeline evaluating roles that were never meaningfully extracted
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MaskQualityResult:
    """Result of checking one layer's mask quality."""
    objectId: str = ""
    role: str = ""
    isClean: bool = True
    reasonCodes: list = field(default_factory=list)
    confidence: float = 0.0
    hasEvidence: bool = False
    hasMaskRef: bool = False
    willRecompose: bool = False


def check_mask_contamination(layer: dict) -> MaskQualityResult:
    """Check a single fg_layer for mask contamination.

    Args:
        layer: dict with role, confidence, semanticEvidence, maskRef, recompose fields

    Returns:
        MaskQualityResult with isClean=True if layer passes all quality checks
    """
    if not isinstance(layer, dict):
        return MaskQualityResult(isClean=False, reasonCodes=["NOT_A_DICT"])

    obj_id = layer.get("objectId") or layer.get("object_id") or ""
    role = (
        layer.get("role")
        or layer.get("semanticRole")
        or layer.get("semantic_role")
        or ""
    )
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
    recompose = bool(layer.get("recompose", False))

    reasons = []
    if confidence <= 0:
        reasons.append("CONFIDENCE_ZERO")
    if not evidence:
        reasons.append("NO_SEMANTIC_EVIDENCE")
    if not mask_ref:
        reasons.append("NO_MASK_REF")
    if not recompose:
        reasons.append("RECOMPOSE_FALSE")

    return MaskQualityResult(
        objectId=obj_id,
        role=role,
        isClean=len(reasons) == 0,
        reasonCodes=reasons,
        confidence=confidence,
        hasEvidence=bool(evidence),
        hasMaskRef=bool(mask_ref),
        willRecompose=recompose,
    )


def filter_clean_fg_layers(
    layers: list,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> tuple:
    """Filter fg_layers into (clean_layers, rejected_pairs).

    Args:
        layers:  list of fg_layer dicts
        job_id:  for logging
        spec_id: for logging

    Returns:
        (clean_layers, rejected_pairs)
        clean_layers:   list of dicts that pass all quality checks
        rejected_pairs: list of (layer_dict, MaskQualityResult) for rejected layers
    """
    if not layers:
        return [], []

    clean = []
    rejected_pairs = []

    for layer in layers:
        result = check_mask_contamination(layer)
        if result.isClean:
            clean.append(layer)
        else:
            rejected_pairs.append((layer, result))

    if rejected_pairs:
        print(
            f"[MASK_CONTAMINATION_FILTER] jobId={job_id} specId={spec_id}"
            f" totalCount={len(layers)}"
            f" cleanCount={len(clean)}"
            f" rejectedCount={len(rejected_pairs)}",
            flush=True,
        )
        for layer, qr in rejected_pairs:
            print(
                f"[MASK_CONTAMINATION_REJECT] jobId={job_id} specId={spec_id}"
                f" objectId={qr.objectId!r}"
                f" role={qr.role!r}"
                f" reasonCodes={qr.reasonCodes}"
                f" confidence={qr.confidence:.4f}"
                f" hasEvidence={qr.hasEvidence}"
                f" hasMaskRef={qr.hasMaskRef}"
                f" willRecompose={qr.willRecompose}",
                flush=True,
            )

    return clean, rejected_pairs
