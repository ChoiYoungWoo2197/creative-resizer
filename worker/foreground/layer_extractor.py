"""Stage 21: Extract foreground RGBA layers from classified PSD layers.

Each layer with a foreground role is composited via psd-tools _layer_obj and
scaled to fit the target canvas. Positions are derived from the original bbox
(canvas-relative), then proportionally scaled to (target_w, target_h).
"""
from __future__ import annotations

from PIL import Image

# Roles included in foreground compositing (background excluded)
FOREGROUND_ROLES = frozenset({
    "human_subject", "product", "main_image",
    "logo", "title", "body_text", "cta", "badge", "decorative",
})


def extract_foreground_layers(
    psd_layers: list[dict],
    canvas_w: int,
    canvas_h: int,
    target_w: int,
    target_h: int,
) -> list[dict]:
    """Extract RGBA images for each foreground layer, scaled to target canvas.

    Args:
        psd_layers: classify_layers() output — each item must have _layer_obj.
        canvas_w/h: original PSD canvas dimensions (source coordinate space).
        target_w/h: target output canvas dimensions.

    Returns:
        List of dicts (ordered as in psd_layers, background excluded):
        {
          "role": str,
          "name": str,
          "image": PIL.Image (RGBA, sw x sh),
          "bbox": {"x": int, "y": int, "width": int, "height": int},  # target coords
          "depth": int,
          "layerId": str,
        }
        Layers whose composite() fails or have zero size are silently dropped.
    """
    if not psd_layers or canvas_w <= 0 or canvas_h <= 0:
        return []

    scale_x = target_w / canvas_w
    scale_y = target_h / canvas_h
    # Uniform scale preserves aspect ratios of foreground objects.
    # Positions use individual scale_x/scale_y (proportional placement),
    # but dimensions always use the smaller axis scale to avoid distortion.
    scale_uniform = min(scale_x, scale_y)

    result = []
    for layer in psd_layers:
        role = layer.get("role", "unknown")
        if role not in FOREGROUND_ROLES:
            continue

        lobj = layer.get("_layer_obj")
        if lobj is None:
            continue

        bbox = layer.get("bbox", {})
        ox = int(bbox.get("x", 0))
        oy = int(bbox.get("y", 0))
        ow = int(bbox.get("width", 0))
        oh = int(bbox.get("height", 0))
        if ow <= 0 or oh <= 0:
            continue

        try:
            limg = lobj.composite()
            if limg is None or limg.width <= 0 or limg.height <= 0:
                continue
            limg = limg.convert("RGBA")
        except Exception as e:
            print(f"[FG_EXTRACT] skip layer={layer.get('name')!r} role={role}: {e}")
            continue

        # Positions: proportional to target canvas (preserves relative layout)
        sx = round(ox * scale_x)
        sy = round(oy * scale_y)
        # Dimensions: uniform scale to preserve aspect ratio (no distortion)
        sw = max(1, round(ow * scale_uniform))
        sh = max(1, round(oh * scale_uniform))

        if limg.width != sw or limg.height != sh:
            limg = limg.resize((sw, sh), Image.LANCZOS)

        src_aspect = ow / oh if oh > 0 else 1.0
        dst_aspect = sw / sh if sh > 0 else 1.0
        if abs(src_aspect - dst_aspect) > 0.005:
            print(
                f"[FG_EXTRACT] NON_UNIFORM_FOREGROUND_SCALE"
                f" role={role} name={layer.get('name')!r}"
                f" srcAspect={src_aspect:.3f} dstAspect={dst_aspect:.3f}",
                flush=True,
            )

        result.append({
            "role":    role,
            "name":    layer.get("name", ""),
            "image":   limg,
            "bbox":    {"x": sx, "y": sy, "width": sw, "height": sh},
            "depth":   layer.get("depth", 0),
            "layerId": layer.get("id", ""),
        })
        print(
            f"[FG_EXTRACT] role={role} name={layer.get('name')!r}"
            f" src={ow}x{oh} -> {sw}x{sh} @ ({sx},{sy})"
            f" scale_uniform={scale_uniform:.4f}",
            flush=True,
        )

    return result
