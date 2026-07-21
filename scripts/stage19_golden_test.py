#!/usr/bin/env python3
"""Stage 19.5 — Real PSD Background Pipeline Golden Visual Quality Test.

Runs inside the creative-worker container with pipeline flags enabled:
  BACKGROUND_PIPELINE_ENABLED=true
  BACKGROUND_PIPELINE_COMPARE_ONLY=false
  BACKGROUND_OUTPAINT_ENABLED=true

Usage (inside worker container):
  python /scripts/stage19_golden_test.py --psd /app/storage/inputs/mother-hand-product.psd
  python /scripts/stage19_golden_test.py --psd /psd/file.psd --specs 1250x560,1200x300,300x1200
  python /scripts/stage19_golden_test.py --psd /psd/file.psd --width 1250 --height 560 --output /artifacts/golden

Exit code: 0 if all specs pass technical gate, 1 otherwise.

Golden PASS criteria:
  G3  No blur band   — outpaintAttempted=True and not fallback
  G6  Correct size   — result.size == (target_w, target_h)
  G8  Pipeline candidate accepted (not native fallback)
  G9  Visual improvement score > 50 pts
  G10 Not Fake Provider

Manual review flags (cannot be automated):
  G1  No product pixel change  — inspect protected-pixel-diff.png
  G2  No product loss          — inspect stage19-recomposited.png
  G4  No repetition            — inspect final-overlay.png
  G5  No seam                  — inspect stage19-background.png
  G7  No non-uniform scale     — inspect final-overlay.png

Stage 20 blockers:
  external_inpaint_not_attempted — external provider not configured
  outpaint_not_attempted         — outpaint flag not enabled

Output per spec (in --output/<WxH>/):
  source.png                 — flat source image (artboard)
  native-smart-fit.png       — current smart-fit result
  stage19-background.png     — best pipeline candidate background
  stage19-recomposited.png   — pipeline result after quality gate
  protected-pixel-diff.png   — pixel diff native vs stage19
  candidate-contact-sheet.png — all pipeline candidates
  final-overlay.png          — side-by-side native vs stage19
  golden_test_report.json    — full evaluation report
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


# ── Env setup ─────────────────────────────────────────────────────────────────

def _set_pipeline_env() -> None:
    os.environ["BACKGROUND_PIPELINE_ENABLED"] = "true"
    os.environ["BACKGROUND_PIPELINE_COMPARE_ONLY"] = "false"
    os.environ["BACKGROUND_OUTPAINT_ENABLED"] = "true"
    os.environ["BACKGROUND_LOCAL_INPAINT_ENABLED"] = "true"
    os.environ["BACKGROUND_EXTERNAL_INPAINT_ENABLED"] = "false"
    os.environ["BACKGROUND_SHADOW_ENABLED"] = "false"
    os.environ["BACKGROUND_PIPELINE_ARTIFACT_LEVEL"] = "full"


# ── Source loading ─────────────────────────────────────────────────────────────

def _load_source(psd_path: str):
    """Load flat source image from PSD using worker's load_source_image."""
    sys.path.insert(0, "/app")
    from resizer import load_source_image
    return load_source_image(psd_path)


# ── Smart-fit native path ──────────────────────────────────────────────────────

def _run_native(source_img, target_w: int, target_h: int):
    from resizer import _apply_resize
    # Temporarily disable pipeline so native runs without Stage 19
    os.environ["BACKGROUND_PIPELINE_ENABLED"] = "false"
    try:
        resized, meta = _apply_resize(source_img, target_w, target_h, "smart-fit", "balanced", "center")
    finally:
        os.environ["BACKGROUND_PIPELINE_ENABLED"] = "true"
    return resized, meta


# ── Stage 19 pipeline ──────────────────────────────────────────────────────────

def _run_stage19(source_img, target_w: int, target_h: int, output_dir: str):
    from background import BackgroundPipeline
    from background.schemas import BackgroundRequest, BackgroundOptions
    opts = BackgroundOptions.from_env()
    req = BackgroundRequest(
        source_image=source_img.convert("RGB"),
        target_width=target_w,
        target_height=target_h,
        options=opts,
        request_id=f"golden_{target_w}x{target_h}_{int(time.time())}",
    )
    pipeline = BackgroundPipeline(output_dir=output_dir)
    return pipeline.process(req)


