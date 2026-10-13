"""fal-ai 실제 청구 비용 측정.

생성 시점: fal 호출마다 request_id 를 pipeline.jsonl 에 `fal_request` 이벤트로 남긴다.
조회 시점: 로그의 request_id 로 fal Billing Events API 를 조회해 실제 `cost_total`(USD)을 합산한다.
청구 반영이 늦으면 같은 로그로 언제든 다시 조회할 수 있다. 예상 비용은 계산하지 않는다.

  python -m clean_pipeline.fal_cost <pipeline.jsonl | 디렉터리> [...] [--json] [--save]

인증: FAL_BILLING_KEY (BILLING 권한 키), 없으면 FAL_KEY 로 시도. 키는 어디에도 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

BILLING_URL = "https://api.fal.ai/v1/models/billing-events"
EVENT_NAME = "fal_request"
_REQUEST_ID_BATCH = 50  # Billing API request_id 필터 최대 개수
_QUERY_MARGIN = timedelta(hours=1)
REQUIRED_SCOPES = "billing:usage:read, auth:keys:read, account:users:read"


class BillingAuthError(RuntimeError):
    pass


# ── 생성 시점: request_id 수집·기록 ─────────────────────────────────────────────


def enqueue_kwargs(fal_subscribe: Callable, holder: dict) -> dict:
    """fal_client.subscribe 가 on_enqueue 를 지원할 때만 request_id 수집 콜백을 넘긴다."""
    try:
        supported = "on_enqueue" in inspect.signature(fal_subscribe).parameters
    except (TypeError, ValueError):
        supported = False
    if not supported:
        return {}

    def on_enqueue(request_id: str) -> None:
        holder["id"] = request_id

    return {"on_enqueue": on_enqueue}


def log_fal_request(
    logger: Any,
    stage: str,
    endpoint: str,
    holder: dict,
    target_width: int,
    target_height: int,
    started_at: float,
    ok: bool,
) -> None:
    """request_id 를 확보한 fal 호출을 기록한다. 비용 기록 실패가 생성 파이프라인을 막지 않도록 예외를 삼킨다."""
    request_id = holder.get("id")
    emit = getattr(logger, "fal_request", None)
    if not request_id or emit is None:
        return
    try:
        emit(
            stage,
            endpoint,
            request_id,
            f"{endpoint} request_id={request_id} {target_width}x{target_height}",
            metrics={
                "targetWidth": target_width,
                "targetHeight": target_height,
                "durationSec": round(time.time() - started_at, 2),
                "callStatus": "ok" if ok else "error",
            },
        )
    except Exception:
        pass


# ── 조회 시점: 로그 파싱 ────────────────────────────────────────────────────────


def read_fal_calls(jsonl_path: str | Path) -> list[dict]:
    """pipeline.jsonl 에서 fal_request 이벤트를 request_id 기준 중복 없이 순서대로 읽는다."""
    calls: list[dict] = []
    seen: set[str] = set()
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") != EVENT_NAME:
                continue
            request_id = record.get("falRequestId")
            if not request_id or request_id in seen:
                continue
            seen.add(request_id)
            metrics = record.get("metrics") or {}
            calls.append({
                "jobId": record.get("jobId"),
                "stage": record.get("stage"),
                "endpoint": record.get("falEndpoint"),
                "requestId": request_id,
                "output": f"{metrics.get('targetWidth')}x{metrics.get('targetHeight')}",
                "durationSec": metrics.get("durationSec"),
                "callStatus": metrics.get("callStatus"),
                "timestamp": record.get("timestamp"),
            })
    return calls


# ── 조회 시점: Billing Events API ───────────────────────────────────────────────


def _http_get(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
        return json.loads(resp.read())


def fetch_billing_events(request_ids: list[str], api_key: str, start: str) -> dict[str, list[dict]]:
    """request_id 별 billing event 목록. 요청 1건에 과금 라인이 여러 개일 수 있다."""
    headers = {"Authorization": f"Key {api_key}", "Accept": "application/json"}
    by_request: dict[str, list[dict]] = {}
    seen_lines: set[tuple] = set()

    for i in range(0, len(request_ids), _REQUEST_ID_BATCH):
        batch = request_ids[i:i + _REQUEST_ID_BATCH]
        cursor = None
        while True:
            params = [("request_id", rid) for rid in batch] + [("start", start)]
            if cursor:
                params.append(("cursor", cursor))
            try:
                data = _http_get(f"{BILLING_URL}?{urllib.parse.urlencode(params)}", headers)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise BillingAuthError(f"HTTP {exc.code}") from None
                raise RuntimeError(f"Billing API HTTP {exc.code}") from None

            for event in data.get("billing_events") or []:
                line_key = (
                    event.get("request_id"), event.get("timestamp"), event.get("unit"),
                    event.get("quantity"), event.get("cost_total"),
                )
                if line_key in seen_lines:
                    continue
                seen_lines.add(line_key)
                by_request.setdefault(event.get("request_id"), []).append(event)

            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                break
    return by_request


def _query_start(calls: list[dict]) -> str:
    stamps = []
    for call in calls:
        try:
            stamps.append(datetime.fromisoformat(call["timestamp"]))
        except (TypeError, ValueError, KeyError):
            continue
    earliest = min(stamps) if stamps else datetime.now(timezone.utc) - timedelta(days=1)
    return (earliest - _QUERY_MARGIN).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 리포트 ────────────────────────────────────────────────────────────────────


def _usd(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001")))


def build_report(calls: list[dict], billing: dict[str, list[dict]] | None) -> list[dict]:
    """billing=None 이면 조회 전(전부 pending). 청구 내역이 없는 호출은 pending 으로 두고 합계에 넣지 않는다."""
    jobs: dict[str, dict] = {}
    for call in calls:
        events = (billing or {}).get(call["requestId"], [])
        entry = dict(call)
        if events:
            cost = sum((Decimal(str(e.get("cost_total") or 0)) for e in events), Decimal(0))
            entry.update(billingStatus="billed", actualCostUsd=_usd(cost),
                         billingLines=len(events), _cost=cost)
        else:
            entry.update(billingStatus="pending", actualCostUsd=None, billingLines=0, _cost=None)

        job = jobs.setdefault(call["jobId"], {"jobId": call["jobId"], "images": {}})
        job["images"].setdefault(call["output"], []).append(entry)

    report = []
    for job in jobs.values():
        images, job_total, job_pending, job_calls = [], Decimal(0), 0, 0
        for output, entries in job["images"].items():
            billed = [e for e in entries if e["_cost"] is not None]
            pending = len(entries) - len(billed)
            subtotal = sum((e["_cost"] for e in billed), Decimal(0))
            images.append({
                "output": output,
                "models": sorted({e["endpoint"] for e in entries}),
                "callCount": len(entries),
                "pendingCount": pending,
                "billingStatus": "billed" if pending == 0 else "pending",
                # 확정 비용은 모든 호출이 청구 확인됐을 때만 채운다.
                "actualCostUsd": _usd(subtotal) if pending == 0 else None,
                "billedSoFarUsd": _usd(subtotal),
                "calls": [{k: v for k, v in e.items() if k != "_cost"} for e in entries],
            })
            job_total += subtotal
            job_pending += pending
            job_calls += len(entries)
        report.append({
            "jobId": job["jobId"],
            "callCount": job_calls,
            "pendingCount": job_pending,
            "billingStatus": "billed" if job_pending == 0 else "pending",
            "actualCostUsd": _usd(job_total) if job_pending == 0 else None,
            "billedSoFarUsd": _usd(job_total),
            "images": images,
        })
    return report


def measure(jsonl_path: str | Path, api_key: str | None) -> tuple[list[dict], str | None]:
    """(report, warning). 키가 없거나 권한이 부족하면 전부 pending 으로 돌려주고 사유를 warning 에 담는다."""
    calls = read_fal_calls(jsonl_path)
    if not calls:
        return [], None
    if not api_key:
        return build_report(calls, None), "FAL_BILLING_KEY 미설정 — 청구 내역을 조회하지 않았습니다."
    try:
        billing = fetch_billing_events([c["requestId"] for c in calls], api_key, _query_start(calls))
    except BillingAuthError as exc:
        return build_report(calls, None), (
            f"Billing API 인증 실패({exc}). BILLING 프리셋 키(권한: {REQUIRED_SCOPES})가 필요합니다."
        )
    return build_report(calls, billing), None


# ── CLI ───────────────────────────────────────────────────────────────────────


def _collect_logs(paths: list[str]) -> list[Path]:
    logs: list[Path] = []
    for raw in paths:
        p = Path(raw)
        logs.extend(sorted(p.rglob("pipeline.jsonl")) if p.is_dir() else [p])
    return logs


def _fmt(value: float | None) -> str:
    return "pending" if value is None else f"${value:.6f}"


def _print_text(log: Path, report: list[dict], warning: str | None) -> None:
    print(f"# {log}")
    if warning:
        print(f"  ! {warning}")
    if not report:
        print("  fal_request 기록 없음 (request_id 기록 기능 적용 이전 로그이거나 fal 호출 없음)")
    for job in report:
        print(f"  jobId={job['jobId']}")
        for image in job["images"]:
            for call in image["calls"]:
                print(f"    [{image['output']}] {call['endpoint']} request_id={call['requestId']} "
                      f"{call['durationSec']}s {call['billingStatus']} {_fmt(call['actualCostUsd'])}")
            print(f"    → 이미지 {image['output']}: 호출 {image['callCount']}회, "
                  f"실제 청구 {_fmt(image['actualCostUsd'])}")
        tail = f" (청구 미확인 {job['pendingCount']}건, 확인분 ${job['billedSoFarUsd']:.6f})" if job["pendingCount"] else ""
        print(f"  작업 합계: 호출 {job['callCount']}회, 실제 청구 {_fmt(job['actualCostUsd'])}{tail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fal-ai 실제 청구 비용 조회 (pipeline.jsonl 기반)")
    parser.add_argument("paths", nargs="+", help="pipeline.jsonl 파일 또는 jobId 출력 디렉터리")
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    parser.add_argument("--save", action="store_true", help="각 로그 옆에 fal_cost.json 저장")
    args = parser.parse_args(argv)

    api_key = os.environ.get("FAL_BILLING_KEY") or os.environ.get("FAL_KEY")
    results, auth_failed = [], False
    for log in _collect_logs(args.paths):
        report, warning = measure(log, api_key)
        auth_failed = auth_failed or bool(warning and "인증" in warning)
        results.append({"log": str(log), "warning": warning, "jobs": report})
        if args.save:
            (log.parent / "fal_cost.json").write_text(
                json.dumps({"warning": warning, "jobs": report}, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.json:
            _print_text(log, report, warning)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 2 if auth_failed else 0


if __name__ == "__main__":
    sys.exit(main())
