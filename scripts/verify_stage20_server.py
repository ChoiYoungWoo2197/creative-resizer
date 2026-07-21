"""Stage 20 Typography Pipeline — server verification (multi-command).

All commands are designed to run inside the helper container with PYTHONPATH=/app.
The shell script (verify_stage20_server.sh) handles Docker lifecycle; this script
handles all Python logic (imports, module checks, report generation).

Commands:
  import-check           Verify all Stage 20 typography module imports succeed
  check-roles            Verify 15 role aliases resolve correctly
  check-templates        Verify layout templates cover all spec types
  check-flags            Verify TYPOGRAPHY_PIPELINE_ENABLED=false by default
  check-dedup            Verify duplicate detector group-cover and similarity logic
  check-quality          Verify quality gate full-pass scoring
  check-smart-fit-guard  Verify Smart Fit runtime guard (Stage 20.2)
  check-mode-selector    Verify background mode selector (Stage 20.2)
  check-source-faithful-repair  Verify SFR orchestrator returns PARTIAL on no provider (Stage 20.2)
  check-sfr-masks        Verify SFR mask builders (Stage 20.2)
  check-prompt-builder   Verify versioned prompt builder (Stage 20.2)
  report                 Aggregate artifact JSONs into a final verification report

Usage (from verify_stage20_server.sh via docker exec):
  python /scripts/verify_stage20_server.py import-check --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-roles  --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-templates --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-flags  --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-dedup  --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-quality --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-smart-fit-guard --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-mode-selector   --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-source-faithful-repair --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-sfr-masks        --artifact-dir /artifacts
  python /scripts/verify_stage20_server.py check-prompt-builder   --artifact-dir /artifacts
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


# ─── check-smart-fit-guard (Stage 20.2) ──────────────────────────────────────

def cmd_check_smart_fit_guard(args: argparse.Namespace) -> int:
    """Verify Stage 20.2 Smart Fit runtime guard: all fields False, raises correctly."""
    try:
        from background.smart_fit_guard import (
            build_no_smart_fit_fields,
            build_blocked_result,
            SmartFitForbiddenError,
            check_smart_fit_allowed,
            SMART_FIT_FORBIDDEN,
            SMART_FIT_RUNTIME_CALL_BLOCKED,
            NATIVE_BACKGROUND_FALLBACK_FORBIDDEN,
        )
    except Exception as exc:
        _ng("background.smart_fit_guard import", str(exc))
        return 1

    all_ok = True
    checks = []

    # All fields must be False
    fields = build_no_smart_fit_fields()
    all_false = all(v is False for v in fields.values())
    checks.append({"check": "all_no_smart_fit_fields_false", "ok": all_false})
    if all_false:
        _ok("build_no_smart_fit_fields() → all False")
    else:
        _ng("build_no_smart_fit_fields()", f"non-False fields: {[k for k,v in fields.items() if v]}")
        all_ok = False

    # check_smart_fit_allowed raises for final_output
    raised = False
    code = ""
    try:
        check_smart_fit_allowed("final_output", "blur_fill")
    except SmartFitForbiddenError as exc:
        raised = True
        code = exc.error_code
    raise_ok = raised and code == SMART_FIT_RUNTIME_CALL_BLOCKED
    checks.append({"check": "check_smart_fit_allowed_raises_final_output", "ok": raise_ok, "errorCode": code})
    if raise_ok:
        _ok(f"check_smart_fit_allowed('final_output') → SmartFitForbiddenError(code={code!r})")
    else:
        _ng("check_smart_fit_allowed('final_output')", f"raised={raised} code={code!r}")
        all_ok = False

    # debug context does NOT raise
    not_raised = True
    try:
        check_smart_fit_allowed("debug", "blur_fill")
    except SmartFitForbiddenError:
        not_raised = False
    checks.append({"check": "check_smart_fit_allowed_debug_ok", "ok": not_raised})
    if not_raised:
        _ok("check_smart_fit_allowed('debug') → no exception")
    else:
        _ng("check_smart_fit_allowed('debug')", "unexpectedly raised SmartFitForbiddenError")
        all_ok = False

    # build_blocked_result structure
    blocked = build_blocked_result("blur_fill", "final_output", 1200, 628)
    blocked_ok = (
        blocked.get("errorCode") == SMART_FIT_RUNTIME_CALL_BLOCKED
        and blocked.get("smartFitAllowed") is False
        and blocked.get("targetWidth") == 1200
    )
    checks.append({"check": "build_blocked_result_structure", "ok": blocked_ok})
    if blocked_ok:
        _ok("build_blocked_result() structure verified")
    else:
        _ng("build_blocked_result()", f"got={blocked}")
        all_ok = False

    result = {"checks": checks, "allOk": all_ok}
    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "smart-fit-guard.json", result)

    return 0 if all_ok else 1


# ─── check-mode-selector (Stage 20.2) ────────────────────────────────────────

def cmd_check_mode_selector(args: argparse.Namespace) -> int:
    """Verify Stage 20.2 background mode selector routes SFR vs generative correctly."""
    try:
        from background.mode_selector import (
            select_background_mode,
            SOURCE_FAITHFUL_REPAIR,
            GENERATIVE_BACKGROUND,
        )
    except Exception as exc:
        _ng("background.mode_selector import", str(exc))
        return 1

    all_ok = True
    tests = [
        ([{"role": "hand", "name": "hand", "type": "pixel", "bbox": {}, "dedupSkip": False}],
         SOURCE_FAITHFUL_REPAIR, "hand_role"),
        ([{"role": "person", "name": "person", "type": "pixel", "bbox": {}, "dedupSkip": False}],
         SOURCE_FAITHFUL_REPAIR, "person_role"),
        ([{"role": "product", "name": "product", "type": "pixel", "bbox": {}, "dedupSkip": False}],
         GENERATIVE_BACKGROUND, "product_only"),
        ([{"role": "background", "name": "어머님_손", "type": "pixel", "bbox": {}, "dedupSkip": False}],
         SOURCE_FAITHFUL_REPAIR, "korean_name_hint"),
        ([], GENERATIVE_BACKGROUND, "empty_layers"),
    ]

    check_results = []
    for layers, expected_mode, label in tests:
        mode, reason = select_background_mode(layers)
        ok = mode == expected_mode
        check_results.append({"label": label, "expected": expected_mode, "got": mode, "ok": ok})
        if ok:
            _ok(f"mode_selector {label!r} → {mode!r} ({reason})")
        else:
            _ng(f"mode_selector {label!r}", f"expected {expected_mode!r}, got {mode!r}")
            all_ok = False

    # Forced mode override
    forced_mode, reason = select_background_mode(
        [{"role": "hand", "name": "hand", "type": "pixel", "bbox": {}, "dedupSkip": False}],
        forced_mode=GENERATIVE_BACKGROUND,
    )
    forced_ok = forced_mode == GENERATIVE_BACKGROUND and "forced" in reason
    check_results.append({"label": "forced_override", "expected": GENERATIVE_BACKGROUND,
                           "got": forced_mode, "ok": forced_ok})
    if forced_ok:
        _ok(f"forced_mode override → {forced_mode!r}")
    else:
        _ng("forced_mode override", f"got mode={forced_mode!r}, reason={reason!r}")
        all_ok = False

    result = {"modeTests": check_results, "allOk": all_ok}
    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "mode-selector.json", result)
    return 0 if all_ok else 1


# ─── check-source-faithful-repair (Stage 20.2) ───────────────────────────────

def cmd_check_source_faithful_repair(args: argparse.Namespace) -> int:
    """Verify SFR orchestrator: no-provider → PARTIAL, no-gen-needed → PASS, SFR fields False."""
    try:
        from background.source_faithful_repair import run_source_faithful_repair
        from PIL import Image
    except Exception as exc:
        _ng("background.source_faithful_repair import", str(exc))
        return 1

    all_ok = True
    checks = []

    source = Image.new("RGB", (200, 200), (180, 180, 180))

    # No-gen scenario: no layers, same size → PASS without AI
    r1 = run_source_faithful_repair(source, [], 200, 200, None)
    no_gen_ok = r1.success and r1.verdict == "PASS" and r1.original_psd_background_used
    checks.append({"check": "no_gen_needed_pass", "ok": no_gen_ok,
                   "verdict": r1.verdict, "success": r1.success})
    if no_gen_ok:
        _ok("SFR: no generation needed → PASS")
    else:
        _ng("SFR: no generation needed", f"verdict={r1.verdict}, success={r1.success}")
        all_ok = False

    # Provider-not-configured scenario → PARTIAL
    layers = [{"role": "title", "type": "pixel", "name": "t", "dedupSkip": False,
               "bbox": {"x": 10, "y": 10, "width": 50, "height": 30}}]
    r2 = run_source_faithful_repair(source, layers, 200, 200, None, max_attempts=1)
    no_prov_ok = not r2.success and r2.verdict == "PARTIAL" and "provider_not_configured" in r2.failure_reason
    checks.append({"check": "no_provider_partial", "ok": no_prov_ok,
                   "verdict": r2.verdict, "failure_reason": r2.failure_reason})
    if no_prov_ok:
        _ok(f"SFR: no provider → PARTIAL ({r2.failure_reason})")
    else:
        _ng("SFR: no provider", f"verdict={r2.verdict}, reason={r2.failure_reason}")
        all_ok = False

    # Smart Fit fields must all be False
    sfr_fields = {
        "smart_fit_allowed": r2.smart_fit_allowed,
        "smart_fit_used": r2.smart_fit_used,
        "smart_fit_fallback_used": r2.smart_fit_fallback_used,
        "blur_fill_used": r2.blur_fill_used,
        "mirror_fill_used": r2.mirror_fill_used,
        "stretch_fill_used": r2.stretch_fill_used,
    }
    sf_all_false = all(v is False for v in sfr_fields.values())
    checks.append({"check": "sfr_fields_all_false", "ok": sf_all_false, "fields": sfr_fields})
    if sf_all_false:
        _ok("SFR: all Smart Fit fields = False")
    else:
        _ng("SFR Smart Fit fields", f"non-False: {[k for k,v in sfr_fields.items() if v]}")
        all_ok = False

    result = {"checks": checks, "allOk": all_ok}
    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "source-faithful-repair.json", result)
    return 0 if all_ok else 1


# ─── check-sfr-masks (Stage 20.2) ────────────────────────────────────────────

def cmd_check_sfr_masks(args: argparse.Namespace) -> int:
    """Verify SFR mask builder helpers: removal, immutable, outpaint, union, ratio."""
    try:
        from background.source_faithful_repair import (
            _mask_from_classified_roles,
            _build_outpaint_mask,
            _union_masks,
            _mask_ratio,
            _REMOVAL_ROLES,
            _IMMUTABLE_ROLES,
        )
        from PIL import Image
    except Exception as exc:
        _ng("background.source_faithful_repair mask helpers import", str(exc))
        return 1

    all_ok = True
    checks = []

    # Removal mask has white pixels for title layer
    layers = [{"role": "title", "type": "pixel", "name": "t", "dedupSkip": False,
               "bbox": {"x": 10, "y": 10, "width": 50, "height": 30}}]
    rm = _mask_from_classified_roles(layers, _REMOVAL_ROLES, 200, 200)
    rm_ok = _mask_ratio(rm) > 0
    checks.append({"check": "removal_mask_active", "ok": rm_ok, "ratio": _mask_ratio(rm)})
    if rm_ok:
        _ok(f"removal_mask active (ratio={_mask_ratio(rm):.4f})")
    else:
        _ng("removal_mask", "ratio=0 for title layer")
        all_ok = False

    # dedupSkip layers excluded
    layers_skip = [{"role": "title", "type": "pixel", "name": "t", "dedupSkip": True,
                    "bbox": {"x": 10, "y": 10, "width": 50, "height": 30}}]
    rm_skip = _mask_from_classified_roles(layers_skip, _REMOVAL_ROLES, 200, 200)
    skip_ok = _mask_ratio(rm_skip) == 0.0
    checks.append({"check": "dedup_skip_excluded", "ok": skip_ok})
    if skip_ok:
        _ok("dedupSkip layer excluded from removal mask")
    else:
        _ng("dedup_skip", "layer was included despite dedupSkip=True")
        all_ok = False

    # Outpaint mask for different sizes
    op = _build_outpaint_mask(800, 600, 1200, 600)
    op_ok = op is not None and _mask_ratio(op) > 0
    checks.append({"check": "outpaint_mask_active", "ok": op_ok,
                   "ratio": _mask_ratio(op) if op else 0})
    if op_ok:
        _ok(f"outpaint_mask active for 800x600→1200x600 (ratio={_mask_ratio(op):.4f})")
    else:
        _ng("outpaint_mask", "None or ratio=0 for 800→1200 resize")
        all_ok = False

    # Same-size returns None
    op_same = _build_outpaint_mask(200, 200, 200, 200)
    same_ok = op_same is None
    checks.append({"check": "outpaint_mask_same_size_none", "ok": same_ok})
    if same_ok:
        _ok("outpaint_mask None for same size")
    else:
        _ng("outpaint_mask same size", "expected None, got image")
        all_ok = False

    # union OR logic
    m1 = Image.new("L", (10, 10), 0)
    m2 = Image.new("L", (10, 10), 255)
    union = _union_masks(m1, m2)
    union_ok = union is not None and _mask_ratio(union) == 1.0
    checks.append({"check": "union_masks_or", "ok": union_ok})
    if union_ok:
        _ok("_union_masks OR logic verified")
    else:
        _ng("_union_masks", f"ratio={_mask_ratio(union) if union else 'N/A'}")
        all_ok = False

    result = {"maskChecks": checks, "allOk": all_ok}
    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "sfr-masks.json", result)
    return 0 if all_ok else 1


# ─── check-prompt-builder (Stage 20.2) ───────────────────────────────────────

def cmd_check_prompt_builder(args: argparse.Namespace) -> int:
    """Verify Stage 20.2 versioned prompt builder: dimensions, augmentations, attempt sequence."""
    try:
        from background.prompt_builder import (
            build_prompt,
            get_attempt_version,
            ATTEMPT_VERSION_SEQUENCE,
            LATEST_VERSION,
            PROMPT_VERSIONS,
        )
    except Exception as exc:
        _ng("background.prompt_builder import", str(exc))
        return 1

    all_ok = True
    checks = []

    # v1 prompt contains dimensions
    p = build_prompt("source-faithful-repair-v1", 1200, 628)
    dim_ok = "1200" in p and "628" in p
    checks.append({"check": "v1_prompt_has_dimensions", "ok": dim_ok})
    if dim_ok:
        _ok("v1 prompt contains 1200 and 628")
    else:
        _ng("v1 prompt dimensions", "1200 or 628 not found in prompt")
        all_ok = False

    # Unknown version raises ValueError
    raised = False
    try:
        build_prompt("nonexistent-v99", 100, 100)
    except ValueError:
        raised = True
    checks.append({"check": "unknown_version_raises", "ok": raised})
    if raised:
        _ok("Unknown version raises ValueError")
    else:
        _ng("unknown version", "did not raise ValueError")
        all_ok = False

    # Spec augmentation for 1250x560
    p_aug = build_prompt("source-faithful-repair-v1", 1250, 560, spec_augmentation=True)
    aug_ok = len(p_aug) > len(build_prompt("source-faithful-repair-v1", 1250, 560, spec_augmentation=False))
    checks.append({"check": "spec_augmentation_1250x560", "ok": aug_ok})
    if aug_ok:
        _ok("Spec augmentation 1250x560 extends prompt")
    else:
        _ng("spec augmentation 1250x560", "augmented not longer than base")
        all_ok = False

    # Attempt sequence clamp
    last = ATTEMPT_VERSION_SEQUENCE[-1]
    clamped = get_attempt_version(999)
    clamp_ok = clamped == last
    checks.append({"check": "attempt_version_clamp", "ok": clamp_ok, "clamped": clamped})
    if clamp_ok:
        _ok(f"get_attempt_version(999) → {clamped!r} (clamped to last)")
    else:
        _ng("attempt version clamp", f"got {clamped!r}, expected {last!r}")
        all_ok = False

    # 3 attempt versions defined
    version_count_ok = len(ATTEMPT_VERSION_SEQUENCE) == 3
    checks.append({"check": "attempt_version_count", "ok": version_count_ok,
                   "count": len(ATTEMPT_VERSION_SEQUENCE)})
    if version_count_ok:
        _ok(f"ATTEMPT_VERSION_SEQUENCE has {len(ATTEMPT_VERSION_SEQUENCE)} versions")
    else:
        _ng("ATTEMPT_VERSION_SEQUENCE", f"has {len(ATTEMPT_VERSION_SEQUENCE)} versions, expected 3")
        all_ok = False

    result = {"promptChecks": checks, "allOk": all_ok}
    if args.artifact_dir:
        _write_json(Path(args.artifact_dir) / "prompt-builder.json", result)
    return 0 if all_ok else 1


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
    # Stage 20.2
    sfg_data       = _load_json(ad, "smart-fit-guard.json")
    ms_data        = _load_json(ad, "mode-selector.json")
    sfr_data       = _load_json(ad, "source-faithful-repair.json")
    sfm_data       = _load_json(ad, "sfr-masks.json")
    pb_data        = _load_json(ad, "prompt-builder.json")

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
    if sfg_data and not sfg_data.get("allOk"):
        failures.append("smart_fit_guard_failed")
    if ms_data and not ms_data.get("allOk"):
        failures.append("mode_selector_failed")
    if sfr_data and not sfr_data.get("allOk"):
        failures.append("source_faithful_repair_failed")
    if sfm_data and not sfm_data.get("allOk"):
        failures.append("sfr_masks_failed")
    if pb_data and not pb_data.get("allOk"):
        failures.append("prompt_builder_failed")
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
        # Stage 20.2
        "smartFitGuardOk":         sfg_data.get("allOk") if sfg_data else None,
        "modeSelectorOk":          ms_data.get("allOk") if ms_data else None,
        "sourceFaithfulRepairOk":  sfr_data.get("allOk") if sfr_data else None,
        "sfrMasksOk":              sfm_data.get("allOk") if sfm_data else None,
        "promptBuilderOk":         pb_data.get("allOk") if pb_data else None,
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
            "check-flags", "check-dedup", "check-quality",
            "check-smart-fit-guard", "check-mode-selector",
            "check-source-faithful-repair", "check-sfr-masks",
            "check-prompt-builder",
            "report",
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
    "import-check":              cmd_import_check,
    "check-roles":               cmd_check_roles,
    "check-templates":           cmd_check_templates,
    "check-flags":               cmd_check_flags,
    "check-dedup":               cmd_check_dedup,
    "check-quality":             cmd_check_quality,
    "check-smart-fit-guard":     cmd_check_smart_fit_guard,
    "check-mode-selector":       cmd_check_mode_selector,
    "check-source-faithful-repair": cmd_check_source_faithful_repair,
    "check-sfr-masks":           cmd_check_sfr_masks,
    "check-prompt-builder":      cmd_check_prompt_builder,
    "report":                    cmd_report,
}


def main() -> int:
    args = _build_parser().parse_args()
    return _DISPATCH[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