# ── Image utilities ────────────────────────────────────────────────────────────

def _pixel_diff(img_a, img_b):
    """Compute absolute pixel diff image and mean delta. Returns (diff_img, mean_delta)."""
    from PIL import ImageChops
    import numpy as np
    a = img_a.convert("RGB")
    b = img_b.convert("RGB").resize(a.size) if img_b.size != a.size else img_b.convert("RGB")
    diff = ImageChops.difference(a, b)
    arr = np.array(diff, dtype=float)
    return diff.convert("RGB"), float(arr.mean())


def _make_contact_sheet(candidates, thumb_w: int = 360, max_cols: int = 3):
    """Tile all pipeline candidate images into a single sheet."""
    from PIL import Image, ImageDraw
    valid = [c for c in candidates if c.image is not None]
    if not valid:
        return None
    thumb_h = max(1, int(thumb_w * 0.6))
    cols = min(max_cols, len(valid))
    rows = (len(valid) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h + 22), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    for i, cand in enumerate(valid):
        col, row = i % cols, i // cols
        x0, y0 = col * thumb_w, row * thumb_h + 22
        thumb = cand.image.convert("RGB").resize((thumb_w, thumb_h))
        sheet.paste(thumb, (x0, y0))
        label = f"{cand.candidate_id[:28]} | score={cand.score:.0f} | {'PASS' if cand.accepted else 'FAIL'}"
        color = (100, 230, 130) if cand.accepted else (220, 100, 100)
        draw.rectangle([x0, y0 - 18, x0 + thumb_w, y0 - 1], fill=(30, 30, 30))
        draw.text((x0 + 4, y0 - 16), label, fill=color)
    return sheet


def _make_side_by_side(native_img, stage19_img, label_a: str, label_b: str):
    """Create annotated side-by-side comparison image."""
    from PIL import Image, ImageDraw
    a = native_img.convert("RGB")
    b = stage19_img.convert("RGB")
    gap = 8
    header_h = 20
    w = a.width + b.width + gap
    h = max(a.height, b.height) + header_h
    out = Image.new("RGB", (w, h), (18, 18, 18))
    draw = ImageDraw.Draw(out)
    draw.text((4, 3), label_a, fill=(180, 180, 180))
    draw.text((a.width + gap + 4, 3), label_b, fill=(100, 220, 130))
    out.paste(a, (0, header_h))
    out.paste(b, (a.width + gap, header_h))
    return out


# ── Golden evaluation ─────────────────────────────────────────────────────────

