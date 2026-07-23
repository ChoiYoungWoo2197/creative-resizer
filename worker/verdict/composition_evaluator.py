"""Stage 21 Bundle C-1: Composition verdict evaluator.

Validates that foreground objects were composited correctly:
no duplicates, required objects placed, each unique object composited exactly once.

Separates the following invariants (previously conflated):
  noDuplicateComposition:        duplicate_count == 0
  allRequiredObjectsPlaced:      no required-role object was skipped
  allUniqueObjectsPlaced:        all unique FG objects were placed (none skipped)
  allObjectsCompositedOnce:      noDuplicateComposition AND all placed==1 AND none skipped
"""
from __future__ import annotations

from verdict.models import (
    VerdictResult, UnifiedObjectManifest,
    PASS, FAIL, NOT_APPLICABLE,
    SOURCE_TYPE_PSD_LAYER, SOURCE_TYPE_AI_SEGMENTATION, OWNER_FOREGROUND_REFLOW,
)
from verdict import reason_codes as RC

_REQUIRED_ROLES = frozenset({"product", "title", "headline", "body_text"})
_FOREGROUND_OWNER_ROLES = frozenset({OWNER_FOREGROUND_REFLOW})

# Source types that support composition evaluation (psd_layer + D-2 virtual)
_EVALUATABLE_SOURCE_TYPES = frozenset({
    SOURCE_TYPE_PSD_LAYER,
    SOURCE_TYPE_AI_SEGMENTATION,
})


