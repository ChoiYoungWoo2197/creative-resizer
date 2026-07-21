"""Stage 20 Typography Pipeline — server verification (multi-command).

All commands are designed to run inside the helper container with PYTHONPATH=/app.
The shell script (verify_stage20_server.sh) handles Docker lifecycle; this script
handles all Python logic (imports, module checks, report generation).

Commands:
  import-check    Verify all Stage 20 typography module imports succeed
  check-roles     Verify 15 role aliases resolve correctly
  check-templates Verify layout templates cover all spec types
  check-flags     Verify TYPOGRAPHY_PIPELINE_ENABLED=false by default
  check-dedup     Verify duplicate detector group-cover and similarity logic
  check-quality   Verify quality gate full-pass scoring
  report          Aggregate artifact JSONs into a final verification report

Usage (from verify_stage20_server.sh via docker exec):
  python /scripts/verify_stage20_server.py import-check --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-roles  --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-templates --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-flags  --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-dedup  --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-quality --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py report \\
      --artifact-dir /artifacts \\
      --git-sha abc1234 \\
      --timestamp 20260721T120000 \\
      --output /artifacts/stage20-verification-report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import re
from pathlib import Path


# ─── Output helpers ──────────────────────────────────────────────────────────

def _ok(msg: str, detail: str = "") -> None:
    line = f"[OK]  {msg}"
    if detail:
        line += f"  ({detail})"
    print(line)


def _ng(msg: str, detail: str = "") -> None:
    line = f"[NG]  {msg}"
    if detail:
        line += f"  ({detail})"
    print(line, file=sys.stderr)


def _write_json(path: Path | str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _load_json(artifact_dir: str, fname: str) -> dict:
    p = Path(artifact_dir) / fname
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ─── import-check ────────────────────────────────────────────────────────────

def cmd_import_check(args: argparse.Namespace) -> int:
    """Verify all Stage 20 typography module symbols are importable."""
    symbols = [
        ("typography.pipeline",          "run_typography_pipeline"),
        ("typography.role_resolver",     "classify_role_by_name"),
        ("typography.role_resolver",     "ROLE_ALIASES"),
        ("typography.layout_templates",  "get_template"),
        ("typography.layout_templates",  "_spec_type"),
        ("typography.duplicate_detector","detect_duplicates"),
        ("typography.duplicate_detector","count_deduped"),
        ("typography.quality_gate",      "evaluate"),
        ("typography.quality_gate",      "LAYOUT_SCORE_THRESHOLD"),
        ("typography.schemas",           "LayoutSlot"),
        ("typography.schemas",           "TypographyResult"),
        ("typography.text_extractor",    "extract_text_layers"),
        ("typography.cta_layout",        "detect_cta_groups"),
        ("typography.compositor",        "compose"),
        ("typography.font_resolver",     "resolve_font"),
    ]

    checks: list[dict] = []
    all_ok = True
    for module_name, attr in symbols:
        try:
            mod = __import__(module_name, fromlist=[attr])
            getattr(mod, attr)
            checks.append({"module": module_name, "attr": attr, "ok": True})
        except Exception as exc:
            checks.append({"module": module_name, "attr": attr, "ok": False, "error": str(exc)})
            all_ok = False

    result = {"stage20ImportReady": all_ok, "symbolCount": len(symbols), "checks": checks}

    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "import-check.json", result)

    if all_ok:
        _ok("Stage20 import check", f"{len(symbols)} symbols verified")
    else:
        failed = [c for c in checks if not c["ok"]]
        for c in failed:
            _ng(f"{c['module']}.{c['attr']}", c.get("error", ""))

    return 0 if all_ok else 1


# ─── check-roles ─────────────────────────────────────────────────────────────

def cmd_check_roles(args: argparse.Namespace) -> int:
    """Verify 15 role aliases exist and resolve correctly."""
    try:
        from typography.role_resolver import classify_role_by_name, ROLE_ALIASES
    except Exception as exc:
        _ng("typography.role_resolver import", str(exc))
        return 1

    # Count: ROLE_ALIASES is list[tuple[str, list[str]]]
    role_count = len(ROLE_ALIASES)
    count_ok = role_count == 15

    if count_ok:
        _ok(f"ROLE_ALIASES count = {role_count}")
    else:
        _ng("ROLE_ALIASES count", f"got {role_count}, expected 15")

    # Alias resolution tests (Korean + English)
    alias_tests = [
        ("배경",           "background"),
        ("product",        "main_image"),
        ("타이틀",         "title"),
        ("subcopy",        "body_text"),
        ("cta",            "cta"),
        ("로고",           "logo"),
        ("disclaimer",     "legal_text"),
        ("brand_name",     "brand_name"),
        ("product_detail", "product_detail"),
        ("sub_logo",       "sub_logo"),
        ("texture",        "pattern"),
        ("gradient_overlay", "overlay"),
        ("scene",          "scene"),
        ("deco_layer",     "decoration"),
        ("sale_tag",       "badge"),
    ]

    alias_results: list[dict] = []
    for name, expected in alias_tests:
        got = classify_role_by_name(name)
        ok = got == expected
        alias_results.append({"name": name, "expected": expected, "got": got, "ok": ok})
        if ok:
            _ok(f"classify_role_by_name({name!r}) = {got!r}")
        else:
            _ng(f"classify_role_by_name({name!r})", f"got {got!r}, expected {expected!r}")

    all_alias_ok = all(r["ok"] for r in alias_results)

    # Verify required roles are present in ROLE_ALIASES
    defined_roles = {role for role, _ in ROLE_ALIASES}
    required_roles = {
        "background", "main_image", "title", "cta", "logo", "body_text",
        "legal_text", "brand_name", "product_detail", "sub_logo",
        "pattern", "overlay", "scene", "decoration", "badge",
    }
    missing_roles = sorted(required_roles - defined_roles)
    if missing_roles:
        _ng("missing required roles", str(missing_roles))
    else:
        _ok("all 15 required roles defined")

    all_ok = count_ok and all_alias_ok and not missing_roles

    result = {
        "roleCount": role_count,
        "roleCountOk": count_ok,
        "aliasTests": alias_results,
        "aliasTestsOk": all_alias_ok,
        "requiredRolesMissing": missing_roles,
        "allOk": all_ok,
    }

    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "roles.json", result)

    return 0 if all_ok else 1


# ─── check-templates ─────────────────────────────────────────────────────────

def cmd_check_templates(args: argparse.Namespace) -> int:
    """Verify layout templates cover all spec types with unique z_orders."""
    try:
        from typography.layout_templates import get_template, _spec_type
    except Exception as exc:
        _ng("typography.layout_templates import", str(exc))
        return 1

    spec_tests = [
        (1250, 560,  "1250x560"),
        (1200, 628,  "horizontal"),
        (1000, 1000, "square"),
        (600,  900,  "vertical"),
        (300,  1200, "ultravert"),   # _spec_type returns "ultravertical"; template name contains "ultravert"
        (2400, 600,  "ultrawide"),
    ]

    template_results: list[dict] = []
    all_ok = True

    for w, h, expected_substr in spec_tests:
        try:
            name_t, slots = get_template(w, h, [])
            name_ok = expected_substr in name_t
            slot_count = len(slots)

            # Verify z_orders are unique within this template
            zorders = [s.z_order for s in slots]
            zorder_ok = len(zorders) == len(set(zorders))

            entry = {
                "spec": f"{w}x{h}",
                "template": name_t,
                "expectedSubstr": expected_substr,
                "slotCount": slot_count,
                "nameOk": name_ok,
                "zOrderUnique": zorder_ok,
                "ok": name_ok and zorder_ok,
            }
            template_results.append(entry)

            if name_ok:
                _ok(f"template {w}x{h} → {name_t!r} ({slot_count} slots)")
            else:
                _ng(f"template {w}x{h}", f"got {name_t!r}, expected '{expected_substr}' substring")
                all_ok = False

            if not zorder_ok:
                _ng(f"z_order not unique in {name_t!r}", f"zorders={sorted(zorders)}")
                all_ok = False
            else:
                _ok(f"z_order unique in {name_t!r}")

        except Exception as exc:
            template_results.append({
                "spec": f"{w}x{h}", "template": None,
                "expectedSubstr": expected_substr, "slotCount": 0,
                "nameOk": False, "zOrderUnique": False, "ok": False, "error": str(exc),
            })
            _ng(f"template {w}x{h}", str(exc))
            all_ok = False

    result = {"templateTests": template_results, "allOk": all_ok}

    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "templates.json", result)

    return 0 if all_ok else 1


# ─── check-flags ─────────────────────────────────────────────────────────────

def cmd_check_flags(args: argparse.Namespace) -> int:
    """Verify TYPOGRAPHY_PIPELINE_ENABLED=false is the default (pipeline disabled)."""
    os.environ.pop("TYPOGRAPHY_PIPELINE_ENABLED", None)

    try:
        from typography.pipeline import run_typography_pipeline
    except Exception as exc:
        _ng("typography.pipeline import", str(exc))
        return 1

    result_dict = run_typography_pipeline(
        "/nonexistent.psd", 1000, 600, "/tmp/stage20-flag-verify.jpg"
    )
    error_val = result_dict.get("error", "")
    flag_ok = error_val == "typography_pipeline_disabled"

    if flag_ok:
        _ok("TYPOGRAPHY_PIPELINE_ENABLED defaults to false")
        _ok(f"pipeline returns error={error_val!r}")
    else:
        _ng("TYPOGRAPHY_PIPELINE_ENABLED default",
            f"error={error_val!r}, expected 'typography_pipeline_disabled'")

    check_result = {
        "envVarAbsent": "TYPOGRAPHY_PIPELINE_ENABLED" not in os.environ,
        "pipelineDisabledByDefault": flag_ok,
        "errorReturned": error_val,
        "allOk": flag_ok,
    }

    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "flags.json", check_result)

    return 0 if flag_ok else 1


# ─── check-dedup ─────────────────────────────────────────────────────────────

def cmd_check_dedup(args: argparse.Namespace) -> int:
    """Verify duplicate detector: group-composite coverage and role+similarity logic."""
    try:
        from typography.duplicate_detector import detect_duplicates, count_deduped
    except Exception as exc:
        _ng("typography.duplicate_detector import", str(exc))
        return 1

    all_ok = True

    # ── Scenario 1: group composite bbox covers child text layer → dedupSkip ──
    layers_s1 = [
        {
            "id": "group_composite", "type": "group",
            "isTextLayer": False, "isGroupComposite": True,
            "role": "unknown",
            "bbox": {"x": 0, "y": 0, "width": 400, "height": 200},
        },
        {
            "id": "child_title", "type": "type",
            "isTextLayer": True, "isGroupComposite": False,
            "role": "title", "textContent": "한국어 제목 레이어", "fontSize": 24,
            "bbox": {"x": 10, "y": 10, "width": 200, "height": 40},
        },
    ]
    result_s1 = detect_duplicates(layers_s1)
    child = next((l for l in result_s1 if l.get("id") == "child_title"), None)
    s1_ok = child is not None and child.get("dedupSkip") is True

    if s1_ok:
        _ok("Scenario 1: group composite covers child → dedupSkip=True")
    else:
        _ng("Scenario 1: group composite cover",
            f"child.dedupSkip={child.get('dedupSkip') if child else 'N/A'}")
        all_ok = False

    # ── Scenario 2: separate layers with different roles → no skip ────────────
    layers_s2 = [
        {
            "id": "t1", "type": "type",
            "isTextLayer": True, "isGroupComposite": False,
            "role": "title", "textContent": "메인 타이틀 문구", "fontSize": 28,
            "bbox": {"x": 0, "y": 0, "width": 300, "height": 60},
        },
        {
            "id": "t2", "type": "type",
            "isTextLayer": True, "isGroupComposite": False,
            "role": "body_text", "textContent": "서브카피 본문 내용", "fontSize": 16,
            "bbox": {"x": 0, "y": 400, "width": 300, "height": 60},
        },
    ]
    result_s2 = detect_duplicates(layers_s2)
    t1 = next((l for l in result_s2 if l.get("id") == "t1"), None)
    t2 = next((l for l in result_s2 if l.get("id") == "t2"), None)
    s2_ok = (
        t1 is not None and not t1.get("dedupSkip", True) and
        t2 is not None and not t2.get("dedupSkip", True)
    )

    if s2_ok:
        _ok("Scenario 2: separate roles, no overlap → no dedupSkip")
    else:
        _ng("Scenario 2: separate layers",
            f"t1.dedupSkip={t1.get('dedupSkip') if t1 else 'N/A'}, "
            f"t2.dedupSkip={t2.get('dedupSkip') if t2 else 'N/A'}")
        all_ok = False

    # ── Scenario 3: count_deduped on mixed list ───────────────────────────────
    dedup_count_s1 = count_deduped(result_s1)
    s3_ok = dedup_count_s1 == 1  # only child_title should be skipped

    if s3_ok:
        _ok(f"Scenario 3: count_deduped = {dedup_count_s1} (expected 1)")
    else:
        _ng("Scenario 3: count_deduped", f"got {dedup_count_s1}, expected 1")
        all_ok = False

    check_result = {
        "scenario1_group_covers_child": {
            "ok": s1_ok,
            "childDedupSkip": child.get("dedupSkip") if child else None,
        },
        "scenario2_separate_roles_no_skip": {
            "ok": s2_ok,
            "t1DedupSkip": t1.get("dedupSkip") if t1 else None,
            "t2DedupSkip": t2.get("dedupSkip") if t2 else None,
        },
        "scenario3_count_deduped": {"ok": s3_ok, "count": dedup_count_s1},
        "allOk": all_ok,
    }

    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "dedup.json", check_result)

    return 0 if all_ok else 1


# ─── check-quality ───────────────────────────────────────────────────────────

def cmd_check_quality(args: argparse.Namespace) -> int:
    """Verify quality gate gives PASS (≥ 65) on a full-roles scenario."""
    try:
        from typography.quality_gate import evaluate, LAYOUT_SCORE_THRESHOLD
        from typography.schemas import LayoutSlot
    except Exception as exc:
        _ng("typography.quality_gate import", str(exc))
        return 1

    # Full-pass scenario: background + main_image + title (Korean) + cta, all in safe zone
    classified = [
        {"id": "l1", "role": "background", "dedupSkip": False, "isKorean": False},
        {"id": "l2", "role": "main_image",  "dedupSkip": False, "isKorean": False},
        {"id": "l3", "role": "title",       "dedupSkip": False, "isKorean": True},
        {"id": "l4", "role": "cta",         "dedupSkip": False, "isKorean": False},
    ]
    slots = [
        LayoutSlot(role="background", x=0,   y=0,   w=1000, h=600, mode="cover"),
        LayoutSlot(role="main_image",  x=0,   y=50,  w=500,  h=500, mode="contain"),
        LayoutSlot(role="title",       x=520, y=50,  w=460,  h=100, mode="contain"),
        LayoutSlot(role="cta",         x=520, y=450, w=200,  h=60,  mode="contain"),
    ]

    try:
        result = evaluate(classified, slots, 1000, 600, had_korean=True)
    except Exception as exc:
        _ng("evaluate() raised exception", str(exc))
        if args.artifact_dir:
            _write_json(Path(args.artifact_dir) / "quality.json",
                        {"error": str(exc), "allOk": False})
        return 1

    q_score = result.quality_score
    success = result.success
    gate_pass = success and q_score >= LAYOUT_SCORE_THRESHOLD

    if gate_pass:
        _ok(f"Quality gate PASS (score={q_score:.1f} >= threshold={LAYOUT_SCORE_THRESHOLD})")
    else:
        _ng("Quality gate FAIL",
            f"score={q_score:.1f} threshold={LAYOUT_SCORE_THRESHOLD} success={success}")
        if result.warnings:
            _ng("warnings", str(result.warnings))

    check_result = {
        "qualityScore": q_score,
        "threshold": LAYOUT_SCORE_THRESHOLD,
        "success": success,
        "missingRoles": result.missing_roles,
        "safeZonePass": result.safe_zone_pass,
        "warnings": result.warnings,
        "gatePass": gate_pass,
        "allOk": gate_pass,
    }

    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "quality.json", check_result)

    return 0 if gate_pass else 1


# ─── report ──────────────────────────────────────────────────────────────────

def cmd_report(args: argparse.Namespace) -> int:
    """Aggregate artifact JSONs into a final verification report."""
    ad = args.artifact_dir

    import_data    = _load_json(ad, "import-check.json")
    roles_data     = _load_json(ad, "roles.json")
    templates_data = _load_json(ad, "templates.json")
    flags_data     = _load_json(ad, "flags.json")
    dedup_data     = _load_json(ad, "dedup.json")
    quality_data   = _load_json(ad, "quality.json")
    typo_health    = _load_json(ad, "typography-health.json")
    bg_health      = _load_json(ad, "background-health.json")

    # Parse pytest log
    tests_passed = 0
    tests_failed = 0
    stage19_passed = 0
    stage19_failed = 0
    for log_name, passed_var, failed_var in [
        ("stage20-pytest.log", "tests_passed", "tests_failed"),
        ("stage19-pytest.log", "stage19_passed", "stage19_failed"),
    ]:
        p = Path(ad) / log_name
        if p.exists():
            content = p.read_text(errors="replace")
            m = re.search(r"(\d+) passed", content)
            f = re.search(r"(\d+) failed", content)
            if log_name.startswith("stage20"):
                tests_passed  = int(m.group(1)) if m else 0
                tests_failed  = int(f.group(1)) if f else 0
            else:
                stage19_passed = int(m.group(1)) if m else 0
                stage19_failed = int(f.group(1)) if f else 0

    failures: list[str] = []
    if not import_data.get("stage20ImportReady"):
        failures.append("import_check_failed")
    if not roles_data.get("allOk"):
        failures.append("roles_check_failed")
    if not templates_data.get("allOk"):
        failures.append("templates_check_failed")
    if not flags_data.get("allOk"):
        failures.append("flags_check_failed")
    if not dedup_data.get("allOk"):
        failures.append("dedup_check_failed")
    if not quality_data.get("allOk"):
        failures.append("quality_gate_failed")
    if tests_failed > 0:
        failures.append(f"stage20_pytest_{tests_failed}_failed")
    if stage19_failed > 0:
        failures.append(f"stage19_regression_{stage19_failed}_failed")
    if typo_health and typo_health.get("status") != "ok":
        failures.append("typography_health_failed")
    if bg_health and bg_health.get("status") != "ok":
        failures.append("background_health_failed")

    verdict = "PASS" if not failures else "FAIL"

    report = {
        "gitSha":                  getattr(args, "git_sha", "unknown"),
        "timestamp":               getattr(args, "timestamp", "unknown"),
        "verdict":                 verdict,
        "containerBased":          True,
        "hostPythonRequired":      False,
        "verifyImage":             f"creative-stage20-verify:{getattr(args, 'git_sha', 'unknown')}",
        "importCheckOk":           import_data.get("stage20ImportReady", False),
        "rolesCheckOk":            roles_data.get("allOk", False),
        "templatesCheckOk":        templates_data.get("allOk", False),
        "flagsCheckOk":            flags_data.get("allOk", False),
        "dedupCheckOk":            dedup_data.get("allOk", False),
        "qualityGateOk":           quality_data.get("allOk", False),
        "typographyHealthOk":      typo_health.get("status") == "ok" if typo_health else None,
        "backgroundHealthOk":      bg_health.get("status") == "ok" if bg_health else None,
        "stage20TestsPassed":      tests_passed,
        "stage20TestsFailed":      tests_failed,
        "stage19RegressionPassed": stage19_passed,
        "stage19RegressionFailed": stage19_failed,
        "failureReasons":          failures,
        "productionImpact":        "none",
        "cleanupCompleted":        True,
    }

    output_path = getattr(args, "output", None) or str(Path(ad) / "stage20-verification-report.json")
    _write_json(output_path, report)

    print(f"[OK]  Report written: {output_path}")
    print(f"      verdict={verdict}")
    if failures:
        print(f"      failures={failures}")

    return 0 if verdict == "PASS" else 1


# ─── main ─────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stage 20 Typography Pipeline verification (runs inside Docker helper)"
    )
    p.add_argument(
        "command",
        choices=[
            "import-check", "check-roles", "check-templates",
            "check-flags", "check-dedup", "check-quality", "report",
        ],
        help="Verification subcommand to run",
    )
    p.add_argument("--artifact-dir", default="",
                   help="Host-mounted artifact directory (writable inside container)")
    p.add_argument("--git-sha",    default="unknown", help="Git SHA for report header")
    p.add_argument("--timestamp",  default="unknown", help="ISO timestamp for report header")
    p.add_argument("--output",     default="",
                   help="Output path for report JSON (report command only)")
    return p


_DISPATCH = {
    "import-check":    cmd_import_check,
    "check-roles":     cmd_check_roles,
    "check-templates": cmd_check_templates,
    "check-flags":     cmd_check_flags,
    "check-dedup":     cmd_check_dedup,
    "check-quality":   cmd_check_quality,
    "report":          cmd_report,
}


def main() -> int:
    args = _build_parser().parse_args()
    return _DISPATCH[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
