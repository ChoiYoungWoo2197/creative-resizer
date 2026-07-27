"""Repo-root conftest: add worker/ to sys.path so clean_pipeline imports resolve."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "worker"))
