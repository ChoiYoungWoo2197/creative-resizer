"""Adapts a /generate request JSON payload → CleanPipelineRequest.

Only psdPath and specs are consumed.
resizeMode, smartFitStrength, focalPosition, psdMode are deliberately ignored.
"""
from __future__ import annotations

from clean_pipeline.contracts import CleanPipelineRequest, TargetSpec


def adapt_request(data: dict, job_id: str, output_directory: str) -> CleanPipelineRequest:
    source_path = data.get("psdPath", "")
    specs_raw = data.get("specs", [])

    target_specs: list[TargetSpec] = []
    for s in specs_raw:
        sz = s.get("safeZone") or {}
        target_specs.append(TargetSpec(
            width=int(s.get("width", 0)),
            height=int(s.get("height", 0)),
            safe_top=int(sz.get("top", s.get("safeTop", s.get("safe_top", 0)))),
            safe_right=int(sz.get("right", s.get("safeRight", s.get("safe_right", 0)))),
            safe_bottom=int(sz.get("bottom", s.get("safeBottom", s.get("safe_bottom", 0)))),
            safe_left=int(sz.get("left", s.get("safeLeft", s.get("safe_left", 0)))),
        ))

    if not target_specs:
        target_specs = [TargetSpec(width=0, height=0)]

    return CleanPipelineRequest(
        job_id=job_id,
        source_path=source_path,
        target_specs=target_specs,
        output_directory=output_directory,
    )
