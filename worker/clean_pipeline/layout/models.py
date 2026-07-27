"""Layout stage data models."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SafeZone:
    left: int
    top: int
    right: int    # exclusive x boundary (= target_width - safe_right)
    bottom: int   # exclusive y boundary (= target_height - safe_bottom)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class ObjectCandidate:
    object_id: str
    role: str
    required: bool
    anchor: str
    scale: float
    x: int
    y: int
    width: int
    height: int


@dataclass
class PlacedObject:
    object_id: str
    role: str
    required: bool
    anchor: str
    scale: float
    x: int
    y: int
    width: int
    height: int
    source_rgba_path: str

    def to_dict(self) -> dict:
        return {
            "objectId": self.object_id,
            "role": self.role,
            "required": self.required,
            "anchor": self.anchor,
            "scale": self.scale,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "sourceRgbaPath": self.source_rgba_path,
        }


@dataclass
class LayoutResult:
    job_id: str
    target_width: int
    target_height: int
    placed: list[PlacedObject] = field(default_factory=list)
    candidates_json_path: str = ""
    layout_json_path: str = ""
    layout_debug_path: str = ""

    def to_dict(self) -> dict:
        return {
            "jobId": self.job_id,
            "targetWidth": self.target_width,
            "targetHeight": self.target_height,
            "placedCount": len(self.placed),
            "placed": [p.to_dict() for p in self.placed],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
