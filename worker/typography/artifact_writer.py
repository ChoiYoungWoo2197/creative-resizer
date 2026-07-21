"""Stage 20: Artifact writer for typography pipeline debug output."""
from __future__ import annotations
import json
import os

from PIL import Image


def write_artifacts(
    output_dir: str,
    source_image: Image.Image | None,
    result_image: Image.Image | None,
    classified: list[dict],
    slots,
    result_meta: dict,
    job_id: str = "",
    artifact_level: str = "minimal",
) -> dict:
    """Write debug artifacts for Stage 20.

    artifact_level: 'none' | 'minimal' | 'full'
    Returns dict of written paths.
    """
    if artifact_level == "none":
        return {}

    art_dir = os.path.join(output_dir, "stage20_artifacts", job_id or "default")
    os.makedirs(art_dir, exist_ok=True)
    paths: dict = {}

    # Always write: metadata JSON
    meta_path = os.path.join(art_dir, "typography_meta.json")
    try:
        safe_meta = _safe_serializable(result_meta)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(safe_meta, f, ensure_ascii=False, indent=2)
        paths["meta"] = meta_path
    except Exception as e:
        paths["meta_error"] = str(e)

    if artifact_level == "minimal":
        return paths

    # Full: also write source and result thumbnails + role map
    if source_image:
        try:
            thumb = source_image.copy()
            thumb.thumbnail((400, 400))
            sp = os.path.join(art_dir, "source_thumb.jpg")
            thumb.convert("RGB").save(sp)
            paths["source_thumb"] = sp
        except Exception:
            pass

    if result_image:
        try:
            thumb = result_image.copy()
            thumb.thumbnail((400, 400))
            rp = os.path.join(art_dir, "result_thumb.jpg")
            thumb.convert("RGB").save(rp)
            paths["result_thumb"] = rp
        except Exception:
            pass

    # Role assignment JSON
    role_map = [{"id": l.get("id"), "name": l.get("name"), "role": l.get("role"),
                 "roleSource": l.get("roleSource"), "dedupSkip": l.get("dedupSkip", False),
                 "textContent": (l.get("textContent") or "")[:60]}
                for l in classified]
    try:
        rp = os.path.join(art_dir, "role_map.json")
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(role_map, f, ensure_ascii=False, indent=2)
        paths["role_map"] = rp
    except Exception:
        pass

    return paths


def _safe_serializable(obj):
    """Recursively make an object JSON-serializable."""
    if isinstance(obj, dict):
        return {k: _safe_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serializable(i) for i in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