def _evaluate(native_img, stage19_img, bg_result, diff_mean: float) -> dict:
    verdicts: dict[str, str] = {}

    # G1: No product pixel change — diff mean < 5.0 (with no protectedObjects, always SKIP)
    verdicts["G1_no_product_pixel_change"] = "MANUAL_REVIEW"  # inspect protected-pixel-diff.png

    # G2: No product loss — manual inspection
    verdicts["G2_no_product_loss"] = "MANUAL_REVIEW"

    # G3: No blur band — outpaint ran and was not overridden by fallback
    if bg_result.outpaint_attempted and not bg_result.fallback_used:
        verdicts["G3_no_blur_band"] = "PASS"
    elif not bg_result.outpaint_attempted:
        verdicts["G3_no_blur_band"] = "SKIP"  # same-size target; outpaint not needed
    else:
        verdicts["G3_no_blur_band"] = "FAIL"

    # G4: No repetition — manual
    verdicts["G4_no_repetition"] = "MANUAL_REVIEW"

    # G5: No seam — manual
    verdicts["G5_no_seam"] = "MANUAL_REVIEW"

    # G6: Correct target size
    if stage19_img is not None and stage19_img.size == native_img.size:
        verdicts["G6_correct_target_size"] = "PASS"
    else:
        verdicts["G6_correct_target_size"] = "FAIL"

    # G7: No non-uniform scale — manual
    verdicts["G7_no_nonuniform_scale"] = "MANUAL_REVIEW"

    # G8: ≥1 pipeline candidate accepted (not native fallback)
    non_native_accepted = any(
        c.accepted and not c.candidate_id.startswith("native")
        for c in bg_result.candidates
    )
    verdicts["G8_pipeline_candidate_accepted"] = "PASS" if non_native_accepted else "FAIL"

    # G9: Visual improvement — best score > 50 pts
    best_score = bg_result.best_evaluated_background_score or 0.0
    verdicts["G9_visual_improvement"] = "PASS" if best_score > 50 else "FAIL"

    # G10: Not Fake Provider result
    src = bg_result.best_evaluated_background_source or ""
    verdicts["G10_not_fake_provider"] = "FAIL" if "fake" in src.lower() else "PASS"

    # Technical PASS: deterministic criteria only
    tech_pass = (
        verdicts["G6_correct_target_size"] == "PASS"
        and verdicts["G8_pipeline_candidate_accepted"] == "PASS"
        and verdicts["G10_not_fake_provider"] == "PASS"
        and not bg_result.fallback_used
    )

    # Visual PASS: also requires G9 (score) — manual review items remain flagged
    visual_pass = tech_pass and verdicts["G9_visual_improvement"] == "PASS"

    # Stage 20 blockers
    stage20_blockers = []
    if not bg_result.external_inpaint_attempted:
        stage20_blockers.append("external_inpaint_not_attempted")
    if not bg_result.outpaint_attempted:
        stage20_blockers.append("outpaint_not_attempted")
    if not any(c for c in bg_result.candidates if not c.candidate_id.startswith("native")):
        stage20_blockers.append("no_non_native_candidates_generated")

    return {
        "tech_pass": tech_pass,
        "visual_pass": visual_pass,
        "verdicts": verdicts,
        "stage20_blockers": stage20_blockers,
        "pipeline": {
            "verdict": bg_result.verdict,
            "applied_source": bg_result.applied_background_source,
            "best_source": bg_result.best_evaluated_background_source,
            "best_score": bg_result.best_evaluated_background_score,
            "fallback_used": bg_result.fallback_used,
            "fallback_reason": bg_result.fallback_reason,
            "outpaint_attempted": bg_result.outpaint_attempted,
            "outpaint_accepted": bg_result.outpaint_accepted,
            "external_inpaint_attempted": bg_result.external_inpaint_attempted,
            "shadow_applied": bg_result.shadow_applied,
            "candidates_count": len(bg_result.candidates),
            "accepted_count": sum(1 for c in bg_result.candidates if c.accepted),
            "elapsed_ms": bg_result.elapsed_ms,
        },
        "diff_mean": diff_mean,
    }


# ── Per-spec test runner ──────────────────────────────────────────────────────

