"""Canonical source metadata returned by the SOURCE_PREPARATION stage."""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class CanonicalSource:
    source_type: str    # "png" | "jpg" | "psd"
    width: int
    height: int
    mode: str           # always "RGBA"
    sha256: str
    canonical_path: str

    def to_dict(self) -> dict:
        return {
            "sourceType": self.source_type,
            "width": self.width,
            "height": self.height,
            "mode": self.mode,
            "sha256": self.sha256,
            "canonicalPath": self.canonical_path,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
