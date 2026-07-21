"""Stage 19 Background Pipeline schemas.

All classes are plain dataclasses — no external dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Request ───────────────────────────────────────────────────────────────────

@dataclass
class BackgroundOptions:
    enabled: bool = False
    compare_only: bool = True
    allow_local_inpaint: bool = True
    allow_external_inpaint: bool = False
    allow_outpaint: bool = False
    allow_shadow: bool = False
    max_candidates: int = 4
    timeout_seconds: int = 180
    preferred_provider: str = ""
    mask_source: str = "applied"           # "applied" | "best_evaluated"
    artifact_level: str = "standard"       # "none" | "standard" | "full"

    # Stage 20.2 source-faithful repair policy flags
    source_faithful_repair_enabled: bool = False
    background_generation_mode: str = ""          # "" | "source_faithful_repair" | "generative_background"
    smart_fit_allowed: bool = False
    blur_fill_allowed: bool = False
    mirror_fill_allowed: bool = False
    stretch_fill_allowed: bool = False
    background_ai_max_attempts: int = 3
    background_ai_approximate_allowed: bool = True

    @classmethod
    def from_env(cls) -> "BackgroundOptions":
        import os
        def _bool(key: str, default: bool) -> bool:
            v = os.environ.get(key, "").lower()
            return {"true": True, "false": False}.get(v, default)
        def _int(key: str, default: int) -> int:
            try:
                return int(os.environ.get(key, default))
            except (ValueError, TypeError):
                return default
        def _str(key: str, default: str) -> str:
            return os.environ.get(key, default)

        return cls(
            enabled=_bool("BACKGROUND_PIPELINE_ENABLED", False),
            compare_only=_bool("BACKGROUND_PIPELINE_COMPARE_ONLY", True),
            allow_local_inpaint=_bool("BACKGROUND_LOCAL_INPAINT_ENABLED", True),
            allow_external_inpaint=_bool("BACKGROUND_EXTERNAL_INPAINT_ENABLED", False),
            allow_outpaint=_bool("BACKGROUND_OUTPAINT_ENABLED", False),
            allow_shadow=_bool("BACKGROUND_SHADOW_ENABLED", False),
            max_candidates=_int("BACKGROUND_MAX_CANDIDATES", 4),
            timeout_seconds=_int("BACKGROUND_REQUEST_TIMEOUT_SECONDS", 180),
            # Stage 20.2
            source_faithful_repair_enabled=_bool("BACKGROUND_SOURCE_FAITHFUL_REPAIR_ENABLED", False),
            background_generation_mode=_str("BACKGROUND_GENERATION_MODE", ""),
            smart_fit_allowed=_bool("BACKGROUND_SMART_FIT_ALLOWED", False),
            blur_fill_allowed=_bool("BACKGROUND_BLUR_FILL_ALLOWED", False),
            mirror_fill_allowed=_bool("BACKGROUND_MIRROR_FILL_ALLOWED", False),
            stretch_fill_allowed=_bool("BACKGROUND_STRETCH_FILL_ALLOWED", False),
            background_ai_max_attempts=_int("BACKGROUND_AI_MAX_ATTEMPTS", 3),
        )


@dataclass
class BackgroundRequest:
    source_image: Any                          # PIL Image
    target_width: int = 0
    target_height: int = 0
    protected_objects: list[dict] = field(default_factory=list)
    protected_masks: list[dict] = field(default_factory=list)
    removal_mask: Any = None                   # PIL L-mode or None
    removal_objects: list[dict] = field(default_factory=list)  # explicit removal targets
    layout_candidate: dict = field(default_factory=dict)
    safe_zone: dict = field(default_factory=dict)
    options: BackgroundOptions = field(default_factory=BackgroundOptions)
    request_id: str = ""


# ── Candidate ─────────────────────────────────────────────────────────────────

@dataclass
class BackgroundCandidate:
    candidate_id: str = ""
    provider: str = ""
    method: str = ""
    image: Any = None                          # PIL Image or None
    score: float = 0.0
    accepted: bool = False
    rejection_reasons: list[str] = field(default_factory=list)
    # quality
    naturalness_score: float = 0.0
    seam_score: float = 0.0
    color_continuity_score: float = 0.0
    texture_continuity_score: float = 0.0
    protected_pixel_integrity_score: float = 100.0
    product_pixel_integrity_score: float = 100.0
    safe_zone_compliance_score: float = 100.0
    spec_compliance_score: float = 100.0
    # risk
    seam_risk: float = 0.0
    blur_band_risk: float = 0.0
    repetition_risk: float = 0.0
    ghosting_risk: float = 0.0
    halo_risk: float = 0.0
    extra_object_risk: float = 0.0
    product_mutation_risk: float = 0.0
    protected_pixel_mutation_risk: float = 0.0
    floating_object_risk: float = 0.0
    # inpaint-specific
    mask_area_ratio: float = 0.0
    boundary_color_delta: float = 0.0
    boundary_texture_delta: float = 0.0
    # shadow
    shadow_applied: bool = False
    shadow_opacity: float = 0.0
    shadow_naturalness_score: float = 0.0
    # timing
    elapsed_ms: int = 0
    # meta
    extras: dict = field(default_factory=dict)


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class BackgroundResult:
    success: bool = False
    verdict: str = "PENDING"         # "PASS" | "PARTIAL" | "FAIL" | "PENDING"
    selected_candidate_id: str = ""
    selected_background_source: str = ""
    # attempted flags
    local_inpaint_attempted: bool = False
    local_inpaint_accepted: bool = False
    external_inpaint_attempted: bool = False
    external_inpaint_accepted: bool = False
    outpaint_attempted: bool = False
    outpaint_accepted: bool = False
    shadow_applied: bool = False
    harmonization_applied: bool = False
    # fallback
    fallback_used: bool = True
    fallback_reason: str = "pipeline_disabled"
    # Stage 18 style separation
    best_evaluated_background_source: str = "native"
    best_evaluated_background_score: float = 0.0
    external_background_eligible: bool = False
    applied_background_source: str = "native"
    background_application_mode: str = "compare_only"
    background_application_blocked_reason: str = "compare_only_enabled"
    background_compare_only: bool = True
    # candidates & metrics
    candidates: list[BackgroundCandidate] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    # output image
    result_image: Any = None

    # ── Stage 20.2 fields ─────────────────────────────────────────────────────
    background_generation_mode: str = ""
    prompt_version: str = ""
    needs_background_generation: bool = False
    background_ai_required: bool = False
    background_ai_executed: bool = False
    background_ai_provider: str = ""
    background_ai_model: str = ""
    background_ai_request_id: str = ""
    background_ai_attempt_count: int = 0
    background_ai_succeeded: bool = False
    background_ai_candidate_count: int = 0
    background_ai_accepted_count: int = 0
    original_psd_background_used: bool = False
    generation_allowed_mask_ratio: float = 0.0
    removal_mask_ratio: float = 0.0
    outpaint_mask_ratio: float = 0.0
    immutable_mask_ratio: float = 0.0
    smart_fit_allowed: bool = False
    smart_fit_used: bool = False
    smart_fit_fallback_used: bool = False
    blur_fill_used: bool = False
    mirror_fill_used: bool = False
    stretch_fill_used: bool = False
    native_fallback_used: bool = False
    protected_object_mutation_detected: bool = False
    visible_hand_mutation_count: int = 0
    generated_text_detected: bool = False
    generated_logo_detected: bool = False
    generated_product_detected: bool = False
    unexpected_generated_hand_detected: bool = False
    generated_person_detected: bool = False
    source_faithfulness_score: float = 0.0
    scene_continuity_score: float = 0.0
    overall_repair_score: float = 0.0
    sfr_failure_reason: str = ""
    hard_fail_reasons: list = field(default_factory=list)


# ── Mask metadata ─────────────────────────────────────────────────────────────

@dataclass
class MaskBuildResult:
    protected_mask: Any = None      # PIL L "255=protected"
    product_mask: Any = None
    person_mask: Any = None
    text_mask: Any = None
    logo_mask: Any = None
    cta_mask: Any = None
    removal_mask: Any = None        # "255=to inpaint"
    outpaint_mask: Any = None       # "255=canvas expansion"
    generation_allowed_mask: Any = None
    generation_blocked_mask: Any = None
    # metadata
    removal_mask_area_ratio: float = 0.0
    protected_mask_area_ratio: float = 0.0
    outpaint_mask_area_ratio: float = 0.0
    mask_dilation_px: int = 0
    mask_feather_px: int = 0
    mask_touches_protected_object: bool = False
    protected_overlap_pixels: int = 0
    warnings: list[str] = field(default_factory=list)
