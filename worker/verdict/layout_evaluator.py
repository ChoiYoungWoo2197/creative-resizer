"""Stage 21 Bundle C-1: Layout verdict evaluator.

Consumes Bundle B LayoutPlanResult and reports layout quality:
safe zone compliance, candidate selection, required object placement,
clipping, overlap.
"""
from __future__ import annotations

from verdict.models import (
    VerdictResult, PASS, FAIL, NOT_APPLICABLE, NOT_TESTED,
    SOURCE_TYPE_PSD_LAYER,
)
from verdict import reason_codes as RC


def evaluate_layout(
    layout_plan,  # LayoutPlanResult from layout.reflow_engine or None
    *,
    source_type: str = SOURCE_TYPE_PSD_LAYER,
    safe_zone_status: str = "",   # safeZoneParseStatus from spec
    job_id: str = "",
    spec_id: str = "",
) -> VerdictResult:
    """Evaluate Bundle B layout plan result.

    NOT_APPLICABLE when:
      - source_type is not "psd_layer" (PNG/JPG path has no layout planning)

    NOT_TESTED when:
      - source_type == "psd_layer" but layout_plan is None (engine error)

    FAIL when:
      - layout_plan.success is False
      - hardFailReasons is non-empty
      - selectedCandidateId is missing
      - required objects not placed (allRequiredObjectsPlaced=False)
      - safe zone violation for spec with parsed safe zone
      - required object clipped
      - forbidden overlap detected

    PASS when:
      - All above checks clear
    """
    reason_codes: list[str] = []
    messages: list[str] = []

    if source_type != SOURCE_TYPE_PSD_LAYER:
        _log_layout(job_id, spec_id, NOT_APPLICABLE, [], {})
        return VerdictResult(
            name="layoutVerdict",
            status=NOT_APPLICABLE,
            required=True,
            reasonCodes=[],
            messages=[f"source_type={source_type!r} — layout not applicable"],
        )

    if layout_plan is None:
        _log_layout(job_id, spec_id, NOT_TESTED, [RC.LAYOUT_ENGINE_ERROR], {})
        return VerdictResult(
            name="layoutVerdict",
            status=NOT_TESTED,
            required=True,
            reasonCodes=[RC.LAYOUT_ENGINE_ERROR],
            messages=["Layout plan is None — layout engine failed or was not invoked"],
        )

    # ── Evaluate layout_plan fields ──────────────────────────────────────────
    sz_available = getattr(layout_plan, "safeZoneAvailable", False)
    sz_enforced = getattr(layout_plan, "safeZoneEnforced", False)
    sz_violation_count = getattr(layout_plan, "safeZoneViolationCount", 0)
    clipping_violation_count = getattr(layout_plan, "clippingViolationCount", 0)
    overlap_violation_count = getattr(layout_plan, "overlapViolationCount", 0)
    hard_fail_reasons = getattr(layout_plan, "hardFailReasons", []) or []
    all_required_placed = getattr(layout_plan, "allRequiredObjectsPlaced", False)
    selected_id = getattr(layout_plan, "selectedCandidateId", "") or ""
    success = getattr(layout_plan, "success", False)
    warnings = getattr(layout_plan, "warnings", []) or []

    # Missing candidate → hard fail
    if not selected_id:
        reason_codes.append(RC.LAYOUT_NO_VALID_CANDIDATE)
        messages.append("No selectedCandidateId — no valid candidate was found")

    # Hard fails from layout engine
    if hard_fail_reasons:
        reason_codes.append(RC.LAYOUT_HARD_FAIL)
        messages.append(f"Layout hard fails: {hard_fail_reasons}")

    # Safe zone unavailable (when spec has parsed safe zone)
    sz_parse_status = safe_zone_status or ""
    if sz_parse_status in ("parsed_text", "parsed_diagram") and not sz_available:
        reason_codes.append(RC.LAYOUT_SAFE_ZONE_UNAVAILABLE)
        messages.append(
            f"Spec requires safe zone (status={sz_parse_status!r})"
            " but safeZoneAvailable=False"
        )
    elif not sz_available:
        messages.append(
            "Safe zone not available — ratio-based fallback or no spec data"
            " (warning only)"
        )

    # Safe zone violations (when enforced)
    if sz_enforced and sz_violation_count > 0:
        reason_codes.append(RC.LAYOUT_SAFE_ZONE_VIOLATION)
        messages.append(
            f"Safe zone violations for required objects: count={sz_violation_count}"
        )

    # Required object clipped
    if clipping_violation_count > 0:
        reason_codes.append(RC.LAYOUT_REQUIRED_OBJECT_CLIPPED)
        messages.append(
            f"Required object clipping violations: count={clipping_violation_count}"
        )

    # Overlap violations (hard fails covered separately; soft overlap here)
    if overlap_violation_count > 0:
        # Hard fail overlaps already in hard_fail_reasons
        # If we have overlap violations AND hard fails, already counted
        if not hard_fail_reasons:
            reason_codes.append(RC.LAYOUT_FORBIDDEN_OVERLAP)
            messages.append(
                f"Forbidden overlap violations: count={overlap_violation_count}"
            )

    # Required objects not placed
    if not all_required_placed:
        reason_codes.append(RC.LAYOUT_REQUIRED_OBJECT_MISSING)
        messages.append("allRequiredObjectsPlaced=False — required object was not placed")

    # Overall layout success flag
    if not success and not reason_codes:
        reason_codes.append(RC.LAYOUT_PLAN_FAILED)
        messages.append("layout_plan.success=False (no specific failure identified)")

    reason_codes_sorted = sorted(set(reason_codes))
    status = FAIL if reason_codes_sorted else PASS

    metrics = {
        "selectedCandidateId": selected_id,
        "safeZoneAvailable": sz_available,
        "safeZoneEnforced": sz_enforced,
        "safeZoneViolationCount": sz_violation_count,
        "clippingViolationCount": clipping_violation_count,
        "overlapViolationCount": overlap_violation_count,
        "allRequiredObjectsPlaced": all_required_placed,
        "hardFailCount": len(hard_fail_reasons),
        "layoutSuccess": success,
    }

    _log_layout(job_id, spec_id, status, reason_codes_sorted, metrics)

    return VerdictResult(
        name="layoutVerdict",
        status=status,
        required=True,
        reasonCodes=reason_codes_sorted,
        messages=messages,
        evidence={
            "hardFailReasons": hard_fail_reasons,
            "warnings": warnings,
        },
        metrics=metrics,
    )


def _log_layout(job_id, spec_id, status, reason_codes, metrics):
    print(
        f"[VERDICT_LAYOUT]"
        f" jobId={job_id} specId={spec_id}"
        f" status={status}"
        f" reasonCodes={reason_codes}"
        f" selectedCandidateId={metrics.get('selectedCandidateId', '?')!r}"
        f" safeZoneAvailable={metrics.get('safeZoneAvailable', '?')}"
        f" safeZoneEnforced={metrics.get('safeZoneEnforced', '?')}"
        f" safeZoneViolationCount={metrics.get('safeZoneViolationCount', '?')}"
        f" clippingViolationCount={metrics.get('clippingViolationCount', '?')}"
        f" overlapViolationCount={metrics.get('overlapViolationCount', '?')}",
        flush=True,
    )
