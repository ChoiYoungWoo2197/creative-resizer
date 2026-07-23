"""Bundle D-1: Serialization helpers for SemanticSceneCleanupResult."""
from __future__ import annotations

from scene_cleanup.models import SemanticSceneCleanupResult, SceneCanvasTransform


def serialize_canvas_transform(t: SceneCanvasTransform | None) -> dict:
    if t is None:
        return {}
    return {
        "strategy": t.strategy,
        "sourceW": t.source_w,
        "sourceH": t.source_h,
        "canvasW": t.canvas_w,
        "canvasH": t.canvas_h,
        "scale": t.scale,
        "cropX": t.crop_x,
        "cropY": t.crop_y,
        "outpaintRequired": t.outpaint_required,
        "maskStrategy": t.mask_strategy,
    }


def serialize_scene_cleanup_result(r: SemanticSceneCleanupResult) -> dict:
    """JSON-safe dict for debug artifacts."""
    return {
        "success": r.success,
        "failureReason": r.failure_reason,
        "providerName": r.provider_name,
        "providerModel": r.provider_model,
        "providerInputSource": r.provider_input_source,
        "promptVersion": r.prompt_version,
        "promptSha256": r.prompt_sha256[:16] if r.prompt_sha256 else "",
        "scenePlateSha256": r.scene_plate_sha256[:16] if r.scene_plate_sha256 else "",
        "scenePlatePath": r.scene_plate_path,
        "canvasTransform": serialize_canvas_transform(r.canvas_transform),
        "attemptCount": r.attempt_count,
        "actualProviderRequestCount": r.actual_provider_request_count,
        "d2Required": r.d2_required,
        "d2Reason": r.d2_reason,
        "sourceW": r.source_w,
        "sourceH": r.source_h,
        "targetW": r.target_w,
        "targetH": r.target_h,
    }


def extract_d1_provenance_fields(r: SemanticSceneCleanupResult) -> dict:
    """D-1 specific fields injected into renderProvenance."""
    return {
        "d1ProviderInputSource": r.provider_input_source,
        "d1PromptVersion": r.prompt_version,
        "d1PromptSha256": r.prompt_sha256[:16] if r.prompt_sha256 else "",
        "d1ScenePlateSha256": r.scene_plate_sha256[:16] if r.scene_plate_sha256 else "",
        "d1AttemptCount": r.attempt_count,
        "d1D2Required": r.d2_required,
        "d1D2Reason": r.d2_reason,
        "d1CanvasTransform": serialize_canvas_transform(r.canvas_transform),
        "d1SourceW": r.source_w,
        "d1SourceH": r.source_h,
    }
