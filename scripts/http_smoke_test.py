"""
BannerSpec Stage 8 HTTP Smoke Test
stdlib only (urllib + json) — no external packages required.

Environment:
  API_URL      : Spring Boot base URL  (default: http://localhost:8081)
  SMOKE_TIMEOUT: per-request timeout   (default: 30)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

API_URL = os.environ.get("API_URL", "http://localhost:8081").rstrip("/")
TIMEOUT = int(os.environ.get("SMOKE_TIMEOUT", "30"))

# ── result tracking ──────────────────────────────────────────────────────────

results = []


def _record(label: str, passed: bool, actual: str = "", expected: str = ""):
    results.append({"label": label, "passed": passed, "actual": actual, "expected": expected})
    icon = "[PASS]" if passed else "[FAIL]"
    print(f"{icon} {label}", flush=True)
    if not passed:
        print(f"       expected : {expected}", flush=True)
        print(f"       actual   : {actual}",   flush=True)


def _pass(label: str, note: str = ""):
    _record(label, True, note)


def _fail(label: str, actual: str, expected: str = ""):
    _record(label, False, actual, expected)


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _request(method: str, path: str, body=None) -> tuple[int, dict | list | str]:
    url = API_URL + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except urllib.error.URLError as e:
        raise RuntimeError(f"URLError {e.reason}") from e


def get(path: str):
    return _request("GET", path)


def post(path: str, body=None):
    return _request("POST", path, body)


# ── smoke steps ──────────────────────────────────────────────────────────────

def step_readiness():
    print("\n── Step 1: API readiness ───────────────────────────────────")
    status, body = get("/actuator/health")
    if status == 200 and isinstance(body, dict) and body.get("status") == "UP":
        _pass("API readiness (actuator/health)", f"status={body.get('status')}")
    else:
        _fail("API readiness (actuator/health)",
              f"HTTP {status} body={str(body)[:200]}", "200 status=UP")
        _abort("API is not healthy — aborting smoke test")


def step_initial_state():
    print("\n── Step 2: MongoDB initial state (empty DB check) ─────────")
    status, body = get("/api/banner-specs?media=naver")
    if status == 200 and isinstance(body, list):
        count = len(body)
        if count == 0:
            _pass("Initial DB is empty (pre-seed)", "count=0")
        else:
            # smoke DB might already have data from a previous partial run
            print(f"  INFO: {count} docs already in DB — seed will mark them unchanged")
            _pass("Initial DB check (may have prior data)", f"count={count}")
    else:
        _fail("Initial DB check", f"HTTP {status}", "200 []")


def step_seed_first():
    print("\n── Step 3: Naver seed — 1st run (all INSERTED) ────────────")
    status, body = post("/api/banner-specs/seed?media=naver")
    if status != 200 or not isinstance(body, dict):
        _fail("Seed 1st run: HTTP 200", f"HTTP {status} body={str(body)[:200]}", "200")
        return None

    loaded    = body.get("loaded",    -1)
    inserted  = body.get("inserted",  -1)
    updated   = body.get("updated",   -1)
    unchanged = body.get("unchanged", -1)
    failed    = body.get("failed",    -1)
    total     = body.get("total",     -1)

    _record("Seed 1st: loaded=68",    loaded    == 68, str(loaded),    "68")
    _record("Seed 1st: failed=0",     failed    == 0,  str(failed),    "0")
    _record("Seed 1st: total=68",     total     == 68, str(total),     "68")
    _record("Seed 1st: inserted+unchanged+updated=68",
            (inserted + updated + unchanged) == 68,
            f"{inserted}+{updated}+{unchanged}={inserted+updated+unchanged}", "68")
    print(f"  seed response: {body}", flush=True)
    return body


def step_list_api():
    print("\n── Step 4: GET /api/banner-specs?media=naver ───────────────")
    status, body = get("/api/banner-specs?media=naver")
    if status != 200 or not isinstance(body, list):
        _fail("List API: HTTP 200", f"HTTP {status}", "200")
        return []

    count = len(body)
    _record("Naver count=68",           count == 68,  str(count),  "68")

    bad_media = [s.get("slug") for s in body if s.get("media") != "naver"]
    _record("All media=naver",          len(bad_media) == 0,
            f"bad slugs={bad_media[:3]}" if bad_media else "OK", "0 bad")

    null_slugs = [i for i, s in enumerate(body) if not s.get("slug")]
    _record("No null slugs",            len(null_slugs) == 0,
            f"null at idx={null_slugs[:3]}" if null_slugs else "OK", "0 null")

    bad_dims = [s.get("slug") for s in body
                if not isinstance(s.get("width"), int) or not isinstance(s.get("height"), int)
                or s.get("width", 0) <= 0 or s.get("height", 0) <= 0]
    _record("No invalid dimensions",    len(bad_dims) == 0,
            f"bad={bad_dims[:3]}" if bad_dims else "OK", "all > 0")

    # 중복 slug 검사
    slugs = [s.get("slug") for s in body]
    unique = set(slugs)
    _record("Duplicate slug count=0",   len(slugs) == len(unique),
            f"duplicates={len(slugs)-len(unique)}", "0")

    return body


def step_detail_api():
    print("\n── Step 5: GET /api/banner-specs/naver/{slug} ──────────────")
    slug = "naver-gfa-mobile-da-image-banner-1250x560"
    status, body = get(f"/api/banner-specs/naver/{slug}")
    if status != 200 or not isinstance(body, dict):
        _fail("Detail API: HTTP 200", f"HTTP {status}", "200")
        return

    _record("Detail: slug matches",         body.get("slug") == slug,
            body.get("slug"), slug)
    _record("Detail: width=1250",           body.get("width") == 1250,
            str(body.get("width")), "1250")
    _record("Detail: height=560",           body.get("height") == 560,
            str(body.get("height")), "560")
    _record("Detail: safeZoneParseStatus=parsed_text",
            body.get("safeZoneParseStatus") == "parsed_text",
            body.get("safeZoneParseStatus"), "parsed_text")

    sz = body.get("safeZone") or {}
    _record("Detail: safeZone.top=50",      sz.get("top") == 50,
            str(sz.get("top")), "50")
    _record("Detail: safeZone.right=240",   sz.get("right") == 240,
            str(sz.get("right")), "240")
    _record("Detail: safeZone.bottom=35",   sz.get("bottom") == 35,
            str(sz.get("bottom")), "35")
    _record("Detail: safeZone.left=240",    sz.get("left") == 240,
            str(sz.get("left")), "240")

    _record("Detail: safeTop=50",           body.get("safeTop") == 50,
            str(body.get("safeTop")), "50")
    _record("Detail: safeZoneWidth=770",    body.get("safeZoneWidth") == 770,
            str(body.get("safeZoneWidth")), "770")
    _record("Detail: safeZoneHeight=475",   body.get("safeZoneHeight") == 475,
            str(body.get("safeZoneHeight")), "475")


def step_not_found():
    print("\n── Step 6: Missing slug → 404 ─────────────────────────────")
    status, _ = get("/api/banner-specs/naver/not-exists-slug-xyz")
    _record("Missing slug: 404",   status == 404,  str(status), "404")
    _record("Missing slug: not 500", status != 500, str(status), "!= 500")


def step_wrong_method():
    print("\n── Step 7: Wrong method → 405 ─────────────────────────────")
    # GET-only endpoint에 DELETE 시도
    status, _ = _request("DELETE", "/api/banner-specs?media=naver")
    _record("Wrong method: 405", status == 405, str(status), "405")
    _record("Wrong method: not 500", status != 500, str(status), "!= 500")


def step_diagram_unreadable(specs: list):
    print("\n── Step 8: diagram_unreadable item check ───────────────────")
    dr_items = [s for s in specs if s.get("safeZoneParseStatus") == "diagram_unreadable"]
    _record("diagram_unreadable items exist",
            len(dr_items) > 0, str(len(dr_items)), ">0")

    if dr_items:
        sample = dr_items[0]
        _record("diagram_unreadable: safeZone is null",
                sample.get("safeZone") is None,
                str(sample.get("safeZone")), "null")
        slug = sample.get("slug", "?")
        print(f"  sample: slug={slug} safeZoneParseStatus={sample.get('safeZoneParseStatus')}",
              flush=True)

    _record("diagram_unreadable count=65",
            len(dr_items) == 65, str(len(dr_items)), "65")


def step_seed_second():
    print("\n── Step 9: Naver seed — 2nd run (all UNCHANGED) ───────────")
    status, body = post("/api/banner-specs/seed?media=naver")
    if status != 200 or not isinstance(body, dict):
        _fail("Seed 2nd run: HTTP 200", f"HTTP {status}", "200")
        return None

    loaded    = body.get("loaded",    -1)
    inserted  = body.get("inserted",  -1)
    updated   = body.get("updated",   -1)
    unchanged = body.get("unchanged", -1)
    failed    = body.get("failed",    -1)
    total     = body.get("total",     -1)

    _record("Seed 2nd: loaded=68",     loaded    == 68, str(loaded),    "68")
    _record("Seed 2nd: inserted=0",    inserted  == 0,  str(inserted),  "0")
    _record("Seed 2nd: updated=0",     updated   == 0,  str(updated),   "0")
    _record("Seed 2nd: unchanged=68",  unchanged == 68, str(unchanged), "68")
    _record("Seed 2nd: failed=0",      failed    == 0,  str(failed),    "0")
    _record("Seed 2nd: total=68",      total     == 68, str(total),     "68")
    print(f"  seed response: {body}", flush=True)
    return body


def step_idempotency():
    print("\n── Step 10: Idempotency — count still 68 after 2nd seed ───")
    status, body = get("/api/banner-specs?media=naver")
    if status != 200 or not isinstance(body, list):
        _fail("Idempotency check: HTTP 200", f"HTTP {status}", "200")
        return
    count = len(body)
    _record("Count still 68 after 2nd seed", count == 68, str(count), "68")

    slugs = [s.get("slug") for s in body]
    unique = set(slugs)
    _record("No duplicate slugs after 2nd seed",
            len(slugs) == len(unique),
            f"duplicates={len(slugs)-len(unique)}", "0")


def step_worker_health_via_api():
    print("\n── Step 11: Worker health via Spring Boot Smoke API ────────")
    status, body = get("/api/smoke/worker-health")
    if status != 200 or not isinstance(body, dict):
        _fail("Worker health: HTTP 200", f"HTTP {status}", "200")
        return
    worker_healthy = body.get("workerHealthy")
    _record("workerHealthy=true", worker_healthy is True,
            str(worker_healthy), "true")


def step_worker_generate_e2e():
    print("\n── Step 12: WorkerResponse E2E deserialization via API ─────")
    status, body = post("/api/smoke/worker-generate-test")
    if status != 200 or not isinstance(body, dict):
        _fail("Worker E2E: HTTP 200", f"HTTP {status} body={str(body)[:200]}", "200")
        return

    _record("Worker E2E: deserializationSuccess=true",
            body.get("deserializationSuccess") is True,
            str(body.get("deserializationSuccess")), "true")
    _record("Worker E2E: error=null",
            body.get("error") is None,
            str(body.get("error")), "null")
    _record("Worker E2E: count >= 1",
            isinstance(body.get("count"), int) and body.get("count", 0) >= 1,
            str(body.get("count")), ">= 1")

    sz_type = body.get("safeZoneViolationsType")
    _record("Worker E2E: safeZoneViolations type OK",
            sz_type in ("List<String>", "null"),
            str(sz_type), "List<String> or null")

    print(f"  Worker E2E response: {body}", flush=True)


# ── abort helper ─────────────────────────────────────────────────────────────

def _abort(reason: str):
    print(f"\n  ABORT: {reason}", flush=True)
    _print_summary()
    sys.exit(1)


# ── summary ──────────────────────────────────────────────────────────────────

def _print_summary():
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    total  = len(results)
    result = "PASS" if failed == 0 else "FAIL"

    print("\n" + "=" * 56)
    print("Summary:")
    print(f"  Total : {total}")
    print(f"  Pass  : {passed}")
    print(f"  Fail  : {failed}")
    print(f"  Result: {result}")
    if failed > 0:
        print("\nFailed checks:")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['label']}")
                print(f"      expected: {r['expected']}")
                print(f"      actual  : {r['actual']}")
    print("=" * 56)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 56)
    print(f"BannerSpec Stage 8 Smoke Test")
    print(f"  API_URL : {API_URL}")
    print(f"  Timeout : {TIMEOUT}s")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 56)

    step_readiness()
    step_initial_state()

    seed1 = step_seed_first()
    specs = step_list_api()
    step_detail_api()
    step_not_found()
    step_wrong_method()
    step_diagram_unreadable(specs)
    step_seed_second()
    step_idempotency()
    step_worker_health_via_api()
    step_worker_generate_e2e()

    _print_summary()

    failed = sum(1 for r in results if not r["passed"])
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
