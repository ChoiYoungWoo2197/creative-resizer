"""
Worker Contract Smoke Test
Python Worker HTTP API를 직접 테스트 (Spring Boot 경유 없음).
Java WorkerResponse 계약과 JSON 필드 타입이 일치하는지 검증.

핵심 검증 항목:
  - safeZoneViolations: List<String>  (Python: hardFailReasons 필터링 결과)
  - fallbackErrors:     List<Map>     (Python: {"step":..., "message":...} dict 리스트)
  - count, width, height: int
  - layoutScore, candidateCount: float/int or null

Environment:
  WORKER_URL   : Worker base URL  (default: http://worker:5000)
  SMOKE_TIMEOUT: per-request timeout (default: 30)
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

WORKER_URL = os.environ.get("WORKER_URL", "http://worker:5000").rstrip("/")
TIMEOUT    = int(os.environ.get("SMOKE_TIMEOUT", "30"))

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


def _request_json(method: str, url: str, body=None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except urllib.error.URLError as e:
        raise RuntimeError(f"URLError {e.reason}") from e


# ── Step 1: /health ──────────────────────────────────────────────────────────

def step_worker_health():
    print("\n── Step 1: Worker /health ──────────────────────────────────")
    try:
        status, body = _request_json("GET", f"{WORKER_URL}/health")
    except Exception as e:
        _fail("Worker /health: reachable", str(e), "HTTP 200")
        return False
    _record("Worker /health: HTTP 200", status == 200, str(status), "200")
    if isinstance(body, dict):
        _record("Worker /health: status=ok", body.get("status") == "ok",
                str(body.get("status")), "ok")
    else:
        _fail("Worker /health: JSON body", str(body)[:100], '{"status":"ok"}')
    return status == 200


# ── Step 2: /generate — WorkerResponse JSON 계약 ─────────────────────────────

def step_worker_generate_contract():
    print("\n── Step 2: Worker /generate — WorkerResponse contract ──────")

    payload = {
        "jobId": "smoke-worker-contract-direct",
        "psdPath": "/app/fixtures/test_banner.jpg",
        "sourceType": "image",
        "resizeMode": "smart-fit",
        "smartFitStrength": "balanced",
        "focalPosition": "center",
        "outputFormat": "jpg",
        "objectReflowEnabled": False,
        "specs": [
            {"media": "smoke", "name": "Smoke 300x250",
             "slug": "smoke-300x250", "width": 300, "height": 250},
            {"media": "smoke", "name": "Smoke 728x90",
             "slug": "smoke-728x90", "width": 728, "height": 90},
        ]
    }

    try:
        status, body = _request_json("POST", f"{WORKER_URL}/generate", payload)
    except Exception as e:
        _fail("Worker /generate: reachable", str(e), "HTTP 200")
        return

    _record("Worker /generate: HTTP 200", status == 200, str(status), "200")
    if status != 200:
        _fail("contract check skipped", f"HTTP {status}", "200 required")
        return

    # ── top-level ─────────────────────────────────────────────────
    _record("jobId=smoke-worker-contract-direct",
            body.get("jobId") == "smoke-worker-contract-direct",
            str(body.get("jobId")), "smoke-worker-contract-direct")

    count = body.get("count")
    _record("count is int",      isinstance(count, int), type(count).__name__, "int")
    _record("count >= 1",        isinstance(count, int) and count >= 1, str(count), ">= 1")

    results_list = body.get("results")
    _record("results is list",   isinstance(results_list, list),
            type(results_list).__name__, "list")
    _record("results non-empty", isinstance(results_list, list) and len(results_list) > 0,
            str(len(results_list or [])), "> 0")

    missing = body.get("missingRatioTypes")
    _record("missingRatioTypes is list",
            isinstance(missing, list), type(missing).__name__, "list")

    if not isinstance(results_list, list) or not results_list:
        _fail("ResultItem contract check skipped", "no results", "need >= 1")
        return

    # ── ResultItem[0] ─────────────────────────────────────────────
    item = results_list[0]
    print(f"\n  ResultItem[0] keys: {sorted(item.keys())}", flush=True)

    _record("width is int",  isinstance(item.get("width"), int),
            type(item.get("width")).__name__, "int")
    _record("height is int", isinstance(item.get("height"), int),
            type(item.get("height")).__name__, "int")

    # safeZoneViolations — 핵심: List<String> (dict 아님)
    sz = item.get("safeZoneViolations")
    if sz is None or sz == []:
        _pass("safeZoneViolations: null/[] — no violations (OK)")
    elif isinstance(sz, list):
        bad = [v for v in sz if not isinstance(v, str)]
        _record("safeZoneViolations: all elements are str",
                len(bad) == 0,
                f"non-str items={[type(v).__name__ for v in bad[:3]]}" if bad else "all str",
                "List<String>")
        print(f"  safeZoneViolations sample: {sz[:3]}", flush=True)
    else:
        _fail("safeZoneViolations: must be list or null",
              f"type={type(sz).__name__}", "list[str] or null")

    # fallbackErrors — List<Map<String,Object>>
    fe = item.get("fallbackErrors")
    if fe is None or fe == []:
        _pass("fallbackErrors: null/[] (OK)")
    elif isinstance(fe, list):
        bad = [v for v in fe if not isinstance(v, dict)]
        _record("fallbackErrors: all elements are dict",
                len(bad) == 0,
                f"non-dict={[type(v).__name__ for v in bad[:3]]}" if bad else "all dict",
                "List<Map>")
    else:
        _fail("fallbackErrors: must be list or null",
              f"type={type(fe).__name__}", "list[dict] or null")

    # layoutScore: float/int or null
    ls = item.get("layoutScore")
    _record("layoutScore is number or null",
            ls is None or isinstance(ls, (float, int)),
            type(ls).__name__, "float/int/null")

    # candidateCount: int or null
    cc = item.get("candidateCount")
    _record("candidateCount is int or null",
            cc is None or isinstance(cc, int),
            type(cc).__name__, "int/null")

    # safeZonePassed: bool or null
    szp = item.get("safeZonePassed")
    _record("safeZonePassed is bool or null",
            szp is None or isinstance(szp, bool),
            type(szp).__name__, "bool/null")

    print(f"\n  Full ResultItem[0]:", flush=True)
    for k, v in sorted(item.items()):
        print(f"    {k}: {repr(v)[:100]}", flush=True)


# ── summary ──────────────────────────────────────────────────────────────────

def _print_summary():
    passed  = sum(1 for r in results if r["passed"])
    failed  = sum(1 for r in results if not r["passed"])
    total   = len(results)
    outcome = "PASS" if failed == 0 else "FAIL"

    print("\n" + "=" * 56)
    print("Worker Contract Smoke Test — Summary")
    print(f"  Total  : {total}")
    print(f"  Pass   : {passed}")
    print(f"  Fail   : {failed}")
    print(f"  Result : {outcome}")
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
    print("Worker Contract Smoke Test (Direct HTTP)")
    print(f"  WORKER_URL : {WORKER_URL}")
    print(f"  Timeout    : {TIMEOUT}s")
    print(f"  Started    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 56)

    healthy = step_worker_health()
    if not healthy:
        print("\n  Worker unreachable — aborting contract test", flush=True)
        _print_summary()
        sys.exit(1)

    step_worker_generate_contract()

    _print_summary()
    failed = sum(1 for r in results if not r["passed"])
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
