"""Scene plate stage output models."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ScenePlateResult:
    job_id: str
    target_width: int
    target_height: int
    source_projection_path: str          # clean_source_projection — P6 comparison baseline
    projected_removal_mask_path: str     # original removal mask projected
    projected_restore_mask_path: str     # inverted outpaint mask — P6 deterministic check
    ai_cleanup_path: str                 # raw AI output for source cleanup
    scene_plate_path: str
    scene_json_path: str
    api_call_count: int = 1              # always 1 (source cleanup); kept for backward compat

    # New P5 v2 artifact paths
    original_projection_path: str = ""  # original source projected (with ads) — debug only
    source_cleanup_ai_path: str = ""    # raw AI source cleanup output at canonical size
    clean_source_plate_path: str = ""   # ads removed + protected subject restored (canonical size)
    clean_source_projection_path: str = ""  # clean source projected to target canvas
    outpaint_mask_path: str = ""        # white = letterbox regions needing outpaint
    target_outpaint_ai_path: str = ""   # raw AI outpaint output (empty if no letterbox)

    # API call metrics
    source_cleanup_api_call_count: int = 1
    outpaint_api_call_count: int = 0
    total_api_call_count: int = 1

    def to_dict(self) -> dict:
        return {
            "jobId": self.job_id,
            "targetWidth": self.target_width,
            "targetHeight": self.target_height,
            "apiCallCount": self.api_call_count,
            "sourceCleanupApiCallCount": self.source_cleanup_api_call_count,
            "outpaintApiCallCount": self.outpaint_api_call_count,
            "totalApiCallCount": self.total_api_call_count,
            "originalProjectionPath": self.original_projection_path,
            "sourceProjectionPath": self.source_projection_path,
            "projectedRemovalMaskPath": self.projected_removal_mask_path,
            "projectedRestoreMaskPath": self.projected_restore_mask_path,
            "sourceCleanupAiPath": self.source_cleanup_ai_path,
            "cleanSourcePlatePath": self.clean_source_plate_path,
            "cleanSourceProjectionPath": self.clean_source_projection_path,
            "outpaintMaskPath": self.outpaint_mask_path,
            "targetOutpaintAiPath": self.target_outpaint_ai_path,
            "aiCleanupPath": self.ai_cleanup_path,
            "scenePlatePath": self.scene_plate_path,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
