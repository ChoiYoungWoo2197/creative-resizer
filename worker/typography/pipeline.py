"""Stage 20: Typography Pipeline — main orchestrator.

Coordinates: role_resolver → text_extractor → duplicate_detector →
             cta_layout → layout_templates → compositor → quality_gate → artifact_writer.

Entry point: run_typography_pipeline(file_path, target_w, target_h, output_path, ...)
Always returns a dict; success field indicates whether to use the output or fall back.

Feature flag: TYPOGRAPHY_PIPELINE_ENABLED (default false).
"""
from __future__ import annotations
import os
import sys
import time

from PIL import Image

from .schemas import TypographyResult
from .role_resolver import resolve_roles, resolve_korean_text_roles, get_role_stats
from .text_extractor import extract_text_layers, count_korean_layers
from .duplicate_detector import detect_duplicates, count_deduped
from .cta_layout import detect_cta_groups
from .layout_templates import get_template, slots_as_dict
from .compositor import compose, save_result, _build_fallback_bg
from .quality_gate import evaluate
from .artifact_writer import write_artifacts


def _is_enabled() -> bool:
    return os.environ.get("TYPOGRAPHY_PIPELINE_ENABLED", "false").lower() == "true"


def _open_psd(file_path: str):
    """Open PSD using psd_compat if available, else psd_tools directly."""
    # Add worker dir to sys.path if needed
    worker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
    try:
        from psd_compat import open_psd_safe_with_patch
        psd, meta = open_psd_safe_with_patch(file_path)
        if meta.get("success"):
            return psd, None
        return None, meta.get("error", "psd open failed")
    except ImportError:
        pass
    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(file_path)
        return psd, None
    except Exception as e:
        return None, str(e)


