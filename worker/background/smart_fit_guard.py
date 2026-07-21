"""Stage 20.2: Smart Fit and blur-fill runtime guard.

All error codes that must appear in responses when these techniques are blocked.
Smart Fit, blur-fill, mirrored-edge, and stretched-texture are prohibited as
final output. They are allowed only for debug/benchmark/regression-compare contexts.
"""
from __future__ import annotations

# ── Error codes ───────────────────────────────────────────────────────────────

SMART_FIT_FORBIDDEN = "SMART_FIT_FORBIDDEN"
SMART_FIT_RUNTIME_CALL_BLOCKED = "SMART_FIT_RUNTIME_CALL_BLOCKED"
BLUR_FILL_FORBIDDEN = "BLUR_FILL_FORBIDDEN"
MIRROR_FILL_FORBIDDEN = "MIRROR_FILL_FORBIDDEN"
STRETCH_FILL_FORBIDDEN = "STRETCH_FILL_FORBIDDEN"
NATIVE_BACKGROUND_FALLBACK_FORBIDDEN = "NATIVE_BACKGROUND_FALLBACK_FORBIDDEN"

# Contexts where smart-fit / blur-fill are still allowed (non-final-output use)
_ALLOWED_CONTEXTS = frozenset({
    "debug",
    "benchmark",
    "regression_compare",
    "compare_only",
    "legacy_compare",
})


class SmartFitForbiddenError(RuntimeError):
    """Raised when smart-fit / blur-fill is called in a forbidden final-output context."""

    def __init__(self, context: str = "", technique: str = "smart_fit") -> None:
        self.error_code = SMART_FIT_RUNTIME_CALL_BLOCKED
        self.context = context
        self.technique = technique
        super().__init__(
            f"{SMART_FIT_FORBIDDEN}: {technique} blocked in context={context!r}. "
            "Smart Fit / blur-fill is never allowed as final output."
        )


def is_final_output_context(context: str) -> bool:
    """Return True if this context is a final-output path — Smart Fit forbidden."""
    return context not in _ALLOWED_CONTEXTS


def check_smart_fit_allowed(context: str = "default", technique: str = "smart_fit") -> None:
    """Raise SmartFitForbiddenError if technique is used in a forbidden context."""
    if is_final_output_context(context):
        raise SmartFitForbiddenError(context=context, technique=technique)


def build_no_smart_fit_fields() -> dict:
    """Return required response fields asserting Smart Fit was NOT used."""
    return {
        "smartFitAllowed": False,
        "smartFitUsed": False,
        "smartFitFallbackUsed": False,
        "blurFillUsed": False,
        "mirrorFillUsed": False,
        "stretchFillUsed": False,
        "nativeFallbackUsed": False,
    }


def build_blocked_result(technique: str, context: str, target_w: int, target_h: int) -> dict:
    """Build a minimal failure result dict when Smart Fit is blocked."""
    return {
        **build_no_smart_fit_fields(),
        "success": False,
        "error": SMART_FIT_FORBIDDEN,
        "errorCode": SMART_FIT_RUNTIME_CALL_BLOCKED,
        "blockedTechnique": technique,
        "blockedContext": context,
        "targetWidth": target_w,
        "targetHeight": target_h,
    }
