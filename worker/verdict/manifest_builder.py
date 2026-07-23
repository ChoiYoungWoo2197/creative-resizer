"""Stage 21 Bundle C-1: Build UnifiedObjectManifest from fg_layers.

Converts PSD foreground layers (output of extract_foreground_layers())
into source-type-independent UnifiedObject records.

PIL Image data is intentionally excluded from the manifest.
Only serializable metadata (bbox, sha, role, id) is stored.
"""
from __future__ import annotations

import hashlib
import json
from typing import Sequence

from verdict.models import (
    UnifiedObject, UnifiedObjectManifest,
    SOURCE_TYPE_PSD_LAYER, SOURCE_TYPE_UNKNOWN, OWNER_FOREGROUND_REFLOW,
)
from verdict import reason_codes as RC

# Required roles for extraction validation
_REQUIRED_ROLES = frozenset({"product", "title", "headline", "body_text"})


def _bbox_valid(bbox: dict) -> bool:
    return (
        isinstance(bbox, dict)
        and bbox.get("width", 0) > 0
        and bbox.get("height", 0) > 0
    )


def _aspect_ratio(bbox: dict) -> float:
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    if h <= 0:
        return 0.0
    return round(w / h, 6)


def build_manifest_from_fg_layers(
    fg_layers: Sequence[dict],
    source_type: str = SOURCE_TYPE_PSD_LAYER,
    job_id: str = "",
    spec_id: str = "",
) -> UnifiedObjectManifest:
    """Convert extract_foreground_layers() output to UnifiedObjectManifest.

    Each fg_layer dict is expected to have:
      objectId, layerId, role, name, bbox (target coords),
      sourceBBox, sourcePixelSha256, depth, compositedCount (may be absent).

    PIL Image fields are ignored — not serialized.
    """
    manifest = UnifiedObjectManifest(sourceType=source_type)
    warnings: list[str] = []

    if not fg_layers:
        warnings.append("MANIFEST_EMPTY: no fg_layers provided")
        manifest.warnings = warnings
        manifest.manifestSha256 = _compute_sha256(manifest)
        return manifest

    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    invalid_ids: list[str] = []
    objects: list[UnifiedObject] = []

    for layer in fg_layers:
        oid = layer.get("objectId", "").strip()
        role = layer.get("role", "unknown")
        tb = layer.get("bbox", {})
        sb = layer.get("sourceBBox", {})
        orig_tb = layer.get("originalTargetBBox") or layer.get("bbox", {})

        if not oid:
            oid = f"_auto_{role}_{layer.get('layerId', '')}_{tb.get('x',0)},{tb.get('y',0)}"

        manifest.inputObjectCount += 1

        if oid in seen_ids:
            duplicate_ids.append(oid)
            continue

        seen_ids.add(oid)
        obj = UnifiedObject(
            objectId=oid,
            sourceType=source_type,
            sourceRef="",
            sourceLayerId=layer.get("layerId", ""),
            sourceLayerName=layer.get("name", ""),
            semanticRole=role,
            layoutRole=role,  # May be overridden by layout resolver
            required=role in _REQUIRED_ROLES,
            priority=0,
            confidence=1.0,
            sourceBBox=dict(sb) if sb else {},
            originalTargetBBox=dict(orig_tb) if orig_tb else {},
            targetBBox=dict(tb) if tb else {},
            width=tb.get("width", 0),
            height=tb.get("height", 0),
            aspectRatio=_aspect_ratio(tb),
            zIndex=layer.get("depth", 0),
            sourcePixelSha256=layer.get("sourcePixelSha256", ""),
            maskSha256="",
            textContent="",
            compositionOwner=OWNER_FOREGROUND_REFLOW,
            layoutLocked=False,
            extractionMethod="psd_layer_composite",
            extractionWarnings=[],
            metadata={},
        )

        # Validate bbox
        if not _bbox_valid(tb):
            invalid_ids.append(oid)
            obj.extractionWarnings.append("INVALID_TARGET_BBOX")
            warnings.append(f"INVALID_BBOX objectId={oid!r} role={role!r} bbox={tb}")

        objects.append(obj)

    manifest.uniqueObjectCount = len(objects)
    manifest.requiredObjectCount = sum(1 for o in objects if o.required)
    manifest.objects = objects
    manifest.duplicateObjectIds = sorted(set(duplicate_ids))
    manifest.invalidObjectIds = sorted(set(invalid_ids))
    manifest.warnings = warnings

    print(
        f"[UNIFIED_MANIFEST]"
        f" jobId={job_id} specId={spec_id}"
        f" sourceType={source_type}"
        f" inputObjectCount={manifest.inputObjectCount}"
        f" uniqueObjectCount={manifest.uniqueObjectCount}"
        f" requiredObjectCount={manifest.requiredObjectCount}"
        f" duplicateObjectIds={manifest.duplicateObjectIds}"
        f" invalidObjectIds={manifest.invalidObjectIds}"
        f" manifestSha256=pending",
        flush=True,
    )

    manifest.manifestSha256 = _compute_sha256(manifest)
    return manifest


def _compute_sha256(manifest: UnifiedObjectManifest) -> str:
    """Deterministic SHA-256 of canonical manifest metadata.

    PIL Image data is excluded. Object list is sorted by objectId
    so insertion order does not affect the hash.
    """
    canonical = {
        "sourceType": manifest.sourceType,
        "inputObjectCount": manifest.inputObjectCount,
        "uniqueObjectCount": manifest.uniqueObjectCount,
        "requiredObjectCount": manifest.requiredObjectCount,
        "duplicateObjectIds": sorted(manifest.duplicateObjectIds),
        "invalidObjectIds": sorted(manifest.invalidObjectIds),
        "objects": sorted([
            {
                "objectId": o.objectId,
                "semanticRole": o.semanticRole,
                "layoutRole": o.layoutRole,
                "required": o.required,
                "width": o.width,
                "height": o.height,
                "aspectRatio": o.aspectRatio,
                "sourcePixelSha256": o.sourcePixelSha256,
                "targetBBox": {k: o.targetBBox.get(k, 0) for k in ("x", "y", "width", "height")},
                "compositionOwner": o.compositionOwner,
            }
            for o in manifest.objects
        ], key=lambda d: d["objectId"]),
    }
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
