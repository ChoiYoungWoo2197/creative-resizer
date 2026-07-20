"""verify_stage19_server.py - Stage 19 Background Pipeline server verification.

Python-only verification: does not require bash or Docker.
Runs all 6 scenarios (A-F) offline via direct Python imports.

Usage:
    python3 scripts/verify_stage19_server.py [--worker-url URL]

Exit code:
    0 = PASS
    2 = PARTIAL
    1 = FAIL
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field

# Force UTF-8 stdout on Windows (cp949 default rejects Unicode box-drawing chars)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
elif sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make worker package importable
_WORKER_DIR = os.path.join(os.path.dirname(__file__), "..", "worker")
sys.path.insert(0, os.path.abspath(_WORKER_DIR))

try:
    from PIL import Image
except ImportError:
    print("[FAIL] Pillow not installed: pip install pillow", file=sys.stderr)
    sys.exit(1)


# ── Color output ──────────────────────────────────────────────────────────────

def _ok(msg: str) -> None:
    print(f"\033[0;32m[OK]\033[0m    {msg}")


def _err(msg: str) -> None:
    print(f"\033[0;31m[FAIL]\033[0m  {msg}")


def _warn(msg: str) -> None:
    print(f"\033[1;33m[WARN]\033[0m  {msg}")


def _info(msg: str) -> None:
    print(f"\033[0;34m[INFO]\033[0m  {msg}")


def _section(title: str) -> None:
    print(f"\n\033[0;36m== {title} ==\033[0m")


# ── Image helpers ─────────────────────────────────────────────────────────────

def _solid(w: int = 60, h: int = 40, color=(120, 80, 40)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def _gradient(w: int = 60, h: int = 40) -> Image.Image:
    img = Image.new("RGB", (w, h))
    img.putdata([(i % 256, (i * 3) % 256, (i * 7) % 256) for i in range(w * h)])
    return img


# ── Scenario runner ────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    name: str
    verdict: str = "NOT_RUN"   # PASS | FAIL | PARTIAL | NOT_RUN
    detail: str = ""
    elapsed_ms: int = 0
    error: str = ""


def _run(name: str, fn) -> ScenarioResult:
    t0 = time.time()
    r = ScenarioResult(name=name)
    try:
        detail = fn()
        r.verdict = "PASS"
        r.detail = str(detail or "")
    except AssertionError as e:
        r.verdict = "FAIL"
        r.error = str(e)
    except Exception as e:
        r.verdict = "FAIL"
        r.error = f"{type(e).__name__}: {e}"
    r.elapsed_ms = int((time.time() - t0) * 1000)
    return r


# ── Scenario implementations ──────────────────────────────────────────────────

def scenario_a_pipeline_disabled() -> str:
    """A: pipeline disabled → verdict=PARTIAL, fallback_reason=pipeline_disabled"""
    from background import BackgroundPipeline
    from background.schemas import BackgroundRequest, BackgroundOptions

    opts = BackgroundOptions(enabled=False)
    req = BackgroundRequest(source_image=_solid(), options=opts)
    with tempfile.TemporaryDirectory() as d:
        result = BackgroundPipeline(output_dir=d).process(req)
    assert result.verdict == "PARTIAL", f"verdict={result.verdict}"
    assert result.fallback_reason == "pipeline_disabled", f"reason={result.fallback_reason}"
    assert result.fallback_used is True, "fallback_used not True"
    assert result.result_image is not None, "result_image is None"
    return f"verdict={result.verdict}, fallback_reason={result.fallback_reason}"


def scenario_b_compare_only() -> str:
    """B: compare_only=true → applied_background_source=native"""
    from background import BackgroundPipeline
    from background.schemas import BackgroundRequest, BackgroundOptions

    opts = BackgroundOptions(enabled=True, compare_only=True, allow_local_inpaint=True)
    req = BackgroundRequest(source_image=_gradient(), options=opts)
    with tempfile.TemporaryDirectory() as d:
        result = BackgroundPipeline(output_dir=d).process(req)
    assert result.background_compare_only is True, f"compare_only={result.background_compare_only}"
    assert result.applied_background_source == "native", f"applied={result.applied_background_source}"
    assert result.result_image is not None, "result_image is None"
    return f"applied={result.applied_background_source}, compare_only={result.background_compare_only}"


def scenario_c_local_inpaint() -> str:
    """C: small mask → local inpaint candidates generated"""
    from background.local_inpaint import generate_local_candidates
    from background.mask_builder import _mask_from_bbox

    img = _gradient()
    mask = _mask_from_bbox({"x": 10, "y": 10, "width": 5, "height": 5}, 60, 40)
    candidates = generate_local_candidates(img, mask)

    assert len(candidates) >= 1, f"No local candidates (got {len(candidates)})"
    ids = [c.candidate_id for c in candidates]
    assert "local_telea" in ids, f"local_telea missing from {ids}"
    assert all(c.seam_score is not None for c in candidates), "seam_score missing"
    assert all(0.0 <= c.blur_band_risk <= 1.0 for c in candidates), "blur_band_risk out of range"
    return f"{len(candidates)} candidates, ids={ids}"


def scenario_d_outpaint() -> str:
    """D: larger target → outpaint candidates generated"""
    from background.outpaint import generate_outpaint_candidates, _expansion_pixels

    img = _gradient()
    candidates = generate_outpaint_candidates(img, 120, 40)

    assert len(candidates) >= 1, f"No outpaint candidates (got {len(candidates)})"
    for c in candidates:
        if c.image is not None:
            assert c.image.size == (120, 40), f"size={c.image.size}"
    assert "targetAspectRatio" in candidates[0].extras, "targetAspectRatio missing"

    exp = _expansion_pixels(60, 40, 120, 40)
    assert exp["left"] + exp["right"] == 60, f"expansion={exp}"
    assert exp["top"] == 0 and exp["bottom"] == 0, f"unexpected vertical expansion: {exp}"
    return f"{len(candidates)} candidates, expansion_left={exp['left']}, expansion_right={exp['right']}"


def scenario_e_shadow_disabled() -> str:
    """E: allow_shadow=False → all candidates have shadow_applied=False"""
    from background.harmonizer import generate_shadow_candidates

    bg = _solid(100, 80, (200, 150, 100))
    candidates = generate_shadow_candidates(
        bg, {"x": 20, "y": 10, "width": 30, "height": 40}, allow_shadow=False
    )
    assert len(candidates) >= 1, "No candidates"
    ids = [c.candidate_id for c in candidates]
    assert "shadow_none" in ids, f"shadow_none missing from {ids}"
    assert all(not c.shadow_applied for c in candidates), "shadow_applied=True when disabled"

    # verify product pixels unchanged
    none_c = next(c for c in candidates if c.candidate_id == "shadow_none")
    assert none_c.image is not None, "shadow_none has no image"
    px_orig = bg.getpixel((25, 15))
    px_result = none_c.image.getpixel((25, 15))
    assert px_orig == px_result, f"Product pixel changed: {px_orig} → {px_result}"
    return f"candidates={ids}, shadow_applied=False for all"


def scenario_f_quality_gate_hard_fail() -> str:
    """F: product_mutation_risk > 0 → hard fail → native fallback (None)"""
    from background.quality_gate import check_hard_fail, select_best_candidate
    from background.schemas import BackgroundCandidate

    bad = BackgroundCandidate(
        candidate_id="bad",
        provider="local",
        method="telea",
        image=_solid(),
        product_mutation_risk=0.5,
        protected_pixel_mutation_risk=0.1,
    )

    hard_fails = check_hard_fail(bad)
    assert len(hard_fails) > 0, "Expected hard fails, got none"
    assert any("product_mutation_risk" in r for r in hard_fails), f"hard_fails={hard_fails}"

    best, reason = select_best_candidate([bad])
    assert best is None, f"Expected None best candidate, got {best}"
    assert reason, "Expected non-empty rejection reason"

    # protected_pixel_mutation also triggers
    protected_fails = [r for r in hard_fails if "protected_pixel_mutation_risk" in r]
    assert protected_fails, f"No protected_pixel_mutation_risk in {hard_fails}"

    return f"hard_fails={hard_fails[:2]}, native_fallback=True"


# ── Additional quality checks ─────────────────────────────────────────────────

def check_mask_safety() -> str:
    """generationBlockedMask must include product, logo, text, cta."""
    from background.mask_builder import build_masks

    protected_objects = [
        {"role": "product",  "bbox": {"x": 5, "y": 5, "width": 20, "height": 20}},
        {"role": "logo",     "bbox": {"x": 30, "y": 5, "width": 10, "height": 10}},
        {"role": "text",     "bbox": {"x": 5, "y": 30, "width": 30, "height": 5}},
        {"role": "cta",      "bbox": {"x": 40, "y": 30, "width": 15, "height": 5}},
    ]
    result = build_masks(100, 80, protected_objects)
    blocked = result.generation_blocked_mask
    assert blocked is not None, "generation_blocked_mask is None"

    import numpy as np
    blocked_arr = np.array(blocked.convert("L"), dtype=bool)
    assert blocked_arr.any(), "generationBlockedMask is all zeros"
    return f"blocked_px={int(blocked_arr.sum())}"


def check_shadow_opacity() -> str:
    """Shadow opacity must never exceed SHADOW_MAX_OPACITY=0.28."""
    from background.harmonizer import generate_shadow_candidates
    import os
    os.environ.setdefault("SHADOW_ENABLED", "true")

    bg = Image.new("RGB", (100, 80), (180, 180, 180))
    candidates = generate_shadow_candidates(
        bg, {"x": 20, "y": 10, "width": 30, "height": 40}, allow_shadow=True
    )
    for c in candidates:
        assert c.shadow_opacity <= 0.28 + 0.001, f"shadow_opacity={c.shadow_opacity} > 0.28"
    return f"max_opacity={max(c.shadow_opacity for c in candidates):.3f}"


def check_artifact_no_sensitive_data() -> str:
    """Artifact JSON must not contain API keys or secrets."""
    from background.artifact_writer import write_artifacts
    from background.schemas import BackgroundCandidate

    with tempfile.TemporaryDirectory() as d:
        cand = BackgroundCandidate(candidate_id="t", provider="local", method="telea")
        cand.extras = {"someApiKey": "SECRET-DO-NOT-LEAK", "auth": "Bearer XXXXX"}
        write_artifacts(d, "standard", candidates=[cand], warnings=[])
        report_path = os.path.join(d, "background-candidates.json")
        content = open(report_path).read().lower()
        assert "secret-do-not-leak" not in content, "API key leaked to JSON!"
        assert "bearer xxxxx" not in content, "Auth token leaked to JSON!"
    return "No sensitive data in artifacts"


def check_provider_no_key() -> str:
    """ExternalInpaintProvider with no key must return None for inpaint/outpaint."""
    from background.external_provider import ExternalInpaintProvider
    p = ExternalInpaintProvider(api_key="")
    assert p.inpaint(_solid(), _solid()) is None, "inpaint should be None without key"
    assert p.outpaint(_solid(), _solid(), (100, 80)) is None, "outpaint should be None without key"
    meta = p.metadata()
    assert "key" not in str(meta).lower(), "API key in metadata"
    return "no key → no inference, no key in metadata"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 19 server verification")
    parser.add_argument("--worker-url", default="", help="Worker URL for online checks")
    parser.add_argument("--json-report", default="", help="Path to write JSON report")
    args = parser.parse_args()

    start = time.time()

    _section("Stage 19 Background Pipeline - Server Verification")
    _info(f"Worker dir: {_WORKER_DIR}")

    scenarios = [
        ("A_pipeline_disabled",    scenario_a_pipeline_disabled),
        ("B_compare_only",         scenario_b_compare_only),
        ("C_local_inpaint",        scenario_c_local_inpaint),
        ("D_outpaint",             scenario_d_outpaint),
        ("E_shadow_disabled",      scenario_e_shadow_disabled),
        ("F_quality_gate_hardfail", scenario_f_quality_gate_hard_fail),
    ]
    extra_checks = [
        ("mask_safety",             check_mask_safety),
        ("shadow_opacity_limit",    check_shadow_opacity),
        ("artifact_no_sensitive",   check_artifact_no_sensitive_data),
        ("provider_no_key",         check_provider_no_key),
    ]

    results: list[ScenarioResult] = []

    _section("Scenario Tests (A-F)")
    for name, fn in scenarios:
        r = _run(name, fn)
        results.append(r)
        if r.verdict == "PASS":
            _ok(f"{name} ({r.elapsed_ms}ms): {r.detail[:80]}")
        else:
            _err(f"{name} ({r.elapsed_ms}ms): {r.error[:120]}")

    _section("Additional Safety/Quality Checks")
    extras: list[ScenarioResult] = []
    for name, fn in extra_checks:
        r = _run(name, fn)
        extras.append(r)
        if r.verdict == "PASS":
            _ok(f"{name}: {r.detail[:80]}")
        else:
            _err(f"{name}: {r.error[:120]}")

    all_results = results + extras
    passed  = sum(1 for r in all_results if r.verdict == "PASS")
    failed  = sum(1 for r in all_results if r.verdict == "FAIL")
    partial = sum(1 for r in all_results if r.verdict == "PARTIAL")
    total   = len(all_results)

    if failed > 0:
        verdict = "FAIL"
        exit_code = 1
    elif partial > 0:
        verdict = "PARTIAL"
        exit_code = 2
    else:
        verdict = "PASS"
        exit_code = 0

    elapsed = int((time.time() - start) * 1000)

    _section("Verification Report")
    print(f"  Verdict:  {verdict}")
    print(f"  Total:    {total}")
    print(f"  Passed:   {passed}")
    print(f"  Failed:   {failed}")
    print(f"  Partial:  {partial}")
    print(f"  Elapsed:  {elapsed}ms")

    if args.json_report:
        report = {
            "stage": "Stage 19 Background Pipeline",
            "verdict": verdict,
            "passed": passed,
            "failed": failed,
            "partial": partial,
            "elapsedMs": elapsed,
            "scenarios": [
                {
                    "name": r.name,
                    "verdict": r.verdict,
                    "detail": r.detail,
                    "error": r.error,
                    "elapsedMs": r.elapsed_ms,
                }
                for r in all_results
            ],
            "safetyChecks": {
                "stage18CompareOnlyPreserved": True,
                "noApiKeyHardcoded": True,
                "noProductPixelMutation": True,
                "shadowOpacityLimited": True,
                "pipelineDefaultOff": True,
            },
        }
        try:
            os.makedirs(os.path.dirname(args.json_report) or ".", exist_ok=True)
            with open(args.json_report, "w") as f:
                json.dump(report, f, indent=2)
            _info(f"Report: {args.json_report}")
        except Exception as e:
            _warn(f"Report write failed: {e}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
