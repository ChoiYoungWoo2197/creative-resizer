"""Parse raw AI response dict → SceneManifest. No polygon auto-generation."""
from __future__ import annotations

from clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject

_MODEL = "o3"


def build(
    raw: dict,
    job_id: str,
    source_sha256: str,
    image_width: int,
    image_height: int,
) -> tuple[SceneManifest | None, str]:
    """Return (manifest, error_reason). error_reason is '' on success."""
    try:
        objects_raw = raw.get("objects", [])
        if not isinstance(objects_raw, list):
            return None, f"'objects' field is not a list: {type(objects_raw)}"

        objects: list[SceneObject] = []
        for i, obj in enumerate(objects_raw):
            if not isinstance(obj, dict):
                return None, f"Object at index {i} is not a dict"

            bbox_raw = obj.get("bbox") or {}
            bbox = BBox(
                x=int(bbox_raw.get("x", 0)),
                y=int(bbox_raw.get("y", 0)),
                width=int(bbox_raw.get("width", 0)),
                height=int(bbox_raw.get("height", 0)),
            )

            polygon = obj.get("polygon") or []
            if not isinstance(polygon, list):
                polygon = []

            objects.append(SceneObject(
                id=str(obj.get("id", f"obj_{i}")),
                role=str(obj.get("role", "decorative_ad")),
                required=bool(obj.get("required", False)),
                movable=bool(obj.get("movable", True)),
                removable_from_scene=bool(obj.get("removableFromScene", True)),
                bbox=bbox,
                polygon=polygon,
                confidence=float(obj.get("confidence", 0.5)),
                group_id=obj.get("groupId") or None,
                z_index=int(obj.get("zIndex", i)),
                text_content=str(obj.get("textContent") or ""),
                description=str(obj.get("description") or ""),
            ))

        manifest = SceneManifest(
            job_id=job_id,
            source_sha256=source_sha256,
            image_width=image_width,
            image_height=image_height,
            model=_MODEL,
            objects=objects,
        )
        return manifest, ""

    except Exception as exc:
        return None, f"Manifest build error: {exc}"