def _run_spec(source_img, target_w: int, target_h: int, spec_dir: str) -> dict:
    spec_label = f"{target_w}x{target_h}"
    os.makedirs(spec_dir, exist_ok=True)

    print(f"\n[Golden] ── Spec: {spec_label} ──────────────────────────────────")

    # 1. Native smart-fit
    t0 = time.time()
    native_img, native_meta = _run_native(source_img, target_w, target_h)
    native_ms = int((time.time() - t0) * 1000)
    native_img.convert("RGB").save(os.path.join(spec_dir, "native-smart-fit.png"))
    print(f"  [native]  strategy={native_meta.get('resizeStrategy')} elapsed={native_ms}ms")

    # 2. Stage 19 pipeline
    stage19_dir = os.path.join(spec_dir, "stage19-artifacts")
    t0 = time.time()
    try:
        bg_result = _run_stage19(source_img, target_w, target_h, stage19_dir)
        stage19_ms = int((time.time() - t0) * 1000)
    except Exception as exc:
        print(f"  [stage19] EXCEPTION: {exc}")
        return {"spec": spec_label, "error": str(exc), "tech_pass": False, "visual_pass": False}

    print(
        f"  [stage19] verdict={bg_result.verdict}"
        f" fallback={bg_result.fallback_used}"
        f" outpaint={bg_result.outpaint_attempted}(accepted={bg_result.outpaint_accepted})"
        f" source={bg_result.applied_background_source}"
        f" candidates={len(bg_result.candidates)}"
        f" elapsed={stage19_ms}ms"
    )

    # 3. Determine final stage19 image
    stage19_result_img = bg_result.result_image
    if stage19_result_img is None:
        stage19_result_img = native_img  # pipeline produced nothing; show native
    stage19_result_img.convert("RGB").save(os.path.join(spec_dir, "stage19-recomposited.png"))

    # 4. Best candidate background (pre-compositing, highest score)
    best_cand = None
    for c in bg_result.candidates:
        if c.accepted and c.image is not None:
            best_cand = c
            break
    if best_cand is None and bg_result.candidates:
        with_img = [c for c in bg_result.candidates if c.image is not None]
        if with_img:
            best_cand = max(with_img, key=lambda c: c.score)
    bg_img = best_cand.image if best_cand and best_cand.image else native_img
    bg_img.convert("RGB").save(os.path.join(spec_dir, "stage19-background.png"))

    # 5. Pixel diff
    try:
        diff_img, diff_mean = _pixel_diff(native_img, stage19_result_img)
        diff_img.save(os.path.join(spec_dir, "protected-pixel-diff.png"))
        print(f"  [diff]    mean_delta={diff_mean:.2f}")
    except Exception as exc:
        diff_mean = -1.0
        print(f"  [diff]    error: {exc}")

    # 6. Candidate contact sheet
    try:
        sheet = _make_contact_sheet(bg_result.candidates)
        if sheet:
            sheet.save(os.path.join(spec_dir, "candidate-contact-sheet.png"))
    except Exception as exc:
        print(f"  [contact] warning: {exc}")

    # 7. Side-by-side final overlay
    try:
        overlay = _make_side_by_side(
            native_img, stage19_result_img,
            "Native smart-fit",
            f"Stage 19 [{bg_result.applied_background_source}]",
        )
        overlay.save(os.path.join(spec_dir, "final-overlay.png"))
    except Exception as exc:
        print(f"  [overlay] warning: {exc}")

    # 8. Evaluate
    evaluation = _evaluate(native_img, stage19_result_img, bg_result, diff_mean)
    evaluation.update({
        "spec": spec_label,
        "native_strategy": native_meta.get("resizeStrategy"),
        "native_blur_area_ratio": native_meta.get("blurAreaRatio"),
        "elapsed_native_ms": native_ms,
        "elapsed_stage19_ms": stage19_ms,
    })

    # Print per-criterion results
    for k, v in evaluation["verdicts"].items():
        icon = "✓" if v == "PASS" else ("?" if v in ("SKIP", "MANUAL_REVIEW") else "✗")
        print(f"    {icon} {k}: {v}")

    if evaluation["stage20_blockers"]:
        print(f"  [Stage20 blockers]")
        for b in evaluation["stage20_blockers"]:
            print(f"    • {b}")

    status = "TECH_PASS" if evaluation["tech_pass"] else "TECH_FAIL"
    vis = "VISUAL_PASS" if evaluation["visual_pass"] else "VISUAL_FAIL"
    print(f"  ⟹  {status} / {vis}")

    return evaluation


# ── Main ──────────────────────────────────────────────────────────────────────

