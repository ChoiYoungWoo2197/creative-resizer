"""Stage 21 Bundle C-1: Verdict serialization to JSON-safe dicts.

All output dicts use deterministic key ordering (sorted).
PIL Images and provider objects must never reach this layer.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from verdict.models import (
    VerdictResult, UnifiedObject, UnifiedObjectManifest, Stage21VerdictSummary,
)


def _to_json_safe(obj: Any) -> Any:
    """Recursively convert dataclass/list/dict to JSON-safe primitives."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_json_safe(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(i) for i in obj]
    return obj


def serialize_verdict_result(vr: VerdictResult) -> dict:
    """Convert VerdictResult to a deterministic JSON-safe dict."""
    return {
        "name": vr.name,
        "status": vr.status,
        "required": vr.required,
        "reasonCodes": sorted(vr.reasonCodes),
        "messages": list(vr.messages),
        "evidence": _to_json_safe(vr.evidence),
        "metrics": _to_json_safe(vr.metrics),
        "checkedAt": vr.checkedAt,
        "version": vr.version,
    }


def serialize_manifest(manifest: UnifiedObjectManifest) -> dict:
    """Convert UnifiedObjectManifest to a JSON-safe dict (no PIL data)."""
    return {
        "sourceType": manifest.sourceType,
        "inputObjectCount": manifest.inputObjectCount,
        "uniqueObjectCount": manifest.uniqueObjectCount,
        "requiredObjectCount": manifest.requiredObjectCount,
        "duplicateObjectIds": sorted(manifest.duplicateObjectIds),
        "invalidObjectIds": sorted(manifest.invalidObjectIds),
        "warnings": list(manifest.warnings),
        "manifestSha256": manifest.manifestSha256,
        "objects": [
            {
                "objectId": o.objectId,
                "sourceType": o.sourceType,
                "sourceLayerId": o.sourceLayerId,
                "sourceLayerName": o.sourceLayerName,
                "semanticRole": o.semanticRole,
                "layoutRole": o.layoutRole,
                "required": o.required,
                "confidence": o.confidence,
                "sourceBBox": dict(o.sourceBBox),
                "targetBBox": dict(o.targetBBox),
                "width": o.width,
                "height": o.height,
                "aspectRatio": o.aspectRatio,
                "zIndex": o.zIndex,
                "sourcePixelSha256": o.sourcePixelSha256,
                "compositionOwner": o.compositionOwner,
                "extractionMethod": o.extractionMethod,
                "extractionWarnings": list(o.extractionWarnings),
            }
            for o in sorted(manifest.objects, key=lambda x: x.objectId)
        ],
    }


def serialize_verdict_summary(summary: Stage21VerdictSummary) -> dict:
    """Convert Stage21VerdictSummary to a deterministic JSON-safe dict."""
    return {
        "version": summary.version,
        "overallStatus": summary.overallStatus,
        "overallReasonCodes": sorted(summary.overallReasonCodes),
        "requiredVerdicts": list(summary.requiredVerdicts),
        "failedVerdicts": list(summary.failedVerdicts),
        "notTestedVerdicts": list(summary.notTestedVerdicts),
        "technicalVerdict": serialize_verdict_result(summary.technicalVerdict) if summary.technicalVerdict else None,
        "extractionVerdict": serialize_verdict_result(summary.extractionVerdict) if summary.extractionVerdict else None,
        "compositionVerdict": serialize_verdict_result(summary.compositionVerdict) if summary.compositionVerdict else None,
        "layoutVerdict": serialize_verdict_result(summary.layoutVerdict) if summary.layoutVerdict else None,
        "visualVerdict": serialize_verdict_result(summary.visualVerdict) if summary.visualVerdict else None,
    }


def extract_provenance_fields(
    summary: Stage21VerdictSummary,
    manifest: UnifiedObjectManifest | None = None,
) -> dict:
    """Extract fields to add into renderProvenance dict.

    Keeps keys deterministic and flat for easy Java Map access.
    """
    prov = {
        "verdictVersion": summary.version,
        "overallVerdict": summary.overallStatus,
        "overallReasonCodes": sorted(summary.overallReasonCodes),
        "requiredVerdicts": list(summary.requiredVerdicts),
        "failedVerdicts": list(summary.failedVerdicts),
        "technicalVerdict": summary.technicalVerdict.status if summary.technicalVerdict else "NOT_TESTED",
        "extractionVerdict": summary.extractionVerdict.status if summary.extractionVerdict else "NOT_TESTED",
        "compositionVerdict": summary.compositionVerdict.status if summary.compositionVerdict else "NOT_TESTED",
        "layoutVerdict": summary.layoutVerdict.status if summary.layoutVerdict else "NOT_TESTED",
        "visualVerdict": "NOT_TESTED",
        # Composition metrics (flat, for easy access)
        "allRequiredObjectsPlaced": (
            summary.compositionVerdict.metrics.get("allRequiredObjectsPlaced", False)
            if summary.compositionVerdict else False
        ),
        "noDuplicateComposition": (
            summary.compositionVerdict.metrics.get("noDuplicateComposition", False)
            if summary.compositionVerdict else False
        ),
        "allObjectsCompositedOnce": (
            summary.compositionVerdict.metrics.get("allObjectsCompositedOnce", False)
            if summary.compositionVerdict else False
        ),
        # Layout metrics (flat)
        "safeZoneAvailable": (
            summary.layoutVerdict.metrics.get("safeZoneAvailable", False)
            if summary.layoutVerdict else False
        ),
        "safeZoneEnforced": (
            summary.layoutVerdict.metrics.get("safeZoneEnforced", False)
            if summary.layoutVerdict else False
        ),
        "selectedCandidateId": (
            summary.layoutVerdict.metrics.get("selectedCandidateId", "")
            if summary.layoutVerdict else ""
        ),
    }
    if manifest is not None:
        prov["manifestSha256"] = manifest.manifestSha256
        prov["unifiedObjectManifest"] = {
            "inputObjectCount": manifest.inputObjectCount,
            "uniqueObjectCount": manifest.uniqueObjectCount,
            "requiredObjectCount": manifest.requiredObjectCount,
            "duplicateObjectIds": sorted(manifest.duplicateObjectIds),
        }
    return prov
