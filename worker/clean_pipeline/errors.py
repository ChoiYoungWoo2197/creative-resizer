"""Structured pipeline errors. Never raise plain string exceptions from pipeline code."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clean_pipeline.contracts import StageName


@dataclass
class PipelineError(Exception):
    code: str
    stage: StageName
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.stage.value}: {self.message}"
