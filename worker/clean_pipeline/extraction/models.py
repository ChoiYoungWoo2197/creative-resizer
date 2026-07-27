"""Extraction stage output models."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class SourceBBox:
    x: int
    y: int
    width: int
    height: int


@dataclass
class ExtractedObject:
    id: str
    role: str
    required: bool
    movable: bool
    removable_from_scene: bool
    source_bbox: SourceBBox      # min bounding rect of mask in original image coords
    rgba_path: str
    mask_path: str
    meta_path: str


@dataclass
class ProtectedMask:
    id: str
    role: str
    mask_path: str               # full original-size mask, no RGBA


@dataclass
class ExtractionResult:
    job_id: str
    objects: list[ExtractedObject] = field(default_factory=list)
    protected: list[ProtectedMask] = field(default_factory=list)
    extraction_json_path: str = ""

    def to_dict(self) -> dict:
        return {
            "jobId": self.job_id,
            "objectCount": len(self.objects),
            "protectedCount": len(self.protected),
            "objects": [
                {
                    "id": o.id,
                    "role": o.role,
                    "required": o.required,
                    "movable": o.movable,
                    "removableFromScene": o.removable_from_scene,
                    "sourceBbox": {
                        "x": o.source_bbox.x,
                        "y": o.source_bbox.y,
                        "width": o.source_bbox.width,
                        "height": o.source_bbox.height,
                    },
                    "rgbaPath": o.rgba_path,
                    "maskPath": o.mask_path,
                    "metaPath": o.meta_path,
                }
                for o in self.objects
            ],
            "protected": [
                {"id": p.id, "role": p.role, "maskPath": p.mask_path}
                for p in self.protected
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
