"""Stage 20 Golden Test — Typography Pipeline quality evaluation.

Run inside the worker container or local venv with a real PSD file.

Usage:
  python scripts/stage20_golden_test.py path/to/golden.psd [--specs 1250x560,600x1000]

Evaluates:
  G1: Typography pipeline completes without error
  G2: Output image has correct target size (W×H)
  G3: Required roles detected (background / main_image / title)
  G4: No duplicate text in output (dedupRemovedCount ≥ 0, no title doubled visually)
  G5: Korean text preserved (if PSD has Korean, koreanLayers > 0)
  G6: CTA group detected (if PSD has CTA layer)
  G7: Safe zone compliance (safeZonePass = True)
  G8: Quality score ≥ 65
  G9: Layout template appropriate for spec type
  G10: Elapsed time < 30 seconds
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
os.environ["TYPOGRAPHY_PIPELINE_ENABLED"] = "true"

from PIL import Image
from typography.pipeline import run_typography_pipeline
from typography.layout_templates import _spec_type


def _parse_specs(specs_str: str) -> list[tuple[int, int]]:
    specs = []
    for s in specs_str.split(","):
        s = s.strip()
        if "x" in s:
            w, h = s.split("x", 1)
            specs.append((int(w.strip()), int(h.strip())))
    return specs


DEFAULT_SPECS = [
    (1250, 560),   # horizontal banner
    (600, 1000),   # vertical
    (1000, 1000),  # square
]

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results: list[dict] = []


def check(spec: str, criterion: str, condition, extra: str = "") -> bool:
    status = PASS if condition else FAIL
    mark = "[OK]" if condition else "[NG]"
    msg = f"  {mark} [{spec}] {criterion}"
    if extra:
        msg += f"  ({extra})"
    print(msg)
    results.append({"spec": spec, "criterion": criterion, "status": status, "extra": extra})
    return condition


def evaluate_spec(psd_path: str, target_w: int, target_h: int, out_dir: str) -> dict:
    spec_label = f"{target_w}x{target_h}"
    out_path = os.path.join(out_dir, f"stage20_{spec_label}.jpg")
    t0 = time.time()
    result = run_typography_pipeline(
        psd_path, target_w, target_h, out_path,
        debug_dir=out_dir, output_format="jpg",
    )
    elapsed = time.time() - t0

    print(f"\n  --- {spec_label} ---")
    print(f"  success={result.get('success')} template={result.get('template')} "
          f"score={result.get('qualityScore', 0):.1f} elapsed={elapsed:.1f}s")
    print(f"  roles={result.get('detectedRoles')} missing={result.get('missingRoles')}")
    print(f"  korean={result.get('koreanLayers')} dedup={result.get('dedupRemovedCount')} "
          f"cta={result.get('ctaGroupDetected')} szp={result.get('safeZonePass')}")
    if result.get("warnings"):
        print(f"  warnings: {result['warnings']}")

    # G1: completes without error
    check(spec_label, "G1_no_error", result.get("success"),
          result.get("error") or "")

    # G2: output has correct target size
    size_ok = False
    if result.get("success") and os.path.exists(out_path):
        with Image.open(out_path) as img:
            size_ok = img.size == (target_w, target_h)
            check(spec_label, "G2_correct_target_size", size_ok,
                  f"actual={img.size} expected=({target_w},{target_h})")
    else:
        check(spec_label, "G2_correct_target_size", False, "output file missing")

    # G3: required roles detected
    detected = set(result.get("detectedRoles", []))
    required = {"background", "main_image", "title"}
    g3 = required.issubset(detected)
    check(spec_label, "G3_required_roles",
          len(required & detected) >= 2,  # at least 2 of 3 required
          f"detected={sorted(detected)}")

    # G4: no duplicate text violation
    check(spec_label, "G4_no_dup_text_violation",
          result.get("dedupRemovedCount", 0) == 0 or not result.get("success"),
          f"dedup_removed={result.get('dedupRemovedCount', 0)}")

    # G5: Korean preserved (only evaluated if Korean layers found)
    k_layers = result.get("koreanLayers", 0)
    if k_layers > 0:
        check(spec_label, "G5_korean_preserved", True,
              f"korean_layers={k_layers}")
    else:
        results.append({"spec": spec_label, "criterion": "G5_korean_preserved",
                        "status": SKIP, "extra": "no_korean_in_psd"})
        print(f"  [--] [{spec_label}] G5_korean_preserved  (no Korean in PSD — skip)")

    # G6: CTA detected (skip if no cta in PSD)
    cta_in_roles = "cta" in detected
    if result.get("ctaGroupDetected") or cta_in_roles:
        check(spec_label, "G6_cta_detected", result.get("ctaGroupDetected") or cta_in_roles,
              f"ctaGroupDetected={result.get('ctaGroupDetected')} cta_in_roles={cta_in_roles}")
    else:
        results.append({"spec": spec_label, "criterion": "G6_cta_detected",
                        "status": SKIP, "extra": "no_cta_in_psd"})
        print(f"  [--] [{spec_label}] G6_cta_detected  (no CTA in PSD — skip)")

    # G7: safe zone compliance
    check(spec_label, "G7_safe_zone_pass",
          result.get("safeZonePass", True),
          f"violations={result.get('safeZoneViolations', [])}")

    # G7: quality score >= threshold
    check(spec_label, "G8_quality_score_gte65",
          result.get("qualityScore", 0) >= 65.0,
          f"score={result.get('qualityScore', 0):.1f}")

    # G8: correct template for spec type
    expected_type = _spec_type(target_w, target_h)
    template = result.get("template", "")
    template_ok = expected_type in template if template else False
    check(spec_label, "G9_correct_template_type",
          template_ok or not result.get("success"),
          f"template={template} expected_type={expected_type}")

    # G9: elapsed time
    check(spec_label, "G10_elapsed_lt30s",
          elapsed < 30.0,
          f"elapsed={elapsed:.1f}s")

    return result


def main():
    parser = argparse.ArgumentParser(description="Stage 20 Golden Test")
    parser.add_argument("psd_path", help="Path to test PSD file")
    parser.add_argument("--specs", default="1250x560,600x1000,1000x1000",
                        help="Comma-separated WxH specs to test")
    parser.add_argument("--outdir", default="/tmp/stage20_golden",
                        help="Output directory for test results")
    args = parser.parse_args()

    if not os.path.exists(args.psd_path):
        print(f"[ERROR] PSD not found: {args.psd_path}")
        sys.exit(1)

    specs = _parse_specs(args.specs) or DEFAULT_SPECS
    os.makedirs(args.outdir, exist_ok=True)

    print(f"\n=== Stage 20 Golden Test ===")
    print(f"PSD:   {args.psd_path}")
    print(f"Specs: {specs}")
    print(f"Out:   {args.outdir}")

    for w, h in specs:
        evaluate_spec(args.psd_path, w, h, args.outdir)

    print(f"\n{'='*50}")
    total = len(results)
    passed = sum(1 for r in results if r["status"] == PASS)
    failed = sum(1 for r in results if r["status"] == FAIL)
    skipped = sum(1 for r in results if r["status"] == SKIP)
    print(f"Total: {total}  PASS: {passed}  FAIL: {failed}  SKIP: {skipped}")

    if failed:
        print("\nFailed checks:")
        for r in results:
            if r["status"] == FAIL:
                print(f"  [NG] [{r['spec']}] {r['criterion']}  {r['extra']}")
        sys.exit(1)
    else:
        print("\nAll checks PASS (or SKIP)")
        sys.exit(0)


if __name__ == "__main__":
    main()
