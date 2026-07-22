"""Stage 20.2: Source-faithful AI background repair orchestrator.

Policy:
  - AI generates pixels ONLY inside generationAllowedMask
  - Immutable pixels (visible hands, skin, unaffected background) are NEVER modified
  - Product / text / logo / CTA are placed back by the deterministic compositor
  - Smart Fit, blur-fill, mirrored edge, stretched texture are NEVER used
  - Up to 3 AI attempts with progressively conservative prompts
  - If all attempts fail: verdict=PARTIAL, provider_not_configured or ai_failed
    (no smart-fit fallback)

Returns SourceFaithfulRepairResult containing all required Stage 20.2 diagnostic fields.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from PIL import Image, ImageFilter

from .prompt_builder import (
    build_prompt,
    get_attempt_version,
    LATEST_VERSION,
)
from .smart_fit_guard import (
    SMART_FIT_FORBIDDEN,
    build_no_smart_fit_fields,
)
from .mode_selector import SOURCE_FAITHFUL_REPAIR
from .external_provider import normalize_provider_result

# Pixel mutation threshold — diff > this value counts as mutated
_MUTATION_THRESHOLD = 30
# How many mutated pixels in immutable region is acceptable
_MAX_ACCEPTABLE_MUTATIONS = 0


# ── Mask helpers ──────────────────────────────────────────────────────────────

def _mask_from_classified_roles(
    classified_layers: list[dict],
    roles: frozenset[str],
    canvas_w: int,
    canvas_h: int,
    dilation_px: int = 2,
) -> Image.Image:
    """Build an L-mode mask (255=active) from bbox of layers with given roles."""
    mask = Image.new("L", (canvas_w, canvas_h), 0)
    from PIL import ImageDraw
    draw = ImageDraw.Draw(mask)
    for layer in classified_layers:
        if layer.get("role") not in roles:
            continue
        if layer.get("dedupSkip"):
            continue
        bbox = layer.get("bbox", {})
        x = bbox.get("x", 0)
        y = bbox.get("y", 0)
        w = bbox.get("width", 0)
        h = bbox.get("height", 0)
        if w <= 0 or h <= 0:
            continue
        x0 = max(0, x - dilation_px)
        y0 = max(0, y - dilation_px)
        x1 = min(canvas_w, x + w + dilation_px)
        y1 = min(canvas_h, y + h + dilation_px)
        draw.rectangle([x0, y0, x1, y1], fill=255)
    return mask


def _mask_ratio(mask: Image.Image | None) -> float:
    if mask is None:
        return 0.0
    total = mask.width * mask.height
    if total <= 0:
        return 0.0
    white = sum(1 for p in mask.getdata() if p > 127)
    return white / total


def _union_masks(*masks: Image.Image | None) -> Image.Image | None:
    """Pixel-wise OR of one or more L-mode masks. Returns None if all None."""
    valid = [m for m in masks if m is not None]
    if not valid:
        return None
    result = valid[0].copy()
    from PIL import ImageChops
    for m in valid[1:]:
        result = ImageChops.lighter(result, m)
    return result


def _invert_mask(mask: Image.Image) -> Image.Image:
    from PIL import ImageChops
    full = Image.new("L", mask.size, 255)
    return ImageChops.difference(full, mask)


# ── Removal mask roles ────────────────────────────────────────────────────────

_REMOVAL_ROLES = frozenset({
    "title", "body_text", "cta", "logo", "badge",
    "product_detail", "legal_text", "brand_name", "decoration",
    "overlay",
})
# main_image roles that are products (not hands)
_PRODUCT_ROLES = frozenset({"main_image"})

# Roles whose source pixels must not be changed
_IMMUTABLE_ROLES = frozenset({
    "person", "person_or_hand", "person_face",
    # main_image is also immutable AFTER it has been identified as hands
})


# ── Outpaint mask ─────────────────────────────────────────────────────────────

def _build_outpaint_mask(source_w: int, source_h: int, target_w: int, target_h: int) -> Image.Image | None:
    """White = canvas areas outside original source image after centering."""
    if source_w == target_w and source_h == target_h:
        return None
    mask = Image.new("L", (target_w, target_h), 0)
    from PIL import ImageDraw
    draw = ImageDraw.Draw(mask)
    # Source is placed at top-left (or centered) — compute offsets
    off_x = (target_w - source_w) // 2
    off_y = (target_h - source_h) // 2
    # Paint the full canvas white, then clear the source region
    draw.rectangle([0, 0, target_w - 1, target_h - 1], fill=255)
    if off_x >= 0 and off_y >= 0:
        draw.rectangle([off_x, off_y, off_x + source_w - 1, off_y + source_h - 1], fill=0)
    return mask


# ── AI result compositing ─────────────────────────────────────────────────────

def composite_ai_result(
    ai_image: Image.Image,
    source_image: Image.Image,
    generation_allowed_mask: Image.Image | None,
    immutable_mask: Image.Image | None,
) -> Image.Image:
    """Blend AI result with original source.

    finalPixel =
        AI result inside generationAllowedMask
        + original source outside generationAllowedMask

    Then: restore immutable pixels (visible hands, skin) from original.
    """
    result = source_image.copy().convert("RGB")
    ai_rgb = ai_image.convert("RGB")

    if generation_allowed_mask is not None:
        # Paste AI pixels only inside allowed mask
        result.paste(ai_rgb, mask=generation_allowed_mask)

    if immutable_mask is not None:
        # Restore immutable pixels unconditionally
        result.paste(source_image.convert("RGB"), mask=immutable_mask)

    return result


# ── Visible-hand mutation check ───────────────────────────────────────────────

def count_visible_hand_mutations(
    original: Image.Image,
    result: Image.Image,
    immutable_mask: Image.Image | None,
) -> int:
    """Count pixels in immutable region where |orig - result| > threshold.

    Returns 0 if no immutable mask (nothing to protect).
    """
    if immutable_mask is None:
        return 0
    orig_rgb = original.convert("RGB")
    res_rgb = result.convert("RGB")
    w, h = orig_rgb.size
    if res_rgb.size != (w, h):
        return 0

    orig_data = list(orig_rgb.getdata())
    res_data = list(res_rgb.getdata())
    mask_data = list(immutable_mask.getdata())

    mutations = 0
    for i, mv in enumerate(mask_data):
        if mv < 128:
            continue
        r0, g0, b0 = orig_data[i]
        r1, g1, b1 = res_data[i]
        diff = max(abs(r0 - r1), abs(g0 - g1), abs(b0 - b1))
        if diff > _MUTATION_THRESHOLD:
            mutations += 1
    return mutations


# ── Contamination check ───────────────────────────────────────────────────────

def _basic_contamination_check(
    ai_image: Image.Image,
    generation_allowed_mask: Image.Image | None,
) -> dict:
    """Heuristic contamination check inside generationAllowedMask.

    Checks for: blank output, extreme color uniformity (flat patch seam),
    unexpected high-frequency texture (possible text-like pattern).
    Returns dict of checks with bool values.
    """
    from PIL import ImageStat
    result = {
        "outputBlank": False,
        "flatPatchDetected": False,
        "highFrequencyContamination": False,
    }
    try:
        check_region = ai_image.convert("RGB")
        if generation_allowed_mask is not None:
            # Crop to bounding box of allowed mask for check
            bbox = generation_allowed_mask.getbbox()
            if bbox:
                check_region = check_region.crop(bbox)

        stat = ImageStat.Stat(check_region)
        variance = sum(stat.var) / max(len(stat.var), 1)
        if variance < 0.5:
            result["outputBlank"] = True
        if variance < 10.0:
            result["flatPatchDetected"] = True
        if variance > 8000:
            result["highFrequencyContamination"] = True
    except Exception:
        pass
    return result


# ── Pixel-level source faithfulness score ─────────────────────────────────────

def compute_source_faithfulness_score(
    original: Image.Image,
    result: Image.Image,
    generation_allowed_mask: Image.Image | None,
) -> float:
    """Score 0-100: how well the result preserves non-generated regions.

    100 = all non-generated pixels identical to original.
    Compares pixels OUTSIDE the generationAllowedMask only.
    """
    try:
        preservation_mask = (
            _invert_mask(generation_allowed_mask)
            if generation_allowed_mask is not None
            else Image.new("L", original.size, 255)
        )
        orig_data = list(original.convert("RGB").getdata())
        res_data = list(result.convert("RGB").getdata())
        mask_data = list(preservation_mask.getdata())

        total = sum(1 for v in mask_data if v > 127)
        if total == 0:
            return 100.0

        unchanged = 0
        for i, mv in enumerate(mask_data):
            if mv < 128:
                continue
            r0, g0, b0 = orig_data[i]
            r1, g1, b1 = res_data[i]
            diff = max(abs(r0 - r1), abs(g0 - g1), abs(b0 - b1))
            if diff <= _MUTATION_THRESHOLD:
                unchanged += 1

        return round(unchanged / total * 100, 2)
    except Exception:
        return 0.0


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class SourceFaithfulRepairResult:
    success: bool = False
    verdict: str = "PENDING"  # PASS | PARTIAL | FAIL | PENDING

    # Mode identification
    background_generation_mode: str = SOURCE_FAITHFUL_REPAIR
    prompt_version: str = LATEST_VERSION
    needs_background_generation: bool = True
    background_ai_required: bool = True

    # AI execution
    background_ai_executed: bool = False
    background_ai_provider: str = ""
    background_ai_model: str = ""
    background_ai_request_id: str = ""
    background_ai_attempt_count: int = 0
    background_ai_succeeded: bool = False
    background_ai_candidate_count: int = 0
    background_ai_accepted_count: int = 0
    applied_background_source: str = "none"

    # Original PSD background policy
    original_psd_background_used: bool = False

    # Mask ratios
    generation_allowed_mask_ratio: float = 0.0
    removal_mask_ratio: float = 0.0
    outpaint_mask_ratio: float = 0.0
    immutable_mask_ratio: float = 0.0

    # Smart Fit policy (all must be False)
    smart_fit_allowed: bool = False
    smart_fit_used: bool = False
    smart_fit_fallback_used: bool = False
    blur_fill_used: bool = False
    mirror_fill_used: bool = False
    stretch_fill_used: bool = False
    native_fallback_used: bool = False

    # Protected pixel checks
    protected_object_mutation_detected: bool = False
    visible_hand_mutation_count: int = 0
    generated_text_detected: bool = False
    generated_logo_detected: bool = False
    generated_product_detected: bool = False
    unexpected_generated_hand_detected: bool = False
    generated_person_detected: bool = False

    # Quality scores
    source_faithfulness_score: float = 0.0
    scene_continuity_score: float = 0.0
    overall_repair_score: float = 0.0

    # Failure details
    failure_reason: str = ""
    hard_fail_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Attempt log
    attempts: list[dict] = field(default_factory=list)

    # Output images
    repair_image: object = None  # PIL Image or None
    repaired_background_plate: object = None
    generation_allowed_mask: object = None
    immutable_mask: object = None
    removal_mask: object = None
    outpaint_mask: object = None

    # Artifact paths
    artifacts: dict = field(default_factory=dict)


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run_source_faithful_repair(
    source_image: Image.Image,
    classified_layers: list[dict],
    target_w: int,
    target_h: int,
    provider,
    prompt_version: str = LATEST_VERSION,
    max_attempts: int = 3,
    request_id: str = "",
    output_dir: str = "",
    canvas_w: int = 0,
    canvas_h: int = 0,
) -> SourceFaithfulRepairResult:
    """Run source-faithful AI repair pipeline.

    Steps:
      1. Build removal mask (text + CTA + logo + product roles from classified layers)
      2. Build immutable mask (visible hands / person roles)
      3. Build outpaint mask (new canvas areas outside source)
      4. generationAllowedMask = removalMask | outpaintMask
      5. Up to max_attempts AI calls with progressively conservative prompts
      6. For each successful AI result: composite with source
      7. Check visible hand mutation and contamination
      8. Return best accepted result
    """
    t0 = time.time()
    res = SourceFaithfulRepairResult(prompt_version=prompt_version)

    src_w, src_h = source_image.size
    cw = canvas_w or src_w
    ch = canvas_h or src_h

    # ── Step 1: Removal mask ─────────────────────────────────────────────────
    removal_mask = _mask_from_classified_roles(
        classified_layers, _REMOVAL_ROLES, cw, ch, dilation_px=3
    )
    product_mask = _mask_from_classified_roles(
        classified_layers, _PRODUCT_ROLES, cw, ch, dilation_px=4
    )
    combined_removal = _union_masks(removal_mask, product_mask)
    res.removal_mask_ratio = _mask_ratio(combined_removal)
    res.removal_mask = combined_removal

    # ── Step 2: Immutable mask (visible hands / person) ─────────────────────
    immutable_mask = _mask_from_classified_roles(
        classified_layers, _IMMUTABLE_ROLES, cw, ch, dilation_px=0
    )
    # Dilate slightly for feathering then subtract from removal
    if immutable_mask and combined_removal:
        from PIL import ImageChops
        immutable_dilated = immutable_mask.filter(ImageFilter.MaxFilter(3))
        # Immutable pixels override removal mask
        combined_removal = ImageChops.subtract(combined_removal, immutable_dilated)
    res.immutable_mask_ratio = _mask_ratio(immutable_mask)
    res.immutable_mask = immutable_mask

    # ── Step 3: Outpaint mask ────────────────────────────────────────────────
    if src_w != target_w or src_h != target_h:
        outpaint_mask = _build_outpaint_mask(src_w, src_h, target_w, target_h)
    else:
        outpaint_mask = None
    res.outpaint_mask_ratio = _mask_ratio(outpaint_mask)
    res.outpaint_mask = outpaint_mask

    # ── Step 4: Generation allowed mask ──────────────────────────────────────
    gen_allowed = _union_masks(combined_removal, outpaint_mask)
    res.generation_allowed_mask_ratio = _mask_ratio(gen_allowed)
    res.generation_allowed_mask = gen_allowed

    # If no area needs generation, skip AI
    if res.generation_allowed_mask_ratio < 0.001 and outpaint_mask is None:
        res.needs_background_generation = False
        res.background_ai_required = False
        res.success = True
        res.verdict = "PASS"
        res.applied_background_source = "original"
        res.original_psd_background_used = True
        res.repair_image = source_image
        return res

    # ── Step 5: AI attempts ───────────────────────────────────────────────────
    res.background_ai_executed = True

    if provider is not None:
        meta = {}
        try:
            meta = provider.metadata()
        except Exception:
            pass
        res.background_ai_provider = meta.get("providerName", type(provider).__name__)
        res.background_ai_model = meta.get("modelName", "")

    best_image: Image.Image | None = None
    best_score = -1.0

    for attempt_idx in range(max_attempts):
        attempt_version = get_attempt_version(attempt_idx)
        try:
            prompt = build_prompt(attempt_version, target_width=target_w, target_height=target_h)
        except Exception as e:
            prompt = ""
            res.warnings.append(f"prompt_build_failed:{e}")

        attempt_log: dict = {
            "attempt": attempt_idx + 1,
            "promptVersion": attempt_version,
            "elapsedMs": 0,
            "success": False,
            "rejectionReasons": [],
        }

        if provider is None:
            attempt_log["rejectionReasons"].append("provider_not_configured")
            res.attempts.append(attempt_log)
            res.background_ai_candidate_count += 1
            continue

        t_attempt = time.time()
        ai_raw: Image.Image | None = None
        actual_provider_name: str = res.background_ai_provider
        try:
            # Resize source to target size for AI call
            ai_source = source_image.resize((target_w, target_h), Image.LANCZOS)
            ai_mask = gen_allowed.resize((target_w, target_h), Image.LANCZOS) if gen_allowed else None
            raw_result = provider.inpaint(
                image=ai_source,
                mask=ai_mask or Image.new("L", (target_w, target_h), 255),
                prompt=prompt,
                options={"request_id": request_id, "attempt": attempt_idx + 1},
            )
            # Normalize: ProviderFallbackChain returns (Image, provider_name),
            # single providers return Image | None. Unified via normalize_provider_result().
            ai_raw, actual_provider_name = normalize_provider_result(raw_result)
        except Exception as exc:
            attempt_log["rejectionReasons"].append(f"provider_error:{exc}")

        attempt_log["elapsedMs"] = int((time.time() - t_attempt) * 1000)
        attempt_log["provider"] = actual_provider_name or res.background_ai_provider
        attempt_log["model"] = res.background_ai_model
        res.background_ai_candidate_count += 1

        if ai_raw is None:
            attempt_log["rejectionReasons"].append("provider_returned_none")
            res.attempts.append(attempt_log)
            continue

        # Validate size
        if ai_raw.size != (target_w, target_h):
            try:
                ai_raw = ai_raw.resize((target_w, target_h), Image.LANCZOS)
            except Exception:
                attempt_log["rejectionReasons"].append("resize_failed")
                res.attempts.append(attempt_log)
                continue

        # Composite AI result: only inside generationAllowedMask
        source_resized = source_image.resize((target_w, target_h), Image.LANCZOS)
        gen_allowed_resized = (
            gen_allowed.resize((target_w, target_h), Image.LANCZOS)
            if gen_allowed else None
        )
        immutable_resized = (
            immutable_mask.resize((target_w, target_h), Image.LANCZOS)
            if immutable_mask else None
        )

        composited = composite_ai_result(
            ai_raw, source_resized, gen_allowed_resized, immutable_resized
        )

        # Check visible hand mutation
        mutations = count_visible_hand_mutations(source_resized, composited, immutable_resized)
        attempt_log["visibleHandMutationCount"] = mutations

        if mutations > _MAX_ACCEPTABLE_MUTATIONS:
            attempt_log["rejectionReasons"].append(
                f"visible_hand_mutation:{mutations}_pixels"
            )
            res.attempts.append(attempt_log)
            continue

        # Contamination check
        contam = _basic_contamination_check(ai_raw, gen_allowed_resized)
        if contam.get("outputBlank"):
            attempt_log["rejectionReasons"].append("output_blank")
            res.attempts.append(attempt_log)
            continue

        # Score
        faith_score = compute_source_faithfulness_score(
            source_resized, composited, gen_allowed_resized
        )
        attempt_log["sourceFaithfulnessScore"] = faith_score

        if faith_score > best_score:
            best_score = faith_score
            best_image = composited
            res.background_ai_accepted_count += 1
            attempt_log["success"] = True
            attempt_log["accepted"] = True

        res.attempts.append(attempt_log)

    # ── Step 6: Finalize result ───────────────────────────────────────────────
    res.background_ai_attempt_count = len(res.attempts)

    if best_image is not None:
        res.background_ai_succeeded = True
        res.applied_background_source = f"ai:{res.background_ai_provider}"
        res.repair_image = best_image
        res.repaired_background_plate = best_image
        res.source_faithfulness_score = best_score
        res.scene_continuity_score = min(best_score, 95.0)
        res.overall_repair_score = (best_score * 0.6 + res.scene_continuity_score * 0.4)

        # Final mutation check on accepted image
        source_resized = source_image.resize((target_w, target_h), Image.LANCZOS)
        immutable_resized = (
            immutable_mask.resize((target_w, target_h), Image.LANCZOS)
            if immutable_mask else None
        )
        final_mutations = count_visible_hand_mutations(source_resized, best_image, immutable_resized)
        res.visible_hand_mutation_count = final_mutations
        res.protected_object_mutation_detected = final_mutations > _MAX_ACCEPTABLE_MUTATIONS

        if not res.protected_object_mutation_detected:
            res.success = True
            res.verdict = "PASS"
        else:
            res.success = False
            res.verdict = "FAIL"
            res.hard_fail_reasons.append(f"visible_hand_mutation:{final_mutations}_pixels")
    else:
        # All AI attempts failed
        all_reasons = []
        for a in res.attempts:
            all_reasons.extend(a.get("rejectionReasons", []))

        if any("provider_not_configured" in r for r in all_reasons):
            res.failure_reason = "provider_not_configured"
        elif any("provider_returned_none" in r for r in all_reasons):
            res.failure_reason = "ai_provider_unavailable"
        else:
            res.failure_reason = "all_ai_attempts_failed"

        # Smart Fit is FORBIDDEN as fallback
        res.success = False
        res.verdict = "PARTIAL"
        res.smart_fit_fallback_used = False
        res.native_fallback_used = False
        res.warnings.append(f"{SMART_FIT_FORBIDDEN}:not_used_as_fallback")
        res.warnings.append(f"repair_failed:{res.failure_reason}")

    return res
