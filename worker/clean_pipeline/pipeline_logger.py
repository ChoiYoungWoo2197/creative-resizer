"""JSONL structured logger. Every line flushed immediately.

Compatible with:
  tail -f pipeline.jsonl
  Get-Content pipeline.jsonl -Wait   (PowerShell)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO

_PIPELINE_VERSION = "clean_v1"


class PipelineLogger:
    def __init__(self, job_id: str, log_path: str | Path | None = None) -> None:
        self._job_id = job_id
        if log_path is not None:
            p = Path(log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._fh: IO[str] = open(p, "a", encoding="utf-8", buffering=1)
            self._owns_fh = True
        else:
            self._fh = sys.stdout
            self._owns_fh = False

    # ── Internal ──────────────────────────────────────────────────────────────

    def _emit(
        self,
        event: str,
        stage: str | None,
        message: str,
        metrics: dict[str, Any],
        failure_code: str | None,
        **extra: Any,
    ) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "jobId": self._job_id,
            "pipelineVersion": _PIPELINE_VERSION,
            "stage": stage,
            "event": event,
            "message": message,
            "metrics": metrics,
            "failureCode": failure_code,
        }
        record.update(extra)
        print(json.dumps(record, ensure_ascii=False), file=self._fh, flush=True)

        # 파일 모드일 때 docker logs에서 읽기 쉽게 stdout에도 출력
        if self._owns_fh:
            stage_label = f"[{stage}]" if stage else "[JOB]"
            if event == "stage_start":
                print(f"{stage_label}:START {message}", flush=True)
            elif event == "stage_pass":
                print(f"{stage_label}:PASS {message}", flush=True)
            elif event == "stage_fail":
                print(f"{stage_label}:FAIL [{failure_code}] {message}", flush=True)
            elif event == "artifact_written":
                artifact = extra.get("artifact", "")
                msg_lower = message.lower()
                art_lower = artifact.lower()
                # SAM2, bg_removal 등 주요 처리 이벤트만 출력 (파일 경로 artifact는 skip)
                if any(k in msg_lower or k in art_lower for k in ("sam2", "bg_removal", "pixel-scan", "title-clamp", "pixel mask")):
                    print(f"{stage_label}:ARTIFACT {message}", flush=True)

    # ── Stage events ──────────────────────────────────────────────────────────

    def stage_start(
        self, stage: str, message: str = "", metrics: dict[str, Any] | None = None
    ) -> None:
        self._emit("stage_start", stage, message, metrics or {}, None)

    def stage_pass(
        self, stage: str, message: str = "", metrics: dict[str, Any] | None = None
    ) -> None:
        self._emit("stage_pass", stage, message, metrics or {}, None)

    def stage_fail(
        self,
        stage: str,
        failure_code: str,
        message: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self._emit("stage_fail", stage, message, metrics or {}, failure_code)

    def artifact_written(
        self, stage: str, path: str, description: str = ""
    ) -> None:
        self._emit("artifact_written", stage, description, {}, None, artifact=path)

    # ── Job events ────────────────────────────────────────────────────────────

    def job_start(self, message: str = "", metrics: dict[str, Any] | None = None) -> None:
        self._emit("job_start", None, message, metrics or {}, None)

    def job_pass(self, message: str = "", metrics: dict[str, Any] | None = None) -> None:
        self._emit("job_pass", None, message, metrics or {}, None)

    def job_fail(
        self,
        failure_code: str,
        message: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self._emit("job_fail", None, message, metrics or {}, failure_code)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._owns_fh:
            self._fh.close()

    def __enter__(self) -> "PipelineLogger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
