"""SceneObject and SceneManifest — output of the SCENE_ANALYSIS stage."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

VALID_ROLES = frozenset({
    "product", "logo", "title_group", "body_text_group",
    "cta_group", "badge", "decorative_ad", "protected_subject",
})

PROTECTED_ROLES = frozenset({"protected_subject"})


@dataclass
class BBox:
    x: int
    y: int
    width: int
    height: int


@dataclass
class SceneObject:
    id: str
    role: str
    required: bool
    movable: bool
    removable_from_scene: bool
    bbox: BBox
    polygon: list[list[int]]       # [[x, y], ...] — never auto-generated from bbox
    confidence: float
    group_id: Optional[str] = None
    z_index: int = 0
    text_content: str = ""
    description: str = ""


@dataclass
class SceneManifest:
    job_id: str
    source_sha256: str
    image_width: int
    image_height: int
    model: str
    objects: list[SceneObject] = field(default_factory=list)
    api_call_count: int = 1

    def to_dict(self) -> dict:
        return {
            "jobId": self.job_id,
            "sourceSha256": self.source_sha256,
            "imageWidth": self.image_width,
            "imageHeight": self.image_height,
            "model": self.model,
            "apiCallCount": self.api_call_count,
            "objects": [
                {
                    "id": o.id,
                    "role": o.role,
                    "required": o.required,
                    "movable": o.movable,
                    "removableFromScene": o.removable_from_scene,
                    "bbox": {
                        "x": o.bbox.x, "y": o.bbox.y,
                        "width": o.bbox.width, "height": o.bbox.height,
                    },
                    "polygon": o.polygon,
                    "confidence": o.confidence,
                    "groupId": o.group_id,
                    "zIndex": o.z_index,
                    "textContent": o.text_content,
                    "description": o.description,
                }
                for o in self.objects
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "SceneManifest":
        d = json.loads(text)
        objects = [
            SceneObject(
                id=o["id"],
                role=o["role"],
                required=o["required"],
                movable=o["movable"],
                removable_from_scene=o["removableFromScene"],
                bbox=BBox(
                    x=o["bbox"]["x"], y=o["bbox"]["y"],
                    width=o["bbox"]["width"], height=o["bbox"]["height"],
                ),
                polygon=o.get("polygon") or [],
                confidence=o.get("confidence", 1.0),
                group_id=o.get("groupId"),
                z_index=o.get("zIndex", 0),
                text_content=o.get("textContent", ""),
                description=o.get("description", ""),
            )
            for o in d.get("objects", [])
        ]
        return cls(
            job_id=d["jobId"],
            source_sha256=d["sourceSha256"],
            image_width=d["imageWidth"],
            image_height=d["imageHeight"],
            model=d["model"],
            api_call_count=d.get("apiCallCount", 1),
            objects=objects,
        )
