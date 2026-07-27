"""Scene plate stage output models."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ScenePlateResult:
    job_id: str
    target_width: int
    target_height: int
    source_projection_path: str
    projected_removal_mask_path: str
    projected_restore_mask_path: str
    ai_cleanup_path: str
    scene_plate_path: str
    scene_json_path: str
    api_call_count: int = 1

    def to_dict(self) -> dict:
        return {
            "jobId": self.job_id,
            "targetWidth": self.target_width,
            "targetHeight": self.target_height,
            "apiCallCount": self.api_call_count,
            "sourceProjectionPath": self.source_projection_path,
            "projectedRemovalMaskPath": self.projected_removal_mask_path,
            "projectedRestoreMaskPath": self.projected_restore_mask_path,
            "aiCleanupPath": self.ai_cleanup_path,
            "scenePlatePath": self.scene_plate_path,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