def evaluate_composition(
    fg_result,  # ForegroundCompositeResult or None
    manifest: UnifiedObjectManifest | None,
    *,
    source_type: str = SOURCE_TYPE_PSD_LAYER,
    job_id: str = "",
    spec_id: str = "",
) -> VerdictResult:
    """Validate foreground composition counters and completeness.

    NOT_APPLICABLE when:
      - source_type is not "psd_layer"
      - fg_result is None AND manifest has no objects

    FAIL when:
      - fg_result has duplicate_count > 0
      - Any required-role object was skipped (no image, out-of-bounds, paste error)
      - Any object was composited != 1 time (0 or >1) unless legitimately excluded
      - Counter invariants violated
    """
    reason_codes: list[str] = []
    messages: list[str] = []

    if source_type not in _EVALUATABLE_SOURCE_TYPES:
        print(
            f"[VERDICT_COMPOSITION]"
            f" jobId={job_id} specId={spec_id}"
            f" status=NOT_APPLICABLE sourceType={source_type!r}",
            flush=True,
        )
        return VerdictResult(
            name="compositionVerdict",
            status=NOT_APPLICABLE,
            required=True,
            reasonCodes=[],
            messages=[f"source_type={source_type!r} — composition not applicable"],
        )

    # If both fg_result and manifest are absent/empty → NOT_APPLICABLE
    manifest_empty = (manifest is None or manifest.inputObjectCount == 0)
    fg_absent = (fg_result is None)
    if fg_absent and manifest_empty:
        print(
            f"[VERDICT_COMPOSITION]"
            f" jobId={job_id} specId={spec_id}"
            f" status=NOT_APPLICABLE (no fg layers)",
            flush=True,
        )
        return VerdictResult(
            name="compositionVerdict",
            status=NOT_APPLICABLE,
            required=True,
            reasonCodes=[],
            messages=["No foreground layers to compose — not applicable"],
        )

    # fg_result None but we expected composition → FAIL
    if fg_result is None:
        reason_codes.append(RC.COMPOSITION_COMPOSITOR_FAILED)
        messages.append("Compositor returned None despite fg layers being present")
        _log_composition(job_id, spec_id, FAIL, reason_codes, {})
        return VerdictResult(
            name="compositionVerdict",
            status=FAIL,
            required=True,
            reasonCodes=sorted(set(reason_codes)),
            messages=messages,
        )

    # ── Gather object_manifest from compositor ───────────────────────────────
    obj_manifest = getattr(fg_result, "object_manifest", []) or []

    unique_count = getattr(fg_result, "unique_object_count", 0)
    duplicate_count = getattr(fg_result, "duplicate_count", 0)
    duplicate_ids = getattr(fg_result, "duplicate_object_ids", []) or []

    # Composited (placed) and skipped entries
    placed_entries = [e for e in obj_manifest if e.get("compositedCount", 0) == 1]
    skipped_entries = [e for e in obj_manifest if "skippedReason" in e]
    multi_composited = [e for e in obj_manifest if e.get("compositedCount", 0) > 1]
    zero_composited = [
        e for e in obj_manifest
        if e.get("compositedCount", 0) == 0 and "skippedReason" not in e
    ]

    # Placed object count (objects, not roles)
    placed_object_count = len(placed_entries)
    skipped_object_count = len(skipped_entries)

    # noDuplicateComposition
    no_dup = duplicate_count == 0
    if not no_dup:
        reason_codes.append(RC.COMPOSITION_DUPLICATE_OBJECT)
        messages.append(
            f"Duplicate objectIds composited: {duplicate_ids}"
        )

    # allRequiredObjectsPlaced
    skipped_required = [
        e for e in skipped_entries if e.get("role", "") in _REQUIRED_ROLES
    ]
    all_required_placed = len(skipped_required) == 0
    if skipped_required:
        reason_codes.append(RC.COMPOSITION_REQUIRED_OBJECT_SKIPPED)
        messages.append(
            f"Required objects skipped: "
            f"{[{e.get('role'), e.get('skippedReason')} for e in skipped_required]}"
        )

    # allUniqueObjectsPlaced (no foreground object was skipped at all)
    all_unique_placed = len(skipped_entries) == 0

    # Objects composited multiple times
    if multi_composited:
        reason_codes.append(RC.COMPOSITION_OBJECT_COMPOSITED_MULTIPLE_TIMES)
        messages.append(
            f"Objects composited >1 time: {[e.get('objectId') for e in multi_composited]}"
        )

    # Objects with compositedCount==0 and no skippedReason (unexplained)
    if zero_composited:
        reason_codes.append(RC.COMPOSITION_OBJECT_NOT_COMPOSITED)
        messages.append(
            f"Objects with compositedCount=0 and no skippedReason: "
            f"{[e.get('objectId') for e in zero_composited]}"
        )

    # Counter invariant: placed + skipped == unique
    counter_ok = (placed_object_count + skipped_object_count == unique_count)
    if unique_count > 0 and not counter_ok:
        reason_codes.append(RC.COMPOSITION_COUNTER_MISMATCH)
        messages.append(
            f"Counter mismatch: placed({placed_object_count})"
            f"+skipped({skipped_object_count}) != unique({unique_count})"
        )

    # allObjectsCompositedOnce
    all_composited_once = (
        no_dup
        and all_required_placed
        and all_unique_placed
        and not multi_composited
        and not zero_composited
    )

    reason_codes_sorted = sorted(set(reason_codes))
    status = FAIL if reason_codes_sorted else PASS

    metrics = {
        "inputObjectCount": getattr(fg_result, "layer_count", 0),
        "uniqueObjectCount": unique_count,
        "placedObjectCount": placed_object_count,
        "skippedObjectCount": skipped_object_count,
        "duplicateCount": duplicate_count,
        "allRequiredObjectsPlaced": all_required_placed,
        "allUniqueObjectsPlaced": all_unique_placed,
        "noDuplicateComposition": no_dup,
        "allObjectsCompositedOnce": all_composited_once,
    }

    _log_composition(job_id, spec_id, status, reason_codes_sorted, metrics)

    return VerdictResult(
        name="compositionVerdict",
        status=status,
        required=True,
        reasonCodes=reason_codes_sorted,
        messages=messages,
        evidence={
            "duplicateObjectIds": duplicate_ids,
            "skippedRequiredObjects": [
                {"objectId": e.get("objectId"), "role": e.get("role"),
                 "reason": e.get("skippedReason")}
                for e in skipped_required
            ],
        },
        metrics=metrics,
    )


def _log_composition(job_id, spec_id, status, reason_codes, metrics):
    print(
        f"[VERDICT_COMPOSITION]"
        f" jobId={job_id} specId={spec_id}"
        f" status={status}"
        f" reasonCodes={reason_codes}"
        f" inputObjectCount={metrics.get('inputObjectCount', '?')}"
        f" uniqueObjectCount={metrics.get('uniqueObjectCount', '?')}"
        f" requiredObjectCount={metrics.get('requiredObjectCount', '?')}"
        f" placedObjectCount={metrics.get('placedObjectCount', '?')}"
        f" skippedObjectCount={metrics.get('skippedObjectCount', '?')}"
        f" duplicateCount={metrics.get('duplicateCount', '?')}"
        f" allRequiredObjectsPlaced={metrics.get('allRequiredObjectsPlaced', '?')}"
        f" allUniqueObjectsPlaced={metrics.get('allUniqueObjectsPlaced', '?')}"
        f" noDuplicateComposition={metrics.get('noDuplicateComposition', '?')}"
        f" allObjectsCompositedOnce={metrics.get('allObjectsCompositedOnce', '?')}",
        flush=True,
    )
