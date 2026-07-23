"""Stage 21 Bundle C-1: Technical verdict evaluator.

Pure function — takes primitive values derived from the SFR result and
output file metadata.  No PIL Image, no provider objects.
"""
from __future__ import annotations

from verdict.models import VerdictResult, PASS, FAIL, NOT_TESTED
from verdict import reason_codes as RC

_FORBIDDEN_PROVIDERS = frozenset({
    "smart-fit", "smart_fit", "blur-fill", "blur_fill",
    "native", "none", "",
})


def evaluate_technical(
    *,
    output_path: str | None,
    output_size: tuple[int, int] | None,
    file_size: int,
    target_w: int,
    target_h: int,
    ai_provider: str,
    fail_closed: bool,
    exception_occurred: bool,
    blurFillUsed: bool,
    forcedSmartFit: bool,
    job_id: str = "",
    spec_id: str = "",
) -> VerdictResult:
    """Evaluate technical execution quality.

    PASS conditions:
      - No exception
      - output_path is not None (file was created)
      - file_size > 0
      - output_size == (target_w, target_h)
      - ai_provider is not a forbidden fallback
      - fail_closed is True
      - blurFillUsed is False
      - forcedSmartFit is False
    """
    reason_codes: list[str] = []
    messages: list[str] = []
    evidence: dict = {
        "outputPath": output_path or "",
        "outputSize": list(output_size) if output_size else [],
        "fileSize": file_size,
        "targetSize": [target_w, target_h],
        "aiProvider": ai_provider,
        "failClosed": fail_closed,
        "exceptionOccurred": exception_occurred,
        "blurFillUsed": blurFillUsed,
        "forcedSmartFit": forcedSmartFit,
    }

    if exception_occurred:
        reason_codes.append(RC.TECH_EXCEPTION_OCCURRED)
        messages.append("Worker exception occurred during processing")

    if not output_path:
        reason_codes.append(RC.TECH_OUTPUT_MISSING)
        messages.append("Output file path is None — no output produced")

    if output_path and file_size <= 0:
        reason_codes.append(RC.TECH_OUTPUT_FILE_EMPTY)
        messages.append(f"Output file is empty: size={file_size}")

    if output_size is not None:
        aw, ah = output_size
        if aw != target_w or ah != target_h:
            reason_codes.append(RC.TECH_OUTPUT_SIZE_INVALID)
            messages.append(
                f"Output size mismatch: expected={target_w}x{target_h} actual={aw}x{ah}"
            )
    elif output_path:
        reason_codes.append(RC.TECH_OUTPUT_DECODE_FAILED)
        messages.append("Output image could not be decoded for size check")

    if not fail_closed:
        reason_codes.append(RC.TECH_FAIL_CLOSED_VIOLATED)
        messages.append("failClosed flag is False — policy violated")

    if blurFillUsed:
        reason_codes.append(RC.TECH_BLUR_FILL_FALLBACK)
        messages.append("blur-fill fallback was used (forbidden in ai-only mode)")

    if forcedSmartFit:
        reason_codes.append(RC.TECH_SMART_FIT_FALLBACK)
        messages.append("Smart Fit fallback was forced (forbidden in ai-only mode)")

    if not ai_provider:
        reason_codes.append(RC.TECH_NO_AI_PROVIDER)
        messages.append("AI provider is empty — no AI provider configured")
    elif ai_provider.lower().replace("-", "_") in {
        p.replace("-", "_") for p in _FORBIDDEN_PROVIDERS if p
    }:
        reason_codes.append(RC.TECH_FALLBACK_USED)
        messages.append(f"AI provider is a forbidden fallback: {ai_provider!r}")

    status = FAIL if reason_codes else PASS
    reason_codes_sorted = sorted(set(reason_codes))

    print(
        f"[VERDICT_TECHNICAL]"
        f" jobId={job_id} specId={spec_id}"
        f" status={status}"
        f" reasonCodes={reason_codes_sorted}"
        f" metrics={{fileSize:{file_size},provider:{ai_provider!r}}}",
        flush=True,
    )

    return VerdictResult(
        name="technicalVerdict",
        status=status,
        required=True,
        reasonCodes=reason_codes_sorted,
        messages=messages,
        evidence=evidence,
        metrics={
            "fileSize": file_size,
            "targetW": target_w,
            "targetH": target_h,
            "aiProvider": ai_provider,
        },
    )
