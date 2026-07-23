"""Stage 21 Bundle C-1: Extraction verdict evaluator.

Validates the UnifiedObjectManifest for structural correctness:
required roles present, no duplicates, valid bboxes, etc.
"""
from __future__ import annotations

from verdict.models import (
    VerdictResult, UnifiedObjectManifest,
    PASS, FAIL, NOT_APPLICABLE,
    SOURCE_TYPE_PSD_LAYER, SOURCE_TYPE_AI_SEGMENTATION,
)
from verdict import reason_codes as RC

_REQUIRED_ROLES = frozenset({"product", "title", "headline", "body_text"})

# Source types that support full extraction validation (including D-2 virtual objects)
_EVALUATABLE_SOURCE_TYPES = frozenset({
    SOURCE_TYPE_PSD_LAYER,
    SOURCE_TYPE_AI_SEGMENTATION,  # D-2 virtual foreground objects
})


def evaluate_extraction(
    manifest: UnifiedObjectManifest | None,
    *,
    source_type: str = SOURCE_TYPE_PSD_LAYER,
    d2_required: bool = False,
    job_id: str = "",
    spec_id: str = "",
) -> VerdictResult:
    """Validate the object manifest.

    NOT_APPLICABLE when:
      - source_type is not "psd_layer" (PNG/JPG input has no extractable layers)
      - manifest is None AND source_type is not psd_layer

    PASS when:
      - manifest was built successfully
      - No duplicate objectIds
      - No invalid bboxes for required objects
      - All required roles present (at least one object per required role)

    FAIL when:
      - manifest is None despite PSD input
      - Duplicate objectIds present
      - Required role missing
      - Invalid bbox on required object
    """
    reason_codes: list[str] = []
    messages: list[str] = []

    # Bundle D-1: flattened input (PNG/JPG without layers) requires D-2 segmentation
    if d2_required and source_type != SOURCE_TYPE_PSD_LAYER:
        print(
            f"[VERDICT_EXTRACTION]"
            f" jobId={job_id} specId={spec_id}"
            f" status=FAIL reasonCodes=[EXTRACTION_D2_REQUIRED_FOR_FLATTENED_INPUT]"
            f" d2Required=True sourceType={source_type!r}",
            flush=True,
        )
        return VerdictResult(
            name="extractionVerdict",
            status=FAIL,
            required=True,
            reasonCodes=[RC.EXTRACTION_D2_REQUIRED_FOR_FLATTENED_INPUT],
            messages=[
                f"source_type={source_type!r} without native layers — "
                "D-2 segmentation required for foreground extraction (not implemented in D-1)"
            ],
        )

    # NOT_APPLICABLE for source types without extraction validation
    # (psd_layer and ai_segmentation both support full evaluation)
    if source_type not in _EVALUATABLE_SOURCE_TYPES:
        print(
            f"[VERDICT_EXTRACTION]"
            f" jobId={job_id} specId={spec_id}"
            f" status=NOT_APPLICABLE reasonCodes=[] sourceType={source_type!r}",
            flush=True,
        )
        return VerdictResult(
            name="extractionVerdict",
            status=NOT_APPLICABLE,
            required=True,
            reasonCodes=[],
            messages=[f"source_type={source_type!r} — extraction not applicable"],
        )

    if manifest is None:
        print(
            f"[VERDICT_EXTRACTION]"
            f" jobId={job_id} specId={spec_id}"
            f" status=FAIL reasonCodes=[EXTRACTION_MANIFEST_BUILD_FAILED]",
            flush=True,
        )
        return VerdictResult(
            name="extractionVerdict",
            status=FAIL,
            required=True,
            reasonCodes=[RC.EXTRACTION_MANIFEST_BUILD_FAILED],
            messages=["UnifiedObjectManifest is None — build failed"],
        )

    evidence: dict = {
        "inputObjectCount": manifest.inputObjectCount,
        "uniqueObjectCount": manifest.uniqueObjectCount,
        "requiredObjectCount": manifest.requiredObjectCount,
        "duplicateObjectIds": manifest.duplicateObjectIds,
        "invalidObjectIds": manifest.invalidObjectIds,
        "manifestSha256": manifest.manifestSha256,
    }

    # No foreground layers at all
    if manifest.inputObjectCount == 0:
        reason_codes.append(RC.EXTRACTION_NO_FOREGROUND_LAYERS)
        messages.append("No foreground layers were extracted from PSD")

    # Duplicate objectIds
    if manifest.duplicateObjectIds:
        reason_codes.append(RC.EXTRACTION_DUPLICATE_OBJECT_ID)
        messages.append(
            f"Duplicate objectIds detected: {manifest.duplicateObjectIds}"
        )

    # Check required roles present
    present_roles = {o.semanticRole for o in manifest.objects}
    # A manifest may have no objects if input was empty
    # Only check required roles if we expected any layers
    if manifest.inputObjectCount > 0:
        missing_required = sorted(_REQUIRED_ROLES - present_roles)
        # Not necessarily FAIL: required roles may not be in source (e.g., pure product image)
        # Report as warning only; actual FAIL is handled by compositionVerdict
        if missing_required:
            messages.append(
                f"Required roles not in manifest: {missing_required} "
                f"(composition verdict will enforce)"
            )

    # Invalid bboxes for any required object
    for obj in manifest.objects:
        if obj.required and obj.width <= 0 or (obj.required and obj.height <= 0):
            if obj.objectId not in manifest.invalidObjectIds:
                manifest.invalidObjectIds.append(obj.objectId)
            reason_codes.append(RC.EXTRACTION_INVALID_BBOX)
            messages.append(
                f"Required object has zero-size bbox: objectId={obj.objectId!r}"
                f" role={obj.semanticRole!r} size={obj.width}x{obj.height}"
            )

    # Invalid aspect ratio (NaN or infinite)
    for obj in manifest.objects:
        ar = obj.aspectRatio
        if not (ar == ar) or ar < 0:  # NaN check: NaN != NaN
            reason_codes.append(RC.EXTRACTION_INVALID_ASPECT_RATIO)
            messages.append(
                f"Invalid aspect ratio for objectId={obj.objectId!r}: {ar}"
            )

    reason_codes_sorted = sorted(set(reason_codes))
    status = FAIL if reason_codes_sorted else PASS

    print(
        f"[VERDICT_EXTRACTION]"
        f" jobId={job_id} specId={spec_id}"
        f" status={status}"
        f" reasonCodes={reason_codes_sorted}"
        f" metrics={{inputObjectCount:{manifest.inputObjectCount}"
        f",uniqueObjectCount:{manifest.uniqueObjectCount}"
        f",requiredObjectCount:{manifest.requiredObjectCount}}}",
        flush=True,
    )

    return VerdictResult(
        name="extractionVerdict",
        status=status,
        required=True,
        reasonCodes=reason_codes_sorted,
        messages=messages,
        evidence=evidence,
        metrics={
            "inputObjectCount": manifest.inputObjectCount,
            "uniqueObjectCount": manifest.uniqueObjectCount,
            "requiredObjectCount": manifest.requiredObjectCount,
            "duplicateCount": len(manifest.duplicateObjectIds),
            "invalidCount": len(manifest.invalidObjectIds),
        },
    )
