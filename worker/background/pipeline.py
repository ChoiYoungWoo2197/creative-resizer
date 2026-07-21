"""Stage 19 Background Pipeline orchestrator.

Coordinates A→B→C→D→E stages:
  A: Local Background Repair (local_inpaint)
  B: External AI Inpaint     (external_provider)
  C: Background Outpaint     (outpaint)
  D: Shadow Harmonization    (harmonizer)
  E: Background Quality Gate (quality_gate)

Default feature flags:
  BACKGROUND_PIPELINE_ENABLED=false
  BACKGROUND_PIPELINE_COMPARE_ONLY=true

All operations fall back to native result on any error.
"""
from __future__ import annotations

import time
from PIL import Image

from .schemas import (
    BackgroundRequest,
    BackgroundOptions,
    BackgroundResult,
    BackgroundCandidate,
    MaskBuildResult,
)
from .mask_builder import build_masks
from .local_inpaint import (
    generate_local_candidates,
    should_use_local,
    should_promote_to_external,
)
from .external_provider import (
    FakeBackgroundProvider,
    ProviderFactory,
    run_external_inpaint,
)
from .outpaint import generate_outpaint_candidates
from .harmonizer import generate_shadow_candidates
from .quality_gate import select_best_candidate, build_quality_metrics
from .artifact_writer import write_artifacts
from .smart_fit_guard import build_no_smart_fit_fields, NATIVE_BACKGROUND_FALLBACK_FORBIDDEN
from .mode_selector import select_background_mode, SOURCE_FAITHFUL_REPAIR
from .source_faithful_repair import run_source_faithful_repair


def _safe_resize_to_target(img: Image.Image, tgt_w: int, tgt_h: int) -> Image.Image:
    """Letterbox-resize img to (tgt_w, tgt_h). Never stretches.

    Used to guarantee result_image is always target-sized on fallback paths,
    which fulfils the G6 contract without applying non-uniform scale.
    """
    from PIL import ImageFilter
    if img.size == (tgt_w, tgt_h):
        return img
    src_w, src_h = img.size
    scale = min(tgt_w / max(src_w, 1), tgt_h / max(src_h, 1))
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    fg = img.convert("RGB").resize((new_w, new_h), Image.LANCZOS)
    bg = img.convert("RGB").resize((tgt_w, tgt_h), Image.LANCZOS).filter(
        ImageFilter.GaussianBlur(30)
    )
    x = (tgt_w - new_w) // 2
    y = (tgt_h - new_h) // 2
    bg.paste(fg, (x, y))
    return bg


