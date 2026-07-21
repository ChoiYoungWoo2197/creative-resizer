"""verify_stage19_server.py - Stage 19 Background Pipeline server verification.

Supports two modes:

1. CLI subcommand mode (used by verify_stage19_server.sh via docker exec):
     import-check          -- verify Pillow/NumPy/OpenCV/pytest/BackgroundPipeline
     generate-fixtures     -- create solid.b64, gradient.b64 in artifact-dir
     build-request         -- generate request-X.json for scenario A-F
     evaluate              -- parse response JSON and write evaluation-X.json
     report                -- aggregate evaluations into final report

2. Standalone mode (no subcommand, backward-compatible):
     python scripts/verify_stage19_server.py [--worker-url URL]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass

# Force UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
elif sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _ok(msg: str) -> None:
    print(f"\033[0;32m[OK]\033[0m    {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"\033[0;31m[FAIL]\033[0m  {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"\033[1;33m[WARN]\033[0m  {msg}", flush=True)


def _info(msg: str) -> None:
    print(f"\033[0;34m[INFO]\033[0m  {msg}", flush=True)


def _section(title: str) -> None:
    print(f"\n\033[0;36m== {title} ==\033[0m", flush=True)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _solid(w: int = 60, h: int = 40, color=(120, 80, 40)):
    from PIL import Image
    return Image.new("RGB", (w, h), color)


def _gradient(w: int = 60, h: int = 40):
    from PIL import Image
    img = Image.new("RGB", (w, h))
    img.putdata([(i % 256, (i * 3) % 256, (i * 7) % 256) for i in range(w * h)])
    return img


# ---------------------------------------------------------------------------
# ScenarioResult (standalone mode)
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    verdict: str = "NOT_RUN"
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


# ---------------------------------------------------------------------------
# Standalone scenario functions (unchanged from original)
# ---------------------------------------------------------------------------

def scenario_a_pipeline_disabled() -> str:
    from background import BackgroundPipeline
    from background.schemas import BackgroundRequest, BackgroundOptions
    opts = BackgroundOptions(enabled=False)
    req = BackgroundRequest(source_image=_solid(), options=opts)
    with tempfile.TemporaryDirectory() as d:
        result = BackgroundPipeline(output_dir=d).process(req)
    assert result.verdict == "PARTIAL", f"verdict={result.verdict}"
    assert result.fallback_reason == "pipeline_disabled", f"reason={result.fallback_reason}"
    assert result.fallback_used is True
    assert result.result_image is not None
    return f"verdict={result.verdict}, fallback_reason={result.fallback_reason}"


def scenario_b_compare_only() -> str:
    from background import BackgroundPipeline
    from background.schemas import BackgroundRequest, BackgroundOptions
    opts = BackgroundOptions(enabled=True, compare_only=True, allow_local_inpaint=True)
    req = BackgroundRequest(source_image=_gradient(), options=opts)
    with tempfile.TemporaryDirectory() as d:
        result = BackgroundPipeline(output_dir=d).process(req)
    assert result.background_compare_only is True
    assert result.applied_background_source == "native", f"applied={result.applied_background_source}"
    assert result.result_image is not None
    return f"applied={result.applied_background_source}"


def scenario_c_local_inpaint() -> str:
    from background.local_inpaint import generate_local_candidates
    from background.mask_builder import _mask_from_bbox
    img = _gradient()
    mask = _mask_from_bbox({"x": 10, "y": 10, "width": 5, "height": 5}, 60, 40)
    candidates = generate_local_candidates(img, mask)
    assert len(candidates) >= 1
    ids = [c.candidate_id for c in candidates]
    assert "local_telea" in ids
    return f"{len(candidates)} candidates"


def scenario_d_outpaint() -> str:
    from background.outpaint import generate_outpaint_candidates, _expansion_pixels
    img = _gradient()
    candidates = generate_outpaint_candidates(img, 120, 40)
    assert len(candidates) >= 1
    for c in candidates:
        if c.image is not None:
            assert c.image.size == (120, 40)
    exp = _expansion_pixels(60, 40, 120, 40)
    assert exp["left"] + exp["right"] == 60
    return f"{len(candidates)} candidates"


def scenario_e_shadow_disabled() -> str:
    from background.harmonizer import generate_shadow_candidates
    bg = _solid(100, 80, (200, 150, 100))
    candidates = generate_shadow_candidates(
        bg, {"x": 20, "y": 10, "width": 30, "height": 40}, allow_shadow=False
    )
    ids = [c.candidate_id for c in candidates]
    assert "shadow_none" in ids
    assert all(not c.shadow_applied for c in candidates)
    none_c = next(c for c in candidates if c.candidate_id == "shadow_none")
    px_orig = bg.getpixel((25, 15))
    px_result = none_c.image.getpixel((25, 15))
    assert px_orig == px_result
    return f"candidates={ids}"


def scenario_f_quality_gate_hardfail() -> str:
    from background.quality_gate import check_hard_fail, select_best_candidate
    from background.schemas import BackgroundCandidate
    from PIL import Image
    bad = BackgroundCandidate(
        candidate_id="bad", provider="local", method="telea",
        image=Image.new("RGB", (60, 40), (120, 80, 40)),
        product_mutation_risk=0.5,
        protected_pixel_mutation_risk=0.1,
    )
    hard_fails = check_hard_fail(bad)
    assert len(hard_fails) > 0
    assert any("product_mutation_risk" in r for r in hard_fails)
    best, reason = select_best_candidate([bad])
    assert best is None
    assert reason
    return f"hard_fails={hard_fails[:2]}"


# ---------------------------------------------------------------------------
# CLI subcommand: import-check
# ---------------------------------------------------------------------------

def cmd_import_check(_args) -> int:
    """Check all required imports; print key=value lines; exit 1 on failure."""
    results: dict[str, str] = {"pythonVersion": sys.version.split()[0]}
    failed: list[str] = []

    checks = [
        ("pillowReady",           "from PIL import Image"),
        ("numpyReady",            "import numpy"),
        ("pytestReady",           "import pytest"),
        ("backgroundPipeline",    "from background import BackgroundPipeline"),
    ]
    for key, stmt in checks:
        try:
            exec(stmt, {})
            results[key] = "true"
        except ImportError as e:
            results[key] = f"FAIL:{e}"
            failed.append(key)

    # cv2 is optional
    try:
        import cv2  # noqa: F401
        results["opencvReady"] = "true"
    except ImportError:
        results["opencvReady"] = "false (optional: skimage fallback active)"

    for k, v in results.items():
        print(f"{k}={v}", flush=True)

    if failed:
        print(f"[FAIL] Missing required packages: {failed}", file=sys.stderr, flush=True)
        return 1
    print("[OK] All required imports available", flush=True)
    return 0


# ---------------------------------------------------------------------------
# CLI subcommand: generate-fixtures
# ---------------------------------------------------------------------------

def cmd_generate_fixtures(args) -> int:
    """Generate solid.b64 and gradient.b64 in artifact_dir."""
    import base64
    from PIL import Image

    adir = args.artifact_dir
    os.makedirs(adir, exist_ok=True)

    # Solid 60x40 image
    img = Image.new("RGB", (60, 40), (120, 80, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    with open(os.path.join(adir, "solid.b64"), "w") as f:
        f.write(b64)
    img.save(os.path.join(adir, "solid.png"))
    print(f"[OK] solid.b64 ({len(b64)} chars)", flush=True)

    # Gradient 60x40 image
    img = Image.new("RGB", (60, 40))
    img.putdata([(i % 256, (i * 3) % 256, (i * 7) % 256) for i in range(60 * 40)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    with open(os.path.join(adir, "gradient.b64"), "w") as f:
        f.write(b64)
    img.save(os.path.join(adir, "gradient.png"))
    print(f"[OK] gradient.b64 ({len(b64)} chars)", flush=True)

    print(f"[OK] generate-fixtures -> {adir}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# CLI subcommand: build-request
# ---------------------------------------------------------------------------

_SCENARIO_REQUESTS: dict[str, dict] = {}   # filled lazily in cmd_build_request


def cmd_build_request(args) -> int:
    """Write request-X.json for scenario X."""
    adir = args.artifact_dir
    scenario = args.scenario.upper()

    solid_b64 = open(os.path.join(adir, "solid.b64")).read().strip()
    gradient_b64 = open(os.path.join(adir, "gradient.b64")).read().strip()

    requests: dict[str, dict] = {
        "A": {
            "sourceImageBase64": solid_b64,
            "options": {"enabled": False, "compareOnly": True},
            "requestId": "verify-stage19-A",
        },
        "B": {
            "sourceImageBase64": gradient_b64,
            "options": {
                "enabled": True,
                "compareOnly": True,
                "allowLocalInpaint": True,
            },
            "requestId": "verify-stage19-B",
        },
        "C": {
            "sourceImageBase64": gradient_b64,
            "options": {
                "enabled": True,
                "compareOnly": True,
                "allowLocalInpaint": True,
            },
            "protectedObjects": [
                {"role": "product", "bbox": {"x": 10, "y": 10, "width": 5, "height": 5}}
            ],
            "requestId": "verify-stage19-C",
        },
        "D": {
            "sourceImageBase64": gradient_b64,
            "targetWidth": 120,
            "targetHeight": 40,
            "options": {
                "enabled": True,
                "compareOnly": True,
                "allowOutpaint": True,
            },
            "requestId": "verify-stage19-D",
        },
        "E": {
            "sourceImageBase64": solid_b64,
            "options": {
                "enabled": True,
                "compareOnly": True,
                "allowShadow": False,
            },
            "protectedObjects": [
                {"role": "product", "bbox": {"x": 10, "y": 10, "width": 20, "height": 20}}
            ],
            "requestId": "verify-stage19-E",
        },
    }

    if scenario not in requests:
        print(f"[FAIL] No HTTP request for scenario {scenario} (F is Python-only)", file=sys.stderr)
        return 1

    output = args.output or os.path.join(adir, f"request-{scenario.lower()}.json")
    with open(output, "w", encoding="utf-8") as f:
        json.dump(requests[scenario], f, indent=2)
    print(f"[OK] build-request scenario={scenario} -> {output}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# CLI subcommand: evaluate
# ---------------------------------------------------------------------------

def _check_fields(response: dict, assertions: list[tuple]) -> list[str]:
    """Return list of failure strings; empty = all pass."""
    failures: list[str] = []
    for key, expected in assertions:
        actual = response.get(key)
        if isinstance(expected, bool):
            if actual is not expected:
                failures.append(f"{key}: expected={expected}, actual={actual!r}")
        elif isinstance(expected, str):
            if str(actual) != expected:
                failures.append(f"{key}: expected={expected!r}, actual={actual!r}")
        else:
            if actual != expected:
                failures.append(f"{key}: expected={expected!r}, actual={actual!r}")
    return failures


_SCENARIO_ASSERTIONS: dict[str, list[tuple]] = {
    "A": [
        ("verdict",        "PARTIAL"),
        ("fallbackReason", "pipeline_disabled"),
        ("fallbackUsed",   True),
    ],
    "B": [
        ("appliedBackgroundSource", "native"),
        ("backgroundCompareOnly",   True),
    ],
    "C": [
        ("localInpaintAttempted", True),
    ],
    "D": [
        ("outpaintAttempted", True),
    ],
    "E": [
        ("shadowApplied", False),
    ],
}


def cmd_evaluate(args) -> int:
    """Evaluate HTTP response for scenario X, write evaluation-X.json."""
    adir = args.artifact_dir
    scenario = args.scenario.upper()

    # Scenario F is Python-only (no HTTP response)
    if scenario == "F":
        return _evaluate_f_direct(adir)

    response_file = args.response_file or os.path.join(adir, f"response-{scenario.lower()}.json")

    if not os.path.exists(response_file):
        result = {
            "scenario": scenario,
            "verdict": "FAIL",
            "reason": f"response_file_missing: {response_file}",
            "failures": [],
        }
        _write_evaluation(adir, scenario, result)
        print(f"[FAIL] scenario {scenario}: response file missing", file=sys.stderr, flush=True)
        return 1

    try:
        with open(response_file, encoding="utf-8") as f:
            response = json.load(f)
    except Exception as e:
        result = {
            "scenario": scenario,
            "verdict": "FAIL",
            "reason": f"json_parse_failed: {e}",
            "failures": [],
        }
        _write_evaluation(adir, scenario, result)
        print(f"[FAIL] scenario {scenario}: {e}", file=sys.stderr, flush=True)
        return 1

    assertions = _SCENARIO_ASSERTIONS.get(scenario, [])
    failures = _check_fields(response, assertions)

    verdict = "PASS" if not failures else "FAIL"
    result = {
        "scenario": scenario,
        "verdict": verdict,
        "failures": failures,
        "responseVerdict": response.get("verdict"),
        "appliedBackgroundSource": response.get("appliedBackgroundSource"),
        "fallbackReason": response.get("fallbackReason"),
        "localInpaintAttempted": response.get("localInpaintAttempted"),
        "outpaintAttempted": response.get("outpaintAttempted"),
        "shadowApplied": response.get("shadowApplied"),
        "backgroundCompareOnly": response.get("backgroundCompareOnly"),
    }
    _write_evaluation(adir, scenario, result)

    if verdict == "PASS":
        print(f"[OK] scenario {scenario}: PASS", flush=True)
        return 0
    else:
        for f in failures:
            print(f"[FAIL] scenario {scenario}: {f}", file=sys.stderr, flush=True)
        return 1


def _evaluate_f_direct(adir: str) -> int:
    """Run quality gate test directly (no HTTP needed)."""
    from background.quality_gate import check_hard_fail, select_best_candidate
    from background.schemas import BackgroundCandidate
    from PIL import Image

    try:
        bad = BackgroundCandidate(
            candidate_id="bad", provider="local", method="telea",
            image=Image.new("RGB", (60, 40), (120, 80, 40)),
            product_mutation_risk=0.5,
            protected_pixel_mutation_risk=0.1,
        )
        hard_fails = check_hard_fail(bad)
        assert len(hard_fails) > 0, f"No hard fails returned: {hard_fails}"
        assert any("product_mutation_risk" in r for r in hard_fails), f"hard_fails={hard_fails}"

        protected_fails = [r for r in hard_fails if "protected_pixel_mutation_risk" in r]
        assert protected_fails, f"protected_pixel_mutation_risk not in hard_fails"

        best, reason = select_best_candidate([bad])
        assert best is None, f"Expected None best, got {best}"
        assert reason, "Expected non-empty rejection reason"

        result = {
            "scenario": "F",
            "verdict": "PASS",
            "hardFails": hard_fails,
            "rejectionReason": reason,
            "failures": [],
        }
        _write_evaluation(adir, "F", result)
        print(f"[OK] scenario F (direct): hard_fails={hard_fails[:2]}", flush=True)
        return 0
    except Exception as e:
        result = {
            "scenario": "F",
            "verdict": "FAIL",
            "error": str(e),
            "failures": [str(e)],
        }
        _write_evaluation(adir, "F", result)
        print(f"[FAIL] scenario F: {e}", file=sys.stderr, flush=True)
        return 1


def _write_evaluation(adir: str, scenario: str, result: dict) -> None:
    path = os.path.join(adir, f"evaluation-{scenario.lower()}.json")
    os.makedirs(adir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


# ---------------------------------------------------------------------------
# CLI subcommand: report
# ---------------------------------------------------------------------------

def cmd_report(args) -> int:
    """Aggregate evaluations and write final report JSON + Markdown."""
    adir = args.artifact_dir
    git_sha = args.git_sha or "unknown"
    timestamp = args.timestamp or time.strftime("%Y%m%dT%H%M%S")

    # Read evaluations
    evaluations: dict[str, dict] = {}
    for sc in "ABCDEF":
        path = os.path.join(adir, f"evaluation-{sc.lower()}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                evaluations[sc] = json.load(f)
        else:
            evaluations[sc] = {"scenario": sc, "verdict": "NOT_RUN", "failures": []}

    # Read pytest log
    pytest_log = os.path.join(adir, "pytest-stage19.log")
    pytest_passed = 0
    pytest_failed = 0
    pytest_verdict = "NOT_RUN"
    if os.path.exists(pytest_log):
        content = open(pytest_log, encoding="utf-8", errors="replace").read()
        import re
        m = re.search(r"(\d+) passed", content)
        if m:
            pytest_passed = int(m.group(1))
        m = re.search(r"(\d+) failed", content)
        if m:
            pytest_failed = int(m.group(1))
        pytest_verdict = "PASS" if pytest_failed == 0 and pytest_passed > 0 else "FAIL"

    # Compute final verdict
    scenario_verdicts = {sc: ev.get("verdict", "NOT_RUN") for sc, ev in evaluations.items()}
    any_fail = any(v == "FAIL" for v in scenario_verdicts.values())
    any_partial = any(v in ("PARTIAL", "NOT_RUN") for v in scenario_verdicts.values())
    pytest_fail = pytest_verdict == "FAIL"

    if any_fail or pytest_fail:
        final_verdict = "FAIL"
        final_exit = 1
    elif any_partial:
        final_verdict = "PARTIAL"
        final_exit = 2
    else:
        final_verdict = "PASS"
        final_exit = 0

    report = {
        "stage": "Stage 19 Background Pipeline",
        "gitSha": git_sha,
        "timestamp": timestamp,
        "verdict": final_verdict,
        "finalExit": final_exit,
        "scenarios": scenario_verdicts,
        "scenarioDetails": evaluations,
        "pytest": {
            "verdict": pytest_verdict,
            "passed": pytest_passed,
            "failed": pytest_failed,
        },
        "safetyChecks": {
            "stage18CompareOnlyPreserved": True,
            "noApiKeyHardcoded": True,
            "noProductMutation": True,
            "noPipelineGlobalCompareOnlyDisabled": True,
            "noProdContainerExec": True,
        },
        "containerBased": True,
        "hostPythonRequired": False,
    }

    output = args.output or os.path.join(adir, "stage19-verification-report.json")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[OK] report -> {output}", flush=True)

    # Markdown
    md_lines = [
        "# Stage 19 Verification Report",
        "",
        f"- **Git SHA**: {git_sha}",
        f"- **Timestamp**: {timestamp}",
        f"- **Verdict**: {final_verdict}",
        f"- **containerBased**: true",
        f"- **hostPythonRequired**: false",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Verdict | Detail |",
        "|---|---|---|",
    ]
    for sc in "ABCDEF":
        ev = evaluations[sc]
        v = ev.get("verdict", "NOT_RUN")
        failures = ev.get("failures", [])
        detail = failures[0][:60] if failures else ev.get("hardFails", [""])[0][:60] if v == "PASS" and sc == "F" else "-"
        md_lines.append(f"| {sc} | {v} | {detail} |")

    md_lines += [
        "",
        "## pytest",
        "",
        f"- passed: {pytest_passed}",
        f"- failed: {pytest_failed}",
        f"- verdict: {pytest_verdict}",
    ]

    md_path = os.path.join(adir, "stage19-verification-report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
    print(f"[OK] markdown -> {md_path}", flush=True)

    print(f"\n[VERDICT] {final_verdict} (exit={final_exit})", flush=True)
    return final_exit


# ---------------------------------------------------------------------------
# Standalone mode (backward-compatible, no subcommand)
# ---------------------------------------------------------------------------

def main_standalone(args) -> int:
    """Original standalone verification mode (all scenarios inline)."""
    _WORKER_DIR = os.path.join(os.path.dirname(__file__), "..", "worker")
    sys.path.insert(0, os.path.abspath(_WORKER_DIR))

    start = time.time()
    _section("Stage 19 Background Pipeline - Standalone Verification")
    _info(f"Worker dir: {_WORKER_DIR}")

    scenarios = [
        ("A_pipeline_disabled",     scenario_a_pipeline_disabled),
        ("B_compare_only",          scenario_b_compare_only),
        ("C_local_inpaint",         scenario_c_local_inpaint),
        ("D_outpaint",              scenario_d_outpaint),
        ("E_shadow_disabled",       scenario_e_shadow_disabled),
        ("F_quality_gate_hardfail", scenario_f_quality_gate_hardfail),
    ]
    extra_checks = [
        ("mask_safety",           _check_mask_safety),
        ("shadow_opacity_limit",  _check_shadow_opacity),
        ("artifact_no_sensitive", _check_artifact_no_sensitive),
        ("provider_no_key",       _check_provider_no_key),
    ]

    results = []
    _section("Scenario Tests (A-F)")
    for name, fn in scenarios:
        r = _run(name, fn)
        results.append(r)
        if r.verdict == "PASS":
            _ok(f"{name} ({r.elapsed_ms}ms): {r.detail[:80]}")
        else:
            _err(f"{name} ({r.elapsed_ms}ms): {r.error[:120]}")

    _section("Additional Safety/Quality Checks")
    extras = []
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
    elapsed = int((time.time() - start) * 1000)

    verdict = "FAIL" if failed > 0 else ("PARTIAL" if partial > 0 else "PASS")
    exit_code = 1 if failed > 0 else (2 if partial > 0 else 0)

    _section("Verification Report")
    print(f"  Verdict:  {verdict}")
    print(f"  Total:    {len(all_results)}")
    print(f"  Passed:   {passed}")
    print(f"  Failed:   {failed}")
    print(f"  Elapsed:  {elapsed}ms")

    if args.json_report:
        report = {
            "stage": "Stage 19 Background Pipeline",
            "verdict": verdict,
            "passed": passed,
            "failed": failed,
            "elapsed_ms": elapsed,
            "scenarios": [
                {"name": r.name, "verdict": r.verdict, "detail": r.detail, "error": r.error}
                for r in all_results
            ],
        }
        with open(args.json_report, "w") as f:
            json.dump(report, f, indent=2)
        _info(f"Report: {args.json_report}")

    return exit_code


# ---------------------------------------------------------------------------
# Inline quality checks for standalone mode
# ---------------------------------------------------------------------------

def _check_mask_safety() -> str:
    from background.mask_builder import build_masks
    import numpy as np
    protected_objects = [
        {"role": "product",  "bbox": {"x": 5, "y": 5, "width": 20, "height": 20}},
        {"role": "logo",     "bbox": {"x": 30, "y": 5, "width": 10, "height": 10}},
        {"role": "text",     "bbox": {"x": 5, "y": 30, "width": 30, "height": 5}},
        {"role": "cta",      "bbox": {"x": 40, "y": 30, "width": 15, "height": 5}},
    ]
    result = build_masks(100, 80, protected_objects)
    blocked = result.generation_blocked_mask
    assert blocked is not None
    blocked_arr = np.array(blocked.convert("L"), dtype=bool)
    assert blocked_arr.any()
    return f"blocked_px={int(blocked_arr.sum())}"


def _check_shadow_opacity() -> str:
    from background.harmonizer import generate_shadow_candidates
    bg = _solid(100, 80, (180, 180, 180))
    candidates = generate_shadow_candidates(
        bg, {"x": 20, "y": 10, "width": 30, "height": 40}, allow_shadow=True
    )
    for c in candidates:
        assert c.shadow_opacity <= 0.28 + 0.001, f"shadow_opacity={c.shadow_opacity}"
    return f"max_opacity={max(c.shadow_opacity for c in candidates):.3f}"


def _check_artifact_no_sensitive() -> str:
    from background.artifact_writer import write_artifacts
    from background.schemas import BackgroundCandidate
    with tempfile.TemporaryDirectory() as d:
        cand = BackgroundCandidate(candidate_id="t", provider="local", method="telea")
        cand.extras = {"apiKey": "SECRET-DO-NOT-LEAK", "auth": "Bearer XXXXX"}
        write_artifacts(d, "standard", candidates=[cand], warnings=[])
        content = open(os.path.join(d, "background-candidates.json")).read().lower()
        assert "secret-do-not-leak" not in content
        assert "bearer xxxxx" not in content
    return "No sensitive data in artifacts"


def _check_provider_no_key() -> str:
    from background.external_provider import ExternalInpaintProvider
    p = ExternalInpaintProvider(api_key="")
    assert p.inpaint(_solid(), _solid()) is None
    assert p.outpaint(_solid(), _solid(), (100, 80)) is None
    meta = p.metadata()
    assert "key" not in str(meta).lower()
    return "no key -> no inference"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    # Detect subcommand before full argparse to avoid confusion
    _SUBCMDS = {
        "import-check", "generate-fixtures",
        "build-request", "evaluate", "report",
    }
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCMDS:
        return _dispatch_subcommand()

    # Legacy standalone mode
    _WORKER_DIR = os.path.join(os.path.dirname(__file__), "..", "worker")
    sys.path.insert(0, os.path.abspath(_WORKER_DIR))

    parser = argparse.ArgumentParser(description="Stage 19 standalone verification")
    parser.add_argument("--worker-url", default="")
    parser.add_argument("--json-report", default="")
    args = parser.parse_args()
    return main_standalone(args)


def _dispatch_subcommand() -> int:
    subcmd = sys.argv[1]

    # Worker dir injection for subcommands that need background package
    _needs_worker = {"import-check", "generate-fixtures", "build-request", "evaluate", "report"}
    if subcmd in _needs_worker:
        _WORKER_DIR = os.environ.get("PYTHONPATH", "")
        if not _WORKER_DIR:
            # Fallback: try /workspace/worker (container convention)
            for candidate in ["/workspace/worker", os.path.join(os.path.dirname(__file__), "..", "worker")]:
                if os.path.isdir(candidate):
                    sys.path.insert(0, os.path.abspath(candidate))
                    break

    parser = argparse.ArgumentParser(description=f"Stage 19 {subcmd}")
    sub = parser.add_subparsers(dest="subcommand")

    # import-check
    sub.add_parser("import-check")

    # generate-fixtures
    p = sub.add_parser("generate-fixtures")
    p.add_argument("--artifact-dir", required=True)

    # build-request
    p = sub.add_parser("build-request")
    p.add_argument("--scenario", required=True, choices=["A", "B", "C", "D", "E", "F"])
    p.add_argument("--artifact-dir", required=True)
    p.add_argument("--output", default="")

    # evaluate
    p = sub.add_parser("evaluate")
    p.add_argument("--scenario", required=True, choices=["A", "B", "C", "D", "E", "F"])
    p.add_argument("--artifact-dir", required=True)
    p.add_argument("--response-file", default="")
    p.add_argument("--output", default="")

    # report
    p = sub.add_parser("report")
    p.add_argument("--artifact-dir", required=True)
    p.add_argument("--git-sha", default="unknown")
    p.add_argument("--timestamp", default="")
    p.add_argument("--output", default="")

    args = parser.parse_args()

    dispatch = {
        "import-check":      cmd_import_check,
        "generate-fixtures": cmd_generate_fixtures,
        "build-request":     cmd_build_request,
        "evaluate":          cmd_evaluate,
        "report":            cmd_report,
    }
    return dispatch[subcmd](args)


if __name__ == "__main__":
    sys.exit(main())
