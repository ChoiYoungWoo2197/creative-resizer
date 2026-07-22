"""Stage 21: Deterministic foreground compositor.

Stacks foreground layers extracted from the original PSD onto the AI background
plate produced by SourceFaithfulRepair.  Layer order follows a fixed z-order
table (role-based) rather than relying on PSD stacking order, which is less
predictable after role-based filtering.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from PIL import Image

# Role → z-order index (lower = composited first / behind)
_ROLE_ZORDER: dict[str, int] = {
    "background":    0,
    "human_subject": 1,   # person behind product (typical ad layout)
    "product":       2,
    "main_image":    3,   # generic visual element
    "decorative":    4,
    "badge":         5,
    "logo":          6,
    "body_text":     7,
    "title":         8,
    "cta":           9,   # CTA always on top
}


@dataclass
class ForegroundCompositeResult:
    success: bool = False
    composite_image: object = None  # PIL Image (RGB) or None

    placed_roles: list[str] = field(default_factory=list)
    skipped_roles: list[str] = field(default_factory=list)

    # Convenience flags for renderProvenance
    product_placed: bool = False
    logo_placed: bool = False
    headline_placed: bool = False
    body_text_placed: bool = False
    cta_placed: bool = False
    human_subject_preserved: bool = False

    layer_count: int = 0  # total layers attempted


def composite_foreground(
    background: "Image.Image",
    foreground_layers: list[dict],
) -> ForegroundCompositeResult:
    """Stack foreground layers onto AI background in role z-order.

    Args:
        background: AI-generated background plate (RGB or RGBA).
        foreground_layers: list from extract_foreground_layers().

    Returns:
        ForegroundCompositeResult — composite_image is RGB when success=True.
        On error or empty foreground_layers, success=True with background unchanged.
    """
    res = ForegroundCompositeResult(layer_count=len(foreground_layers))

    if background is None:
        return res

    canvas = background.convert("RGBA")
    target_w, target_h = canvas.size

    sorted_layers = sorted(
        foreground_layers,
        key=lambda l: (_ROLE_ZORDER.get(l.get("role", "unknown"), 5), l.get("depth", 0)),
    )

    placed: set[str] = set()
    skipped: set[str] = set()

    for layer in sorted_layers:
        role = layer.get("role", "unknown")
        limg: "Image.Image | None" = layer.get("image")
        bbox = layer.get("bbox", {})

        if limg is None:
            skipped.add(role)
            continue

        sx = int(bbox.get("x", 0))
        sy = int(bbox.get("y", 0))
        sw = int(bbox.get("width", limg.width))
        sh = int(bbox.get("height", limg.height))

        # Skip if completely outside canvas
        if sx >= target_w or sy >= target_h or sx + sw <= 0 or sy + sh <= 0:
            print(
                f"[FG_COMPOSITE] skip out-of-bounds role={role}"
                f" bbox=({sx},{sy},{sw},{sh}) canvas={target_w}x{target_h}",
            )
            skipped.add(role)
            continue

        if limg.size != (sw, sh):
            limg = limg.resize((sw, sh), Image.LANCZOS)

        try:
            canvas.paste(limg, (sx, sy), limg)
            placed.add(role)
        except Exception as e:
            print(f"[FG_COMPOSITE] paste failed role={role}: {e}")
            skipped.add(role)

    res.placed_roles = sorted(placed)
    res.skipped_roles = sorted(skipped)
    res.product_placed = bool(placed & {"product", "main_image"})
    res.logo_placed = "logo" in placed
    res.headline_placed = "title" in placed
    res.body_text_placed = "body_text" in placed
    res.cta_placed = "cta" in placed
    res.human_subject_preserved = "human_subject" in placed
    res.composite_image = canvas.convert("RGB")
    res.success = True

    print(
        f"[FG_COMPOSITE] placed={res.placed_roles} skipped={res.skipped_roles}"
        f" product={res.product_placed} logo={res.logo_placed}"
        f" headline={res.headline_placed} cta={res.cta_placed}",
        flush=True,
    )
    return res