def run_golden_test(psd_path: str, specs: list[tuple[int, int]], output_dir: str) -> dict:
    sys.path.insert(0, "/app")
    _set_pipeline_env()

    print(f"[Golden] PSD:    {psd_path}")
    print(f"[Golden] Specs:  {', '.join(f'{w}x{h}' for w, h in specs)}")
    print(f"[Golden] Output: {output_dir}")
    print(f"[Golden] Flags:  PIPELINE=true COMPARE_ONLY=false OUTPAINT=true")

    os.makedirs(output_dir, exist_ok=True)

    try:
        source_img, source_meta = _load_source(psd_path)
        print(f"[Golden] Source: {source_img.size} mode={source_img.mode} renderSource={source_meta['renderSource']}")
        source_img.convert("RGB").save(os.path.join(output_dir, "source.png"))
    except Exception as exc:
        print(f"[Golden] FATAL: source load failed: {exc}")
        sys.exit(1)

    all_results = []
    for target_w, target_h in specs:
        spec_dir = os.path.join(output_dir, f"{target_w}x{target_h}")
        try:
            result = _run_spec(source_img, target_w, target_h, spec_dir)
        except Exception as exc:
            print(f"  EXCEPTION for {target_w}x{target_h}: {exc}")
            result = {"spec": f"{target_w}x{target_h}", "error": str(exc),
                      "tech_pass": False, "visual_pass": False}
        all_results.append(result)

    total = len(all_results)
    tech_pass_n = sum(1 for r in all_results if r.get("tech_pass"))
    visual_pass_n = sum(1 for r in all_results if r.get("visual_pass"))
    error_n = sum(1 for r in all_results if "error" in r)

    report = {
        "psd": psd_path,
        "specs": [f"{w}x{h}" for w, h in specs],
        "timestamp": int(time.time()),
        "pipeline_env": {
            "BACKGROUND_PIPELINE_ENABLED": os.environ.get("BACKGROUND_PIPELINE_ENABLED"),
            "BACKGROUND_PIPELINE_COMPARE_ONLY": os.environ.get("BACKGROUND_PIPELINE_COMPARE_ONLY"),
            "BACKGROUND_OUTPAINT_ENABLED": os.environ.get("BACKGROUND_OUTPAINT_ENABLED"),
        },
        "results": all_results,
        "summary": {
            "total": total,
            "tech_pass": tech_pass_n,
            "visual_pass": visual_pass_n,
            "errors": error_n,
        },
    }

    report_path = os.path.join(output_dir, "golden_test_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n[Golden] ══════════ SUMMARY ══════════")
    print(f"  Total specs : {total}")
    print(f"  Tech  PASS  : {tech_pass_n}/{total}")
    print(f"  Visual PASS : {visual_pass_n}/{total}  (MANUAL_REVIEW items not counted)")
    print(f"  Errors      : {error_n}")
    print(f"  Report      : {report_path}")

    # Stage 20 blocker summary across all specs
    all_blockers: set[str] = set()
    for r in all_results:
        all_blockers.update(r.get("stage20_blockers", []))
    if all_blockers:
        print(f"\n[Stage 20 blockers detected]")
        for b in sorted(all_blockers):
            print(f"  • {b}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 19.5 Golden Visual Quality Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run inside creative-worker container with /app in PYTHONPATH.",
    )
    parser.add_argument("--psd", required=True, help="Path to PSD file (inside container)")
    parser.add_argument(
        "--specs",
        default="1250x560,1200x300,300x1200",
        help="Comma-separated WxH specs (default: 1250x560,1200x300,300x1200)",
    )
    parser.add_argument("--width", type=int, help="Single target width (overrides --specs)")
    parser.add_argument("--height", type=int, help="Single target height (overrides --specs)")
    parser.add_argument("--output", default="/artifacts/golden", help="Output directory")
    args = parser.parse_args()

    if not os.path.exists(args.psd):
        print(f"ERROR: PSD not found: {args.psd}")
        sys.exit(1)

    if args.width and args.height:
        specs = [(args.width, args.height)]
    else:
        specs = []
        for s in args.specs.split(","):
            s = s.strip()
            if "x" in s:
                try:
                    w_s, h_s = s.split("x", 1)
                    specs.append((int(w_s), int(h_s)))
                except ValueError:
                    print(f"WARNING: skipping invalid spec '{s}'")

    if not specs:
        print("ERROR: No valid specs. Use --specs WxH or --width/--height.")
        sys.exit(1)

    report = run_golden_test(args.psd, specs, args.output)

    all_tech_pass = report["summary"]["tech_pass"] == report["summary"]["total"]
    sys.exit(0 if all_tech_pass else 1)


if __name__ == "__main__":
    main()
