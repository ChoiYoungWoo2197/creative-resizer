"""Stage 21 Bundle C-1: Overall verdict aggregator.

Aggregates technical/extraction/composition/layout/visual verdicts
into a single Stage21VerdictSummary with fail-closed semantics.
"""
from __future__ import annotations

from verdict.models import (
    VerdictResult, Stage21VerdictSummary,
    PASS, FAIL, NOT_TESTED, NOT_APPLICABLE,
)
from verdict import reason_codes as RC

# Required verdict names in C-1
_C1_REQUIRED_VERDICTS = ("technicalVerdict", "extractionVerdict",
                         "compositionVerdict", "layoutVerdict")


def aggregate_stage21_verdict(
    technical: VerdictResult,
    extraction: VerdictResult,
    composition: VerdictResult,
    layout: VerdictResult,
    visual: VerdictResult,
    job_id: str = "",
    spec_id: str = "",
    visual_required: bool = False,
) -> Stage21VerdictSummary:
    """Compute overall verdict with fail-closed aggregation.

    Overall PASS conditions (ALL must hold):
      1. All required verdicts are PASS
         (NOT_APPLICABLE is allowed if genuinely inapplicable)
      2. At least one required verdict is PASS
         (prevents all-NOT_APPLICABLE masking as success)

    Overall FAIL triggers:
      - Any required verdict == FAIL
      - Any required verdict == NOT_TESTED
      - All required verdicts == NOT_APPLICABLE simultaneously

    visualVerdict: NOT required in C-1 (visual_required=False, default).
    When visual_required=True (production with VISUAL_VERDICT_ENABLED=true):
      NOT_TESTED or FAIL visual verdict causes overall FAIL.
    """
    required_results = {
        "technicalVerdict":    technical,
        "extractionVerdict":   extraction,
        "compositionVerdict":  composition,
        "layoutVerdict":       layout,
    }
    # E-4: visual is required in production when visual_required=True
    if visual_required:
        required_results["visualVerdict"] = visual

    failed_names: list[str] = []
    not_tested_names: list[str] = []
    overall_reason_codes: list[str] = []

    for name, vr in required_results.items():
        if vr.status == FAIL:
            failed_names.append(name)
        elif vr.status == NOT_TESTED:
            not_tested_names.append(name)

    # All required are NOT_APPLICABLE → disallowed structure
    all_na = all(vr.status == NOT_APPLICABLE for vr in required_results.values())

    # Determine overall status
    if failed_names:
        overall_status = FAIL
        overall_reason_codes.append(RC.OVERALL_REQUIRED_VERDICT_FAILED)
        overall_reason_codes.extend(
            rc for name in failed_names
            for rc in required_results[name].reasonCodes
        )
    elif not_tested_names:
        overall_status = FAIL
        overall_reason_codes.append(RC.OVERALL_REQUIRED_VERDICT_NOT_TESTED)
        for name in not_tested_names:
            overall_reason_codes.extend(required_results[name].reasonCodes)
    elif all_na:
        overall_status = FAIL
        overall_reason_codes.append(RC.OVERALL_ALL_REQUIRED_NOT_APPLICABLE)
    else:
        # All required verdicts are PASS or NOT_APPLICABLE (at least one PASS)
        overall_status = PASS

    overall_reason_codes_sorted = sorted(set(overall_reason_codes))

    _effective_required = list(required_results.keys())
    print(
        f"[STAGE21_VERDICT]"
        f" jobId={job_id} specId={spec_id}"
        f" overallStatus={overall_status}"
        f" requiredVerdicts={_effective_required}"
        f" visualRequired={visual_required}"
        f" failedVerdicts={failed_names}"
        f" notTestedVerdicts={not_tested_names}"
        f" reasonCodes={overall_reason_codes_sorted}"
        f" technicalVerdict={technical.status}"
        f" extractionVerdict={extraction.status}"
        f" compositionVerdict={composition.status}"
        f" layoutVerdict={layout.status}"
        f" visualVerdict={visual.status}",
        flush=True,
    )

    if not visual_required:
        print(
            f"[VERDICT_VISUAL]"
            f" jobId={job_id} specId={spec_id}"
            f" status=NOT_TESTED"
            f" reasonCodes=['VISUAL_NOT_TESTED']",
            flush=True,
        )

    return Stage21VerdictSummary(
        technicalVerdict=technical,
        extractionVerdict=extraction,
        compositionVerdict=composition,
        layoutVerdict=layout,
        visualVerdict=visual,
        overallStatus=overall_status,
        overallReasonCodes=overall_reason_codes_sorted,
        requiredVerdicts=list(_C1_REQUIRED_VERDICTS),
        failedVerdicts=failed_names,
        notTestedVerdicts=not_tested_names,
    )
