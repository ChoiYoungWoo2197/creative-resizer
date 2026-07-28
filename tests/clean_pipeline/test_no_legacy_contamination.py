"""Clean isolation enforcement tests.

Verifies that all modules under worker/clean_pipeline/ import ONLY from:
  - clean_pipeline.* (internal)
  - Python stdlib (os, sys, json, dataclasses, pathlib, enum, typing, abc, io,
                   base64, hashlib, logging, struct, math, re, time, threading,
                   functools, itertools, contextlib, collections, copy, shutil,
                   tempfile, uuid, traceback, warnings, textwrap, decimal, etc.)
  - PIL / Pillow
  - numpy
  - cv2 (opencv-python)
  - openai
  - dataclasses_json (if used)

Banned imports (legacy worker modules):
  resizer, psd_analyzer, layer_object_matcher, background_plate_builder,
  foreground_compositor, scene_cleanup, typography, verdict, virtual_foreground,
  layout (worker-level, i.e. not clean_pipeline.layout),
  unified_v2, background, foreground, segmentation_ai, object_source_resolver,
  compositor (worker-level), engine, pipeline_sequence, preflight_gate,
  retry_invariant, subject_preserving_transform, avoidance_mask, mask_conflict,
  group_rgba_builder, evaluate_extended_visual, pixel_restorer, sha_chain,
  canonical_source_v2, semantic_manifest, visual_evaluator, scene_cleanup,
  repair, narrow_template, dedup, cta_merge, competitor_templates, score_product,
  bg_naturalness, external_segmentation_client, object_map_applicator,
  ProviderFactory, segmentation_poc, inpaint_outpaint_poc, safe_zone (worker-level),
  debug_overlay, safe_zone_checker, layout_repair, stage_*

How it works:
  AST-parses every .py file under worker/clean_pipeline/ and collects all import
  top-level module names. Each name is checked against the allowed set.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# ── Root of the clean_pipeline source tree ────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent
_CLEAN_PIPELINE_ROOT = _REPO_ROOT / "worker" / "clean_pipeline"

assert _CLEAN_PIPELINE_ROOT.is_dir(), (
    f"clean_pipeline source not found: {_CLEAN_PIPELINE_ROOT}"
)


# ── Allowed top-level module names ────────────────────────────────────────────
# Any module whose top-level name is in this set is clean.

_STDLIB_TOP_LEVEL = frozenset({
    # builtins & core
    "__future__", "builtins", "_thread", "abc", "argparse", "ast", "base64", "binascii",
    "bisect", "calendar", "cmath", "codecs", "collections", "contextlib", "copy",
    "copyreg", "csv", "dataclasses", "datetime", "decimal", "dis", "email",
    "enum", "errno", "fileinput", "fnmatch", "fractions", "functools",
    "gc", "glob", "gzip", "hashlib", "heapq", "html", "http", "imaplib",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "linecache", "locale", "logging", "math", "mimetypes",
    "multiprocessing", "numbers", "operator", "os", "pathlib", "pickle",
    "platform", "pprint", "queue", "random", "re", "shutil", "signal",
    "socket", "sqlite3", "ssl", "stat", "statistics", "string", "struct",
    "subprocess", "sys", "tempfile", "textwrap", "threading", "time",
    "timeit", "traceback", "types", "typing", "unicodedata", "unittest",
    "urllib", "uuid", "warnings", "weakref", "xml", "zipfile", "zlib",
    # less common but still stdlib
    "array", "atexit", "cProfile", "cmd", "code", "colorsys", "compileall",
    "concurrent", "configparser", "contextlib", "contextvars", "copy",
    "difflib", "doctest", "email", "faulthandler", "formatter", "getopt",
    "getpass", "gettext", "grp", "hmac", "html", "http", "imghdr",
    "importlib", "io", "itertools", "keyword", "lib2to3", "linecache",
    "lzma", "mailbox", "marshal", "mmap", "modulefinder", "msvcrt",
    "netrc", "nis", "nntplib", "optparse", "ossaudiodev", "parser",
    "pkgutil", "plistlib", "poplib", "posix", "posixpath", "profile",
    "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri",
    "readline", "reprlib", "resource", "rlcompleter", "runpy", "sched",
    "select", "selectors", "shelve", "smtpd", "smtplib", "sndhdr",
    "socketserver", "sre_compile", "sre_constants", "sre_parse",
    "stringprep", "sunau", "symtable", "sysconfig", "syslog", "tabnanny",
    "tarfile", "telnetlib", "termios", "test", "textwrap", "tkinter",
    "token", "tokenize", "trace", "tracemalloc", "tty", "turtle",
    "turtledemo", "uu", "venv", "wave", "winreg", "winsound", "wsgiref",
    "xdrlib", "xmlrpc", "zipapp", "zipimport",
    # type-checking helpers
    "typing_extensions",
})

_ALLOWED_THIRD_PARTY = frozenset({
    "clean_pipeline",   # internal — any sub-path is fine
    "PIL",              # Pillow
    "numpy",            # numpy
    "np",               # numpy alias (rare but possible)
    "cv2",              # OpenCV
    "openai",           # OpenAI SDK
    "psd_tools",        # PSD file parsing (used in canonical_source for PSD input)
    "dataclasses_json", # sometimes used for JSON serialization
    "pydantic",         # sometimes used for models
})

_ALLOWED = _STDLIB_TOP_LEVEL | _ALLOWED_THIRD_PARTY


# ── Banned top-level module names ─────────────────────────────────────────────
# Any import whose top-level name starts with one of these strings is forbidden.

_BANNED_PREFIXES = (
    "resizer",
    "psd_analyzer",
    "layer_object_matcher",
    "background_plate_builder",
    "background_plate",
    "foreground_compositor",
    "foreground",
    "background",
    "scene_cleanup",
    "typography",
    "verdict",
    "virtual_foreground",
    "unified_v2",
    "segmentation_ai",
    "segmentation_poc",
    "inpaint_outpaint_poc",
    "object_source_resolver",
    "object_map_applicator",
    "external_segmentation_client",
    "debug_overlay",
    "safe_zone_checker",
    "layout_repair",
    "repair",
    "narrow_template",
    "competitor_templates",
    "score_product",
    "bg_naturalness",
    "ProviderFactory",
    "pipeline_sequence",
    "preflight_gate",
    "retry_invariant",
    "subject_preserving_transform",
    "avoidance_mask",
    "mask_conflict",
    "group_rgba_builder",
    "evaluate_extended_visual",
    "pixel_restorer",
    "sha_chain",
    "canonical_source_v2",
    "semantic_manifest",
    "visual_evaluator",
    "stage_",     # any stage_N module
)


# ── AST helpers ───────────────────────────────────────────────────────────────


def _top_level(name: str) -> str:
    """Return the top-level module name: 'os.path' → 'os', 'PIL.Image' → 'PIL'."""
    return name.split(".")[0]


def _collect_imports(source: str) -> list[str]:
    """Parse source and return list of top-level names imported."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(_top_level(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(_top_level(node.module))
            # relative imports (level > 0) with no module → stay within package
    return names


