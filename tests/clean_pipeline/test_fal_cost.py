"""fal-ai 실제 비용 측정 — 실제 fal 호출/과금 없이 fake fal_client 와 mock Billing API 로 검증한다."""
from __future__ import annotations

import io
import json
import sys
import types
import urllib.error

import pytest
from PIL import Image

from clean_pipeline import fal_cost
from clean_pipeline.pipeline_logger import PipelineLogger

SECRET = "billing-secret-key-should-never-print"


# ── fake fal_client ─────────────────────────────────────────────────────────────


def _png_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_fake_fal(monkeypatch, *, request_id="req-1", support_enqueue=True, fail=False):
    calls: list[dict] = []
    fake = types.ModuleType("fal_client")
    fake.InProgress = type("InProgress", (), {})
    fake.upload = lambda data, content_type=None: "https://fal.storage/input.jpg"

    def _result(arguments):
        sizes = arguments.get("target_sizes")
        w, h = map(int, sizes[0].split("x")) if sizes else (0, 0)
        return {"images": [{"url": "https://fal.media/out.png", "width": w, "height": h}]}

    if support_enqueue:
        def subscribe(application, arguments, *, with_logs=False, on_enqueue=None, on_queue_update=None):
            calls.append({"application": application, "arguments": arguments})
            if on_enqueue:
                on_enqueue(request_id)
            if fail:
                raise RuntimeError("fal failed after enqueue")
            return _result(arguments)
    else:
        def subscribe(application, arguments, *, with_logs=False, on_queue_update=None):
            calls.append({"application": application, "arguments": arguments})
            return _result(arguments)

    fake.subscribe = subscribe
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    monkeypatch.setenv("FAL_KEY", "dummy-fal-key")
    return calls


def _fal_events(log_path) -> list[dict]:
    return [r for r in map(json.loads, log_path.read_text(encoding="utf-8").splitlines())
            if r["event"] == "fal_request"]


# ── 생성 시점: request_id 기록 (기존 호출 파라미터 불변) ─────────────────────────────


def test_smart_resize_records_request_id_without_changing_arguments(tmp_path, monkeypatch):
    from clean_pipeline.typed import smart_resizer

    calls = _install_fake_fal(monkeypatch, request_id="req-sr-1")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(_png_bytes(300, 250)))
    src = tmp_path / "bg.png"
    Image.new("RGB", (1200, 628)).save(src)
    log = tmp_path / "pipeline.jsonl"

    with PipelineLogger("JOB-1", log) as logger:
        sr, result = smart_resizer.resize(str(src), 300, 250, str(tmp_path), "JOB-1", logger)

    assert result["is_smart_resized"] is True
    assert result["fal_meta"]["requestId"] == "req-sr-1"
    assert list(calls[0]["arguments"].keys()) == [
        "prompt", "image_url", "resolution", "target_sizes", "output_format",
        "safety_tolerance", "num_images_per_size",
    ]
    assert calls[0]["arguments"]["target_sizes"] == ["300x250"]
    events = _fal_events(log)
    assert len(events) == 1
    assert events[0]["falEndpoint"] == "fal-ai/smart-resize"
    assert events[0]["falRequestId"] == "req-sr-1"
    assert events[0]["metrics"]["targetWidth"] == 300 and events[0]["metrics"]["callStatus"] == "ok"


def test_flux_outpaint_records_request_id(tmp_path, monkeypatch):
    from clean_pipeline.typed import flux_outpainter

    _install_fake_fal(monkeypatch, request_id="req-flux-1")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(_png_bytes(1250, 560)))
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1080)).save(bg)
    log = tmp_path / "pipeline.jsonl"

    with PipelineLogger("JOB-2", log) as logger:
        sr, result = flux_outpainter.outpaint(str(bg), 1250, 560, 50, 240, 35, 240,
                                              str(tmp_path), "JOB-2", logger)

    assert result["is_smart_resized"] is True
    events = _fal_events(log)
    assert [e["falRequestId"] for e in events] == ["req-flux-1"]
    assert events[0]["falEndpoint"] == "fal-ai/flux-2-pro/outpaint"


def test_failed_call_after_enqueue_is_still_recorded_and_fallback_kept(tmp_path, monkeypatch):
    from clean_pipeline.typed import smart_resizer

    _install_fake_fal(monkeypatch, request_id="req-fail", fail=True)
    src = tmp_path / "bg.png"
    Image.new("RGB", (1200, 628)).save(src)
    log = tmp_path / "pipeline.jsonl"

    with PipelineLogger("JOB-3", log) as logger:
        sr, result = smart_resizer.resize(str(src), 300, 250, str(tmp_path), "JOB-3", logger)

    assert result["is_smart_resized"] is False  # 기존 PIL fallback 유지
    events = _fal_events(log)
    assert events[0]["falRequestId"] == "req-fail" and events[0]["metrics"]["callStatus"] == "error"