class BackgroundPipeline:
    """Stage 19 Background Pipeline.

    Usage:
        pipeline = BackgroundPipeline()
        result = pipeline.process(request)
    """

    def __init__(self, output_dir: str = "/tmp/stage19-artifacts") -> None:
        self._output_dir = output_dir

    def process(self, request: BackgroundRequest) -> BackgroundResult:
        """Run full pipeline and return BackgroundResult.

        On any unhandled exception, returns native fallback result.
        Never raises — callers must check result.success.
        """
        t0 = time.time()
        opts = request.options
        result = BackgroundResult(
            background_compare_only=opts.compare_only,
            background_application_mode=(
                "compare_only" if opts.compare_only else "apply"
            ),
            background_application_blocked_reason=(
                "compare_only_enabled" if opts.compare_only else ""
            ),
        )

        if not opts.enabled:
            result.fallback_used = True
            result.fallback_reason = "pipeline_disabled"
            result.verdict = "PARTIAL"
            _tgt_w = request.target_width or request.source_image.width
            _tgt_h = request.target_height or request.source_image.height
            result.result_image = _safe_resize_to_target(request.source_image, _tgt_w, _tgt_h)
            result.elapsed_ms = int((time.time() - t0) * 1000)
            return result

        try:
            return self._run(request, result, t0)
        except Exception as exc:
            result.fallback_used = True
            result.fallback_reason = f"pipeline_error:{exc}"
            result.verdict = "FAIL"
            result.success = False
            _tgt_w = request.target_width or request.source_image.width
            _tgt_h = request.target_height or request.source_image.height
            result.result_image = _safe_resize_to_target(request.source_image, _tgt_w, _tgt_h)
            result.warnings.append(f"pipeline_exception:{exc}")
            result.elapsed_ms = int((time.time() - t0) * 1000)
            return result

    def _run(
        self,
        request: BackgroundRequest,
        result: BackgroundResult,
        t0: float,
    ) -> BackgroundResult:
        opts = request.options
        source = request.source_image
        tgt_w = request.target_width or source.width
        tgt_h = request.target_height or source.height
        warnings: list[str] = []
        all_candidates: list[BackgroundCandidate] = []

        # ── Stage 20.2: Smart Fit policy fields (always False) ─────────────────
        no_sf = build_no_smart_fit_fields()
        result.smart_fit_allowed = no_sf["smartFitAllowed"]
        result.smart_fit_used = no_sf["smartFitUsed"]
        result.smart_fit_fallback_used = no_sf["smartFitFallbackUsed"]
        result.blur_fill_used = no_sf["blurFillUsed"]
        result.mirror_fill_used = no_sf["mirrorFillUsed"]
        result.stretch_fill_used = no_sf["stretchFillUsed"]
        result.native_fallback_used = no_sf["nativeFallbackUsed"]

        # ── Stage 20.2: Mode selection ─────────────────────────────────────────
        classified = request.layout_candidate.get("classifiedLayers", [])
        mode, mode_reason = select_background_mode(
            classified,
            source_image=source,
            forced_mode=opts.background_generation_mode or "",
        )
        result.background_generation_mode = mode
        warnings.append(f"backgroundMode:{mode}({mode_reason})")

        # ── Stage 20.2: Source Faithful Repair path ────────────────────────────
        if opts.source_faithful_repair_enabled and mode == SOURCE_FAITHFUL_REPAIR:
            return self._run_source_faithful_repair(
                request, result, t0, tgt_w, tgt_h, classified, warnings
            )

        # ── Step 1: Mask Build ─────────────────────────────────────────────────
        mask_result = build_masks(
            canvas_w=source.width,
            canvas_h=source.height,
            protected_objects=request.protected_objects,
            removal_objects=request.removal_objects or None,
            external_removal_mask=request.removal_mask,
            target_w=tgt_w,
            target_h=tgt_h,
        )
        warnings.extend(mask_result.warnings)
        result.metrics.update({
            "removalMaskAreaRatio":    mask_result.removal_mask_area_ratio,
            "protectedMaskAreaRatio":  mask_result.protected_mask_area_ratio,
            "outpaintMaskAreaRatio":   mask_result.outpaint_mask_area_ratio,
            "maskDilationPx":          mask_result.mask_dilation_px,
            "maskFeatherPx":           mask_result.mask_feather_px,
            "protectedOverlapPixels":  mask_result.protected_overlap_pixels,
        })

        # ── Step 2A: Local Inpaint ─────────────────────────────────────────────
        local_candidates: list[BackgroundCandidate] = []
        _local_eligible = False
        if opts.allow_local_inpaint and mask_result.removal_mask is not None:
            area = mask_result.removal_mask_area_ratio
            _local_eligible = should_use_local(area)
            if _local_eligible:
                result.local_inpaint_attempted = True
                local_candidates = generate_local_candidates(
                    source,
                    mask_result.removal_mask,
                    max_candidates=min(opts.max_candidates, 4),
                )
                all_candidates.extend(local_candidates)
            elif should_promote_to_external(area):
                warnings.append(f"localInpaintSkipped:areaTooLarge({area:.3f})")
        result.metrics["localInpaintEligible"] = _local_eligible
        result.metrics["localCandidateCount"] = len(local_candidates)

        # ── Step 2B: External Inpaint ──────────────────────────────────────────
        if opts.allow_external_inpaint and mask_result.removal_mask is not None:
            result.external_inpaint_attempted = True
            provider = ProviderFactory.create(
                enable_external=True,
                use_fake_for_test=False,
            )
            ext_c = run_external_inpaint(
                source_image=source,
                removal_mask=mask_result.removal_mask,
                provider=provider,
                compare_only=opts.compare_only,
                generation_blocked_mask=mask_result.generation_blocked_mask,
            )
            all_candidates.append(ext_c)

        # ── Step 3C: Outpaint ──────────────────────────────────────────────────
        outpaint_candidates: list[BackgroundCandidate] = []
        if opts.allow_outpaint and (tgt_w != source.width or tgt_h != source.height):
            result.outpaint_attempted = True
            outpaint_candidates = generate_outpaint_candidates(
                source, tgt_w, tgt_h, max_candidates=2,
            )
            all_candidates.extend(outpaint_candidates)

        # ── Step 4D: Shadow / Harmonization ───────────────────────────────────
        shadow_candidates: list[BackgroundCandidate] = []
        if opts.allow_shadow:
            # Apply shadow on top of the source (or best candidate so far)
            product_bbox = {}
            for obj in request.protected_objects:
                if obj.get("role") == "product":
                    product_bbox = obj.get("bbox", {})
                    break
            shadow_candidates = generate_shadow_candidates(
                background=source,
                product_bbox=product_bbox,
                product_mask=mask_result.product_mask,
                allow_shadow=True,
            )
            all_candidates.extend(shadow_candidates)
            if any(c.shadow_applied for c in shadow_candidates):
                result.shadow_applied = True

        # ── Step 5E: Quality Gate ──────────────────────────────────────────────
        best, reject_summary = select_best_candidate(
            all_candidates,
            source_image=source,
            protected_mask=mask_result.protected_mask,
        )

        # ── Build result ───────────────────────────────────────────────────────
        result.candidates = all_candidates
        result.metrics.update(build_quality_metrics(all_candidates, best))
        result.warnings = warnings

        if best is not None:
            result.best_evaluated_background_source = f"{best.provider}:{best.method}"
            result.best_evaluated_background_score  = best.score
            result.selected_candidate_id            = best.candidate_id
            result.selected_background_source       = f"{best.provider}:{best.method}"
            result.external_background_eligible     = any(
                c.provider not in ("local", "native") for c in all_candidates
            )
            # inpaint/outpaint accepted flags
            if best.candidate_id.startswith("local_"):
                result.local_inpaint_accepted = True
            elif best.candidate_id.startswith("external_"):
                result.external_inpaint_accepted = True
            elif best.candidate_id.startswith("outpaint_"):
                result.outpaint_accepted = True

            if opts.compare_only:
                result.applied_background_source = "native"
                result.result_image = source
            else:
                result.applied_background_source = result.selected_background_source
                result.result_image = best.image or source

            result.fallback_used = False
            result.fallback_reason = ""
            result.success = True
            result.verdict = "PASS"
        else:
            # all candidates failed
            result.fallback_used = True
            result.fallback_reason = reject_summary or "all_candidates_rejected"
            result.applied_background_source = "native"
            result.best_evaluated_background_source = "native"
            result.best_evaluated_background_score  = 0.0
            result.success = False
            result.verdict = "PARTIAL"
            warnings.append(f"nativeFallback:{result.fallback_reason}")
            if outpaint_candidates and not any(c.accepted for c in outpaint_candidates):
                warnings.append("outpaint_all_candidates_rejected:external_provider_unavailable")
            # Use letterbox blur only when NOT in source_faithful_repair mode
            if mode == SOURCE_FAITHFUL_REPAIR:
                # Smart Fit / blur-fill forbidden — return error without fallback image
                result.sfr_failure_reason = NATIVE_BACKGROUND_FALLBACK_FORBIDDEN
                result.hard_fail_reasons.append(NATIVE_BACKGROUND_FALLBACK_FORBIDDEN)
                result.result_image = None
                warnings.append(f"{NATIVE_BACKGROUND_FALLBACK_FORBIDDEN}:sfr_mode_no_blur_fallback")
            else:
                result.result_image = _safe_resize_to_target(source, tgt_w, tgt_h)

        # ── Artifacts (Stage 19 path) ─────────────────────────────────────────
        artifact_paths = write_artifacts(
            output_dir=self._output_dir,
            artifact_level=opts.artifact_level,
            source_image=source,
            masks={
                "protected_mask":          mask_result.protected_mask,
                "removal_mask":            mask_result.removal_mask,
                "outpaint_mask":           mask_result.outpaint_mask,
                "generation_allowed_mask": mask_result.generation_allowed_mask,
                "generation_blocked_mask": mask_result.generation_blocked_mask,
                "product_mask":            mask_result.product_mask,
            },
            candidates=all_candidates,
            selected_candidate=best,
            metrics=result.metrics,
            warnings=result.warnings,
            request_id=request.request_id,
            elapsed_ms=int((time.time() - t0) * 1000),
        )
        result.artifacts = artifact_paths
        result.elapsed_ms = int((time.time() - t0) * 1000)
        return result

    def _run_source_faithful_repair(
        self,
        request: BackgroundRequest,
        result: BackgroundResult,
        t0: float,
        tgt_w: int,
        tgt_h: int,
        classified_layers: list[dict],
        warnings: list[str],
    ) -> BackgroundResult:
        """Stage 20.2: Source Faithful Repair path.

        Uses AI to repair only removal + outpaint areas.
        Smart Fit / blur-fill NEVER used as fallback.
        """
        opts = request.options
        source = request.source_image

        # Build provider
        provider = None
        try:
            provider = ProviderFactory.create(
                enable_external=opts.allow_external_inpaint,
                use_fake_for_test=False,
            )
        except Exception as exc:
            warnings.append(f"provider_build_failed:{exc}")

        sfr = run_source_faithful_repair(
            source_image=source,
            classified_layers=classified_layers,
            target_w=tgt_w,
            target_h=tgt_h,
            provider=provider,
            max_attempts=opts.background_ai_max_attempts,
            request_id=request.request_id,
            output_dir=self._output_dir,
            canvas_w=source.width,
            canvas_h=source.height,
        )

        # Map SFR result → BackgroundResult
        result.background_generation_mode = sfr.background_generation_mode
        result.prompt_version = sfr.prompt_version
        result.needs_background_generation = sfr.needs_background_generation
        result.background_ai_required = sfr.background_ai_required
        result.background_ai_executed = sfr.background_ai_executed
        result.background_ai_provider = sfr.background_ai_provider
        result.background_ai_model = sfr.background_ai_model
        result.background_ai_request_id = request.request_id
        result.background_ai_attempt_count = sfr.background_ai_attempt_count
        result.background_ai_succeeded = sfr.background_ai_succeeded
        result.background_ai_candidate_count = sfr.background_ai_candidate_count
        result.background_ai_accepted_count = sfr.background_ai_accepted_count
        result.applied_background_source = sfr.applied_background_source
        result.original_psd_background_used = sfr.original_psd_background_used
        result.generation_allowed_mask_ratio = sfr.generation_allowed_mask_ratio
        result.removal_mask_ratio = sfr.removal_mask_ratio
        result.outpaint_mask_ratio = sfr.outpaint_mask_ratio
        result.immutable_mask_ratio = sfr.immutable_mask_ratio
        result.protected_object_mutation_detected = sfr.protected_object_mutation_detected
        result.visible_hand_mutation_count = sfr.visible_hand_mutation_count
        result.generated_text_detected = sfr.generated_text_detected
        result.generated_logo_detected = sfr.generated_logo_detected
        result.generated_product_detected = sfr.generated_product_detected
        result.unexpected_generated_hand_detected = sfr.unexpected_generated_hand_detected
        result.generated_person_detected = sfr.generated_person_detected
        result.source_faithfulness_score = sfr.source_faithfulness_score
        result.scene_continuity_score = sfr.scene_continuity_score
        result.overall_repair_score = sfr.overall_repair_score
        result.sfr_failure_reason = sfr.failure_reason
        result.hard_fail_reasons = sfr.hard_fail_reasons
        result.warnings = warnings + sfr.warnings
        result.success = sfr.success
        result.verdict = sfr.verdict
        result.result_image = sfr.repair_image
        result.fallback_used = not sfr.success
        result.fallback_reason = sfr.failure_reason if not sfr.success else ""

        result.metrics.update({
            "sourceFaithfulnessScore": sfr.source_faithfulness_score,
            "sceneContinuityScore": sfr.scene_continuity_score,
            "overallRepairScore": sfr.overall_repair_score,
            "aiAttemptCount": sfr.background_ai_attempt_count,
            "generationAllowedMaskRatio": sfr.generation_allowed_mask_ratio,
            "immutableMaskRatio": sfr.immutable_mask_ratio,
        })
        result.elapsed_ms = int((time.time() - t0) * 1000)
        return result
