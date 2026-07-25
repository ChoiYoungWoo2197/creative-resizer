"""Unified Manifest Adapter: GPT unified-ad-object-v1 JSON → fg_layers.

Pre-loop pass (job-level):
  product / logo / icon  → polygon extraction → RGBA PNG (transparent_image)
  title / body_text / cta etc. → metadata only, image=None  (text_render)
  decorative_shape / badge / label → metadata only, image=None  (shape_render)

Per-spec pass (in resizer.py spec loop):
  text_render + shape_render → rendered at target bbox dimensions
  transparent_image          → scaled by scale_virtual_fg_layers()
"""
from __future__ import annotations

from PIL import Image

_ANALYSIS_VERSION = "unified-ad-object-v1"

_ROLE_TO_RENDERMODE: dict[str, str] = {
    "product":          "transparent_image",
    "logo":             "transparent_image",
    "icon":             "transparent_image",
    "title":            "text_render",
    "subtitle":         "text_render",
    "body_text":        "text_render",
    "cta":              "text_render",
    "headline":         "text_render",
    "decorative_shape": "shape_render",
    "badge":            "shape_render",
    "label":            "shape_render",
    "decorative":       "shape_render",
}

_REQUIRED_ROLES = frozenset({"product", "title", "cta"})


def adapt_gpt_objects_to_fg_layers(
    gpt_objects: list[dict],
    source_image: Image.Image,
    job_id: str = "",
) -> list[dict]:
    """Convert GPT JSON object list to fg_layers (pre-loop, job-level).

    For transparent_image roles: polygon extraction → stores PIL RGBA in layer["image"].
    For text_render / shape_render: stores metadata, layer["image"] = None.

    Emits:
      [IMAGE_OBJECT_EXTRACT] per product/logo/icon
      [UNIFIED_MANIFEST]
    """
    from foreground.polygon_extractor import extract_polygon_mask

    fg_layers: list[dict] = []
    image_count = text_count = shape_count = 0
    required_roles: list[str] = []
    accepted_roles: list[str] = []
    rejected_roles: list[str] = []
    rejected_reasons: dict[str, str] = {}

    for i, obj in enumerate(gpt_objects):
        role = (obj.get("role") or "").strip().lower()
        render_mode = _ROLE_TO_RENDERMODE.get(role, "transparent_image")
        obj_id = str(obj.get("objectId") or f"gpt_{role}_{i:04d}")
        required = bool(obj.get("required", False)) or role in _REQUIRED_ROLES
        confidence = float(obj.get("confidence", 0.8))
        bbox = dict(obj.get("bbox") or {})
        z_index = int(obj.get("zIndex", i))
        group_id = str(obj.get("groupId") or "")

        if required:
            required_roles.append(role)

        layer: dict = {
            "objectId": obj_id,
            "role": role,
            "renderMode": render_mode,
            "confidence": confidence,
            "bbox": bbox,
            "sourceBBox": dict(bbox),
            "zIndex": z_index,
            "groupId": group_id,
            "required": required,
            "recompose": True,
            "isVirtual": True,
            "semanticEvidence": ["gpt_unified_analysis"],
            "depth": z_index,
            "layerId": "",
            "maskRef": "",
            "foregroundImageRef": "",
            "maskPixelCount": 0,
            "image": None,
            "name": f"unified_{role}_{obj_id}",
            "compositedCount": 0,
            # text fields
            "text": str(obj.get("text") or ""),
            "fontFamilyGuess": obj.get("fontFamilyGuess"),
            "fontWeight": obj.get("fontWeight"),
            "fontSize": int(obj.get("fontSize") or 24),
            "textAlign": obj.get("textAlign"),
            "segments": obj.get("segments") or [],
            "textColor": _first_segment_color(obj.get("segments")),
            # shape fields
            "fillColor": obj.get("fillColor"),
            "opacity": float(obj.get("opacity", 1.0)),
            "borderRadius": int(obj.get("borderRadius") or 0),
        }

        if render_mode == "transparent_image":
            polygon = obj.get("polygon") or _first_polygon(obj.get("polygons"))
            rgba_img, metrics = (
                extract_polygon_mask(
                    source_image=source_image,
                    polygon=polygon,
                    bbox=bbox,
                    holes=obj.get("holes") or [],
                    edge_feather_px=int(obj.get("edgeFeatherPx") or 2),
                    crop_padding=int(obj.get("cropPadding") or 4),
                )
                if polygon
                else (None, {"error": "NO_POLYGON"})
            )
            passed = rgba_img is not None and metrics.get("maskPixelCount", 0) > 0
            print(
                f"[IMAGE_OBJECT_EXTRACT] jobId={job_id} objectId={obj_id}"
                f" role={role}"
                f" polygonCount={1 if polygon else 0}"
                f" maskPixelCount={metrics.get('maskPixelCount', 0)}"
                f" maskRef={metrics.get('maskRef', '')[:16]}"
                f" foregroundImageRef={metrics.get('foregroundImageRef', '')[:16]}"
                f" passed={passed}"
                f" rejectReason={metrics.get('error', '')}",
                flush=True,
            )
            if not passed:
                if required:
                    rejected_roles.append(role)
                    rejected_reasons[role] = metrics.get("error", "EXTRACT_FAILED")
                continue
            layer["image"] = rgba_img
            layer["maskRef"] = metrics.get("maskRef", "")
            layer["foregroundImageRef"] = metrics.get("foregroundImageRef", "")
            layer["maskPixelCount"] = metrics.get("maskPixelCount", 0)
            cr = metrics.get("cropRect")
            if cr:
                layer["bbox"] = {
                    "x": bbox.get("x", 0),
                    "y": bbox.get("y", 0),
                    "width": cr["width"],
                    "height": cr["height"],
                }
            accepted_roles.append(role)
            image_count += 1

        elif render_mode == "text_render":
            # Sentinel maskRef so MASK_CONTAMINATION_FILTER does not reject as NO_MASK_REF.
            layer["maskRef"] = "text_render_pending"
            accepted_roles.append(role)
            text_count += 1

        elif render_mode == "shape_render":
            # Sentinel maskRef so MASK_CONTAMINATION_FILTER does not reject as NO_MASK_REF.
            layer["maskRef"] = "shape_render_pending"
            accepted_roles.append(role)
            shape_count += 1

        fg_layers.append(layer)

    print(
        f"[UNIFIED_MANIFEST] jobId={job_id}"
        f" manifestObjectCount={len(fg_layers)}"
        f" imageObjectCount={image_count}"
        f" textObjectCount={text_count}"
        f" shapeObjectCount={shape_count}"
        f" requiredRoles={required_roles}"
        f" acceptedRoles={accepted_roles}"
        f" rejectedRoles={rejected_roles}"
        f" rejectedReasons={rejected_reasons}",
        flush=True,
    )
    return fg_layers


# ── Helpers ───────────────────────────────────────────────────────────────────

def _first_polygon(polygons: object) -> list | None:
    if isinstance(polygons, list) and polygons:
        return polygons[0]
    return None


def _first_segment_color(segments: object) -> str | None:
    if isinstance(segments, list) and segments:
        seg = segments[0]
        if isinstance(seg, dict):
            return seg.get("color") or seg.get("fillColor")
    return None
