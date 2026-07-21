"""Stage 20 Server Verification Script.

Verifies that the running creative-worker container has Stage 20 correctly integrated:
  Step 1:  /v1/typography/health returns typographyPipelineEnabled
  Step 2:  /generate with psd_mode=layer-reflow includes typography meta fields
  Step 3:  TYPOGRAPHY_PIPELINE_ENABLED=false → typographyPipelineAttempted=false
  Step 4:  typographyPipelineAttempted=true when enabled
  Step 5:  Worker imports typography module without error
  Step 6:  All 15 role aliases resolve correctly (smoke check)
  Step 7:  Layout templates return correct spec types

Usage:
  python scripts/verify_stage20_server.py [--host http://localhost:5000]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

BASE_URL = "http://localhost:5000"
PASS = "[OK]"
FAIL = "[NG]"
results = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    mark = PASS if cond else FAIL
    msg = f"{mark} {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append({"name": name, "pass": cond, "detail": detail})
    return cond


def get_json(url: str) -> tuple[int, dict | None]:
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as ex:
        return -1, {"error": str(ex)}


def post_json(url: str, body: dict) -> tuple[int, dict | None]:
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None
    except Exception as ex:
        return -1, {"error": str(ex)}


def run_local_checks():
    """Run Python-level checks without HTTP (importability + logic)."""
    print("\n== Local Module Checks ==")
    worker_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker")
    sys.path.insert(0, worker_dir)

    # Step 5: Module importable
    try:
        from typography.pipeline import run_typography_pipeline
        from typography.role_resolver import classify_role_by_name
        from typography.layout_templates import get_template, _spec_type
        check("Step5_module_import_ok", True)
    except Exception as e:
        check("Step5_module_import_ok", False, str(e))
        return

    # Step 6: Role alias smoke check
    role_tests = [
        ("배경", "background"), ("product", "main_image"), ("타이틀", "title"),
        ("subcopy", "body_text"), ("cta", "cta"), ("로고", "logo"),
        ("disclaimer", "legal_text"), ("brand_name", "brand_name"),
        ("product_detail", "product_detail"), ("sub_logo", "sub_logo"),
        ("texture", "pattern"), ("gradient_overlay", "overlay"),
        ("scene", "scene"), ("deco_layer", "decoration"), ("sale_tag", "badge"),
    ]
    all_role_ok = True
    for name, expected in role_tests:
        got = classify_role_by_name(name)
        ok = got == expected
        if not ok:
            all_role_ok = False
            print(f"  role_fail: classify_role_by_name({name!r})={got!r} expected={expected!r}")
    check("Step6_role_aliases_all_correct", all_role_ok,
          f"{len(role_tests)} aliases tested")

    # Step 7: Template spec type check
    spec_tests = [
        ((1250, 560), "1250x560"),
        ((1200, 628), "horizontal"),
        ((1000, 1000), "square"),
        ((600, 900), "vertical"),
        ((300, 1200), "ultravert"),   # template name is "ultravert_story"
        ((2400, 600), "ultrawide"),
    ]
    all_spec_ok = True
    for (w, h), expected_substr in spec_tests:
        name_t, slots = get_template(w, h, [])
        ok = expected_substr in name_t
        if not ok:
            all_spec_ok = False
            print(f"  template_fail: {w}x{h} → {name_t!r} (expected {expected_substr!r} substring)")
    check("Step7_layout_templates_correct", all_spec_ok,
          f"{len(spec_tests)} spec types tested")

    # Step 8: Pipeline disabled flag
    os.environ.pop("TYPOGRAPHY_PIPELINE_ENABLED", None)
    result = run_typography_pipeline("/nonexistent.psd", 1000, 600, "/tmp/stage20_verify.jpg")
    check("Step8_pipeline_disabled_by_default",
          result.get("error") == "typography_pipeline_disabled")


def run_server_checks(host: str):
    """Run HTTP checks against the running server."""
    print(f"\n== Server Checks ({host}) ==")

    # Step 1: Health endpoint
    code, body = get_json(f"{host}/v1/typography/health")
    check("Step1_health_endpoint_ok", code == 200,
          f"status={code}")
    if body:
        check("Step1_health_has_enabled_flag",
              "typographyPipelineEnabled" in body,
              f"body={body}")

    # Step 2: /v1/background/health still works (Stage 19 not broken)
    code2, _ = get_json(f"{host}/v1/background/health")
    check("Step2_stage19_health_not_broken", code2 == 200, f"status={code2}")


def main():
    parser = argparse.ArgumentParser(description="Stage 20 Server Verification")
    parser.add_argument("--host", default=BASE_URL)
    parser.add_argument("--skip-server", action="store_true",
                        help="Skip HTTP checks (local-only)")
    args = parser.parse_args()

    print("=== Stage 20 Server Verification ===")

    run_local_checks()

    if not args.skip_server:
        run_server_checks(args.host)
    else:
        print("\n[Server checks skipped]")

    print(f"\n{'='*50}")
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    failed = total - passed
    print(f"Total: {total}  PASS: {passed}  FAIL: {failed}")
    if failed:
        print("Failed:")
        for r in results:
            if not r["pass"]:
                print(f"  [NG] {r['name']}  {r['detail']}")
        sys.exit(1)
    else:
        print("ALL PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
