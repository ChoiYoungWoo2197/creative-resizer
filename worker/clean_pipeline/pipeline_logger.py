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