def run_typography_pipeline(
    file_path: str,
    target_w: int,
    target_h: int,
    output_path: str,
    debug_dir: str | None = None,
    output_format: str = "jpg",
    user_role_overrides: dict | None = None,
    artifact_level: str = "minimal",
    job_id: str = "",
) -> dict:
    """Run Stage 20 Typography Pipeline.

    Returns dict with keys:
      success, error, template, detectedRoles, missingRoles, usedLayerRoles,
      extractedLayerCount, koreanLayers, dedupRemovedCount,
      ctaGroupDetected, safeZonePass, safeZoneViolations, qualityScore,
      layoutScore, warnings, outputPath, elapsedMs, artifacts
    """
    t0 = time.time()
    base = {
        "success": False,
        "error": None,
        "template": None,
        "detectedRoles": [],
        "missingRoles": [],
        "usedLayerRoles": [],
        "extractedLayerCount": 0,
        "koreanLayers": 0,
        "dedupRemovedCount": 0,
        "ctaGroupDetected": False,
        "safeZonePass": True,
        "safeZoneViolations": [],
        "qualityScore": 0.0,
        "layoutScore": 0.0,
        "warnings": [],
        "outputPath": None,
        "elapsedMs": 0,
        "artifacts": {},
    }

    if not _is_enabled():
        base["error"] = "typography_pipeline_disabled"
        return base

    # ── Step 0: Open PSD ──────────────────────────────────────────────────────
    psd, open_error = _open_psd(file_path)
    if psd is None:
        base["error"] = f"psd_open_failed:{open_error}"
        return base

    # ── Step 1: Parse layers ──────────────────────────────────────────────────
    try:
        worker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, worker_dir) if worker_dir not in sys.path else None
        from psd_layer_parser import parse_psd_layers
        job_dir = debug_dir or os.path.join(os.path.dirname(output_path), "typography_debug")
        raw_layers = parse_psd_layers(psd, job_dir)
    except Exception as e:
        base["error"] = f"parse_layers_failed:{e}"
        return base

    base["extractedLayerCount"] = len(raw_layers)
    if not raw_layers:
        base["error"] = "no_renderable_layers"
        return base

    # ── Step 2: Role resolution ───────────────────────────────────────────────
    classified = resolve_roles(raw_layers, user_role_overrides)
    stats = get_role_stats(classified)
    base["detectedRoles"] = stats["roles"]
    print(f"[Typography] classified {stats['known']}/{stats['total']} "
          f"rate={stats['classifyRate']} roles={stats['roles']}")

    # ── Step 3: Text extraction ───────────────────────────────────────────────
    classified = extract_text_layers(classified)
    korean_count = count_korean_layers(classified)
    base["koreanLayers"] = korean_count
    had_korean = korean_count > 0

    # ── Step 3.5: Re-resolve Korean raster text layer roles ───────────────────
    classified = resolve_korean_text_roles(classified)
    # Update detected roles after re-resolution
    stats2 = get_role_stats(classified)
    base["detectedRoles"] = stats2["roles"]

    # ── Step 4: Duplicate detection ───────────────────────────────────────────
    classified = detect_duplicates(classified)
    dedup_count = count_deduped(classified)
    base["dedupRemovedCount"] = dedup_count

    # ── Step 5: CTA group detection ───────────────────────────────────────────
    cta_groups = detect_cta_groups(classified)
    cta_from_role = any(l.get("role") == "cta" and not l.get("dedupSkip") for l in classified)
    base["ctaGroupDetected"] = len(cta_groups) > 0 or cta_from_role

    # ── Step 6: Layout template ───────────────────────────────────────────────
    template_name, slots = get_template(target_w, target_h, classified)
    base["template"] = template_name
    print(f"[Typography] template={template_name} slots={len(slots)}")

    # ── Step 7: Fallback background ───────────────────────────────────────────
    fallback_bg = _build_fallback_bg(psd, target_w, target_h)

    # ── Step 8: Compose ───────────────────────────────────────────────────────
    try:
        canvas = compose(classified, slots, target_w, target_h, fallback_bg)
    except Exception as e:
        base["error"] = f"compose_failed:{e}"
        return base

    # ── Step 9: Quality gate ──────────────────────────────────────────────────
    result: TypographyResult = evaluate(
        classified, slots, target_w, target_h,
        had_korean=had_korean,
        dedup_count=dedup_count,
        cta_group_detected=base["ctaGroupDetected"],
    )
    base.update({
        "detectedRoles": result.detected_roles,
        "missingRoles": result.missing_roles,
        "usedLayerRoles": result.used_layer_roles,
        "safeZonePass": result.safe_zone_pass,
        "safeZoneViolations": result.safe_zone_violations,
        "qualityScore": result.quality_score,
        "layoutScore": result.layout_score,
        "warnings": result.warnings,
    })

    if not result.success:
        base["error"] = result.error
        base["elapsedMs"] = int((time.time() - t0) * 1000)
        # Write debug artifacts even on failure for diagnosis
        try:
            art_dir = debug_dir or os.path.join(os.path.dirname(os.path.abspath(output_path)), "typography_debug")
            os.makedirs(art_dir, exist_ok=True)
            fail_path = os.path.join(art_dir, f"failed_composite_{target_w}x{target_h}.jpg")
            canvas.convert("RGB").save(fail_path, quality=85)
            base["artifacts"] = {"failed_composite": fail_path}
            write_artifacts(
                output_dir=art_dir,
                source_image=None,
                result_image=None,
                classified=classified,
                slots=slots,
                result_meta=base,
                job_id=job_id,
                artifact_level="minimal",
            )
        except Exception:
            pass
        return base

    # ── Step 10: Save output ──────────────────────────────────────────────────
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        save_result(canvas, output_path, output_format)
        base["outputPath"] = output_path
        base["success"] = True
    except Exception as e:
        base["error"] = f"save_failed:{e}"
        base["elapsedMs"] = int((time.time() - t0) * 1000)
        return base

    # ── Step 11: Artifacts ────────────────────────────────────────────────────
    try:
        source_img: Image.Image | None = None
        try:
            source_img = psd.composite()
            if source_img:
                source_img = source_img.convert("RGBA")
        except Exception:
            pass
        art_dir = debug_dir or os.path.join(os.path.dirname(output_path), "typography_debug")
        artifacts = write_artifacts(
            output_dir=art_dir,
            source_image=source_img,
            result_image=canvas,
            classified=classified,
            slots=slots,
            result_meta=base,
            job_id=job_id,
            artifact_level=artifact_level,
        )
        base["artifacts"] = artifacts
    except Exception:
        pass

    base["elapsedMs"] = int((time.time() - t0) * 1000)
    print(f"[Typography] done template={template_name} score={result.quality_score} "
          f"elapsed={base['elapsedMs']}ms")
    return base
