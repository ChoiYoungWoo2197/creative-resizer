"""Adapts CleanPipelineResult → Worker result_items format."""
from __future__ import annotations

import os

from clean_pipeline.contracts import CleanPipelineResult, PipelineStatus


def adapt_response(
    result: CleanPipelineResult,
    specs_raw: list[dict],
) -> tuple[list[dict], list[str]]:
    """Convert CleanPipelineResult to (result_items, missing_ratio_types).

    clean_pipeline processes target_specs[0] only — always produces exactly 1 result item.
    missing_ratio_types contains the slug/media of specs that produced no valid output.
    """
    succeeded = result.status == PipelineStatus.PASS

    failed_stage = ""
    if not succeeded and result.stage_results:
        for sr in result.stage_results:
            if sr.status == PipelineStatus.FAIL:
                failed_stage = sr.stage.value
                break

    spec = specs_raw[0] if specs_raw else {}
    slug = spec.get("slug") or ""
    media = spec.get("media") or ""
    name = spec.get("name") or ""
    width = spec.get("width") or 0
    height = spec.get("height") or 0

    base = {
        "renderSource": "clean_pipeline",
        "fallbackUsed": False,
        "pipelineVersion": "clean_v1",
        "media": media,
        "name": name,
        "slug": slug,
        "width": width,
        "height": height,
    }

    if succeeded and result.output_paths:
        file_path = result.output_paths[0]
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = None
        item = {
            **base,
            "filePath": file_path,
            "fileName": os.path.basename(file_path),
            "fileSize": file_size,
            "valid": True,
        }
        return [item], []

    slug_key = slug or media or "unknown"
    item = {
        **base,
        "filePath": "",
        "fileName": "",
        "valid": False,
        "failedStage": failed_stage,
        "failureCode": result.failure_code,
        "error": result.failure_message,
    }
    return [item], [slug_key]
