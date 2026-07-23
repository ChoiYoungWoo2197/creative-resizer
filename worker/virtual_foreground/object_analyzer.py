"""D-2: Analyze advertisement foreground objects in a flattened image.

Provider injection pattern: inject FakeObjectAnalysisProvider in tests (zero API calls),
inject OpenAIObjectAnalysisProvider in production.

Spec Section 9 prompt policy: describe content, never name specific brands,
never reference source hash, role classification only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Protocol, runtime_checkable

from PIL import Image

from virtual_foreground.models import (
    FlattenedObjectDetection,
    FlattenedObjectMap,
    REQUIRED_ROLES,
    OWNER_FOREGROUND_REFLOW,
    OWNER_SCENE_PLATE,
    ROLE_HUMAN_SUBJECT,
)

_OBJECT_ANALYSIS_VERSION = "d2-object-analysis-v1"

_VALID_ROLES = frozenset({
    "product", "title", "headline", "body_text",
    "logo", "cta", "badge", "decorative", "human_subject",
})


@runtime_checkable
class ObjectAnalysisProvider(Protocol):
    """Interface for advertisement object analysis providers."""
    def analyze(self, image: Image.Image, source_sha256: str) -> dict:
        """Return dict with 'objects' list and optional 'provider'/'model' keys."""
        ...


class FakeObjectAnalysisProvider:
    """Deterministic fake provider for tests — zero API calls."""

    def __init__(self, detections: list[dict] | None = None):
        self._detections = detections
        self.provider = "fake"
        self.model = "fake-v1"

    def analyze(self, image: Image.Image, source_sha256: str) -> dict:
        if self._detections is not None:
            return {
                "provider": self.provider,
                "model": self.model,
                "objects": self._detections,
            }

        # Default: two fake detections proportional to image size
        w, h = image.size
        return {
            "provider": self.provider,
            "model": self.model,
            "objects": [
                {
                    "detection_id": "det_0001",
                    "semantic_role": "product",
                    "layout_role": "product",
                    "bbox": {"x": w // 4, "y": h // 4,
                             "width": w // 2, "height": h // 2},
                    "confidence": 0.9,
                    "required": True,
                    "priority": 1,
                    "text_content": "",
                    "z_order": 0,
                    "contains_product": True,
                },
                {
                    "detection_id": "det_0002",
                    "semantic_role": "title",
                    "layout_role": "title",
                    "bbox": {"x": w // 8, "y": h // 8,
                             "width": 3 * w // 4, "height": h // 8},
                    "confidence": 0.85,
                    "required": True,
                    "priority": 2,
                    "text_content": "Sample Title",
                    "z_order": 1,
                    "contains_text": True,
                },
            ],
        }


def analyze_flattened_objects(
    *,
    source_image: Image.Image,
    source_sha256: str,
    provider: ObjectAnalysisProvider,
    job_id: str = "",
) -> FlattenedObjectMap:
    """Analyze all advertisement elements in the flattened source image.

    Always returns a FlattenedObjectMap (empty detections on failure).
    Never calls real API in tests — inject FakeObjectAnalysisProvider.
    """
    src_w, src_h = source_image.size

    print(
        f"[D2_OBJECT_ANALYSIS]"
        f" jobId={job_id} sourceSize={src_w}x{src_h}"
        f" sourceSha256={source_sha256[:16]}",
        flush=True,
    )

    obj_map = FlattenedObjectMap(
        source_width=src_w,
        source_height=src_h,
        source_sha256=source_sha256,
        analysis_version=_OBJECT_ANALYSIS_VERSION,
    )

    try:
        raw = provider.analyze(source_image, source_sha256)
    except Exception as exc:
        obj_map.warnings.append(f"OBJECT_ANALYSIS_PROVIDER_ERROR: {exc}")
        print(f"[D2_OBJECT_ANALYSIS] provider error jobId={job_id}: {exc}", flush=True)
        return obj_map

    obj_map.analysis_provider = raw.get("provider", "")
    obj_map.analysis_model = raw.get("model", "")
    raw_objects = raw.get("objects") or []

    if not isinstance(raw_objects, list):
        obj_map.warnings.append(
            "OBJECT_ANALYSIS_INVALID_RESPONSE: 'objects' is not a list"
        )
        return obj_map

    detections: list[FlattenedObjectDetection] = []
    seen_ids: set[str] = set()

    for i, raw_obj in enumerate(raw_objects):
        if not isinstance(raw_obj, dict):
            continue

        role = (raw_obj.get("semantic_role") or "").strip().lower()
        if not role or role not in _VALID_ROLES:
            obj_map.warnings.append(
                f"SKIP_INVALID_ROLE detection_index={i} role={role!r}"
            )
            continue

        bbox_raw = raw_obj.get("bbox") or {}
        bx = int(bbox_raw.get("x", 0))
        by = int(bbox_raw.get("y", 0))
        bw = int(bbox_raw.get("width", 0))
        bh = int(bbox_raw.get("height", 0))

        if bw <= 0 or bh <= 0:
            obj_map.warnings.append(
                f"SKIP_INVALID_BBOX detection_index={i} bbox={bbox_raw}"
            )
            continue

        # Clamp to source bounds
        bx = max(0, min(bx, src_w - 1))
        by = max(0, min(by, src_h - 1))
        bw = min(bw, src_w - bx)
        bh = min(bh, src_h - by)

        if bw <= 0 or bh <= 0:
            obj_map.warnings.append(
                f"SKIP_CLAMPED_BBOX_ZERO detection_index={i}"
            )
            continue

        det_id = (raw_obj.get("detection_id") or f"det_{i:04d}").strip()
        if det_id in seen_ids:
            det_id = f"{det_id}_dup{i}"
        seen_ids.add(det_id)

        # Spec Section 36: human_subject assigned to scene_plate, not foreground_reflow
        comp_owner = (
            OWNER_SCENE_PLATE
            if role == ROLE_HUMAN_SUBJECT
            else OWNER_FOREGROUND_REFLOW
        )

        det = FlattenedObjectDetection(
            detection_id=det_id,
            semantic_role=role,
            layout_role=raw_obj.get("layout_role") or role,
            bbox={"x": bx, "y": by, "width": bw, "height": bh},
            confidence=float(raw_obj.get("confidence", 0.8)),
            required=role in REQUIRED_ROLES,
            priority=int(raw_obj.get("priority", 0)),
            text_content=str(raw_obj.get("text_content", "")),
            group_id=str(raw_obj.get("group_id", "")),
            parent_id=str(raw_obj.get("parent_id", "")),
            z_order=int(raw_obj.get("z_order", i)),
            composition_owner=comp_owner,
            contains_text=bool(raw_obj.get("contains_text", False)),
            contains_logo=bool(raw_obj.get("contains_logo", False)),
            contains_product=bool(raw_obj.get("contains_product", False)),
        )
        detections.append(det)

    obj_map.detections = detections

    # Deterministic SHA-256 of analysis output
    canonical = json.dumps([
        {
            "detection_id": d.detection_id,
            "semantic_role": d.semantic_role,
            "bbox": d.bbox,
            "confidence": d.confidence,
        }
        for d in sorted(detections, key=lambda d: d.detection_id)
    ], sort_keys=True)
    obj_map.object_map_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    print(
        f"[D2_OBJECT_ANALYSIS] jobId={job_id}"
        f" detectionCount={len(detections)}"
        f" objectMapSha256={obj_map.object_map_sha256[:16]}",
        flush=True,
    )

    return obj_map