def _is_banned(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _BANNED_PREFIXES)


def _is_allowed(name: str) -> bool:
    return name in _ALLOWED or name.startswith("clean_pipeline")


# ── Collect all .py files ─────────────────────────────────────────────────────

def _all_py_files() -> list[Path]:
    return sorted(_CLEAN_PIPELINE_ROOT.rglob("*.py"))


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_clean_pipeline_directory_exists():
    """Sanity: the directory we are scanning actually exists."""
    assert _CLEAN_PIPELINE_ROOT.is_dir()
    py_files = _all_py_files()
    assert len(py_files) > 0, "Expected at least one .py file in clean_pipeline/"


def test_no_banned_imports_in_any_clean_pipeline_module():
    """No clean_pipeline module must import a banned legacy module."""
    violations: list[str] = []

    for py_file in _all_py_files():
        source = py_file.read_text(encoding="utf-8", errors="replace")
        imports = _collect_imports(source)
        for name in imports:
            if _is_banned(name):
                rel = py_file.relative_to(_REPO_ROOT)
                violations.append(f"{rel}: banned import '{name}'")

    if violations:
        report = "\n  ".join(violations)
        pytest.fail(
            f"clean_pipeline isolation violated — {len(violations)} banned import(s):\n  {report}"
        )


def test_no_unexpected_worker_level_imports():
    """No clean_pipeline module imports from top-level worker modules not in the allow-list."""
    # These are modules that live at worker/ level and are NOT allowed in clean_pipeline/
    # We detect them by checking: any import that is not allowed and not stdlib
    # that isn't from a known third party.
    _KNOWN_THIRD_PARTY = frozenset({
        "PIL", "numpy", "cv2", "openai", "psd_tools",
        "dataclasses_json", "pydantic", "np", "typing_extensions",
    })
    suspicious: list[str] = []

    for py_file in _all_py_files():
        source = py_file.read_text(encoding="utf-8", errors="replace")
        imports = _collect_imports(source)
        for name in imports:
            if name in _STDLIB_TOP_LEVEL:
                continue
            if name in _KNOWN_THIRD_PARTY:
                continue
            if name.startswith("clean_pipeline"):
                continue
            if not name:
                continue
            rel = py_file.relative_to(_REPO_ROOT)
            suspicious.append(f"{rel}: unexpected import '{name}'")

    if suspicious:
        report = "\n  ".join(suspicious)
        pytest.fail(
            f"clean_pipeline imports unknown module(s) — verify these are allowed:\n  {report}"
        )


@pytest.mark.parametrize("py_file", [str(f.relative_to(_REPO_ROOT)) for f in _all_py_files()])
def test_individual_file_has_no_banned_imports(py_file):
    """Per-file parametrized check — surfaces the exact file name on failure."""
    full_path = _REPO_ROOT / py_file
    source = full_path.read_text(encoding="utf-8", errors="replace")
    imports = _collect_imports(source)

    banned = [name for name in imports if _is_banned(name)]
    assert banned == [], (
        f"{py_file} imports banned legacy module(s): {banned}\n"
        "clean_pipeline must not depend on any top-level worker legacy module."
    )
