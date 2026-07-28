"""Clean-room boundary checker for worker/clean_pipeline/.

Scans every .py file under worker/clean_pipeline/ for forbidden strings
that indicate contamination from the legacy pipeline.

Exit 0 = PASS, Exit 1 = FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN: list[str] = [
    "worker.resizer",
    "unified_v2",
    "smart_fit",
    "focus_fill",
    "crop_fallback",
    "layout_fallback",
    "best_effort",
    "center_restore",
    "original_position_fallback",
]

_REPO_ROOT = Path(__file__).parent.parent
_TARGET_DIR = _REPO_ROOT / "worker" / "clean_pipeline"


def check_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, forbidden_term, line_content) for each violation."""
    violations: list[tuple[int, str, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        for term in FORBIDDEN:
            if term in line:
                violations.append((lineno, term, line.strip()))
    return violations


def main() -> int:
    files = sorted(_TARGET_DIR.rglob("*.py"))
    total = 0

    for f in files:
        for lineno, term, line in check_file(f):
            rel = f.relative_to(_REPO_ROOT)
            print(f"BOUNDARY VIOLATION [{term}]  {rel}:{lineno}  {line}")
            total += 1

    if total:
        print(f"\nboundary check FAILED — {total} violation(s)")
        return 1

    print("boundary check PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
