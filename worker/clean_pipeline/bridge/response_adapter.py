"""Adapts CleanPipelineResult → Worker result_items format."""
from __future__ import annotations

from worker.clean_pipeline.contracts import CleanPipelineResult, PipelineStatus


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

    base = {
        "renderSource": "clean_pipeline",
        "fallbackUsed": False,
        "pipelineVersion": "clean_v1",
    }

    if succeeded and result.output_paths:
        item = {**base, "filePath": result.output_paths[0], "valid": True}
        return [item], []

    spec = specs_raw[0] if specs_raw else {}
    slug = spec.get("slug") or spec.get("media") or "unknown"
    item = {
        **base,
        "filePath": "",
        "valid": False,
        "failedStage": failed_stage,
        "failureCode": result.failure_code,
        "error": result.failure_message,
    }
    return [item], [slug]
