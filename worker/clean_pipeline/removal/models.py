"""Removal stage output models."""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class RemovalMaskResult:
    job_id: str
    image_width: int
    image_height: int
    source_object_count: int   # number of ad objects whose masks were unioned
    dilation_px: int
    removal_mask_path: str     # full-size L-mode PNG: white = area to remove
    restore_mask_path: str     # full-size L-mode PNG: white = area to restore (complement)
    removal_json_path: str

    def to_dict(self) -> dict:
        return {
            "jobId": self.job_id,
            "imageWidth": self.image_width,
            "imageHeight": self.image_height,
            "sourceObjectCount": self.source_object_count,
            "dilationPx": self.dilation_px,
            "removalMaskPath": self.removal_mask_path,
            "restoreMaskPath": self.restore_mask_path,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