def test_old_fal_client_without_on_enqueue_still_works(tmp_path, monkeypatch):
    from clean_pipeline.typed import smart_resizer

    _install_fake_fal(monkeypatch, support_enqueue=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(_png_bytes(300, 250)))
    src = tmp_path / "bg.png"
    Image.new("RGB", (1200, 628)).save(src)
    log = tmp_path / "pipeline.jsonl"

    with PipelineLogger("JOB-4", log) as logger:
        sr, result = smart_resizer.resize(str(src), 300, 250, str(tmp_path), "JOB-4", logger)

    assert result["is_smart_resized"] is True
    assert _fal_events(log) == []


# ── 조회 시점: 로그 + Billing API 집계 ───────────────────────────────────────────


def _write_log(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _fal_record(rid, endpoint, w, h, ts="2026-10-12T01:00:00+00:00"):
    return {"timestamp": ts, "jobId": "JOB-9", "event": "fal_request", "stage": "BG_EXTRACTION",
            "falEndpoint": endpoint, "falRequestId": rid,
            "metrics": {"targetWidth": w, "targetHeight": h, "durationSec": 12.3, "callStatus": "ok"}}


def test_read_fal_calls_dedupes_request_ids_and_ignores_other_events(tmp_path):
    log = tmp_path / "pipeline.jsonl"
    _write_log(log, [
        {"event": "stage_start", "jobId": "JOB-9"},
        _fal_record("r1", "fal-ai/smart-resize", 300, 250),
        _fal_record("r1", "fal-ai/smart-resize", 300, 250),
        _fal_record("r2", "fal-ai/flux-2-pro/outpaint", 1250, 560),
    ])
    assert [c["requestId"] for c in fal_cost.read_fal_calls(log)] == ["r1", "r2"]


def test_billing_sums_lines_per_request_dedupes_and_marks_pending(tmp_path, monkeypatch):
    log = tmp_path / "pipeline.jsonl"
    _write_log(log, [
        _fal_record("r1", "fal-ai/smart-resize", 300, 250),
        _fal_record("r2", "fal-ai/smart-resize", 728, 90),
        _fal_record("r3", "fal-ai/flux-2-pro/outpaint", 728, 90),
    ])
    line_image = {"request_id": "r1", "timestamp": "t1", "unit": "image", "quantity": 1, "cost_total": 0.15}
    line_vision = {"request_id": "r1", "timestamp": "t1", "unit": "vision", "quantity": 1, "cost_total": 0.05}
    line_r2 = {"request_id": "r2", "timestamp": "t2", "unit": "image", "quantity": 1, "cost_total": 0.15}
    pages = [
        {"billing_events": [line_image, line_vision], "has_more": True, "next_cursor": "c2"},
        {"billing_events": [line_vision, line_r2], "has_more": False, "next_cursor": None},  # 중복 라인 포함
    ]
    seen_urls: list[str] = []

    def fake_get(url, headers):
        seen_urls.append(url)
        assert headers["Authorization"] == f"Key {SECRET}"
        return pages[len(seen_urls) - 1]

    monkeypatch.setattr(fal_cost, "_http_get", fake_get)
    report, warning = fal_cost.measure(log, SECRET)

    assert warning is None
    assert "request_id=r1" in seen_urls[0] and "start=2026-10-12T00%3A00%3A00Z" in seen_urls[0]
    assert "cursor=c2" in seen_urls[1]
    job = report[0]
    images = {i["output"]: i for i in job["images"]}
    assert images["300x250"]["actualCostUsd"] == 0.2  # 0.15 + 0.05, 중복 vision 라인 1회만
    assert images["300x250"]["billingStatus"] == "billed"
    assert images["728x90"]["callCount"] == 2
    assert images["728x90"]["billingStatus"] == "pending"
    assert images["728x90"]["actualCostUsd"] is None  # 청구 미확인분이 있으면 확정 비용 없음
    assert images["728x90"]["billedSoFarUsd"] == 0.15
    assert job["billingStatus"] == "pending" and job["actualCostUsd"] is None
    assert job["billedSoFarUsd"] == 0.35 and job["callCount"] == 3 and job["pendingCount"] == 1


def test_auth_failure_reports_pending_with_guidance_and_hides_key(tmp_path, monkeypatch, capsys):
    log = tmp_path / "pipeline.jsonl"
    _write_log(log, [_fal_record("r1", "fal-ai/smart-resize", 300, 250)])

    def forbidden(url, headers):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(fal_cost, "_http_get", forbidden)
    monkeypatch.setenv("FAL_BILLING_KEY", SECRET)

    exit_code = fal_cost.main([str(tmp_path), "--save"])

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "billing:usage:read" in out and "pending" in out
    assert SECRET not in out
    saved = json.loads((tmp_path / "fal_cost.json").read_text(encoding="utf-8"))
    assert saved["jobs"][0]["images"][0]["calls"][0]["billingStatus"] == "pending"
    assert SECRET not in json.dumps(saved)


def test_without_key_everything_is_pending(tmp_path):
    log = tmp_path / "pipeline.jsonl"
    _write_log(log, [_fal_record("r1", "fal-ai/smart-resize", 300, 250)])
    report, warning = fal_cost.measure(log, None)
    assert "FAL_BILLING_KEY" in warning
    assert report[0]["images"][0]["billingStatus"] == "pending"
