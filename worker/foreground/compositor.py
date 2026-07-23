"""Stage 21: Deterministic foreground compositor.

Stacks foreground layers extracted from the original PSD onto the AI background
plate produced by SourceFaithfulRepair.  Layer order follows a fixed z-order
table (role-based) rather than relying on PSD stacking order, which is less
predictable after role-based filtering.

Bundle A additions:
  - Deduplication by objectId: each layer composited exactly once.
  - FG_MANIFEST log with objectCount / uniqueObjectCount / duplicateObjectIds.
  - compositedCount field updated on each placed layer dict.
  - FG_COMPOSITE_SUMMARY log.
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

    layer_count: int = 0        # total input layers (including duplicates)
    unique_object_count: int = 0
    placed_count: int = 0       # legacy: unique role count (kept for compat)
    skipped_count: int = 0      # legacy: unique skipped role count (kept for compat)
    placed_object_count: int = 0   # Bundle C-1: actual placed object count
    skipped_object_count: int = 0  # Bundle C-1: actual skipped object count
    duplicate_count: int = 0
    duplicate_object_ids: list[str] = field(default_factory=list)
    all_objects_composited_once: bool = True

    # Per-object manifest (for provenance)
    object_manifest: list[dict] = field(default_factory=list)


def composite_foreground(
    background: "Image.Image",
    foreground_layers: list[dict],
    job_id: str = "",
    spec_id: str = "",
) -> ForegroundCompositeResult:
    """Stack foreground layers onto AI background in role z-order.

    Each layer is placed exactly once (deduplicated by objectId).
    Duplicate objectIds are logged and skipped.

    Args:
        background:        AI-generated background plate (RGB or RGBA).
        foreground_layers: list from extract_foreground_layers().
        job_id / spec_id:  for structured log output.

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

    # ── Deduplication pass ────────────────────────────────────────────────────
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    unique_layers: list[dict] = []

    for layer in sorted_layers:
        oid = layer.get("objectId", "")
        if not oid:
            # No objectId → generate a fallback from role+name+bbox for dedup purposes
            role_fb = layer.get("role", "?")
            name_fb = (layer.get("name") or "")[:30]
            bbox_fb = layer.get("bbox", {})
            oid = f"_fallback_{role_fb}_{name_fb}_{bbox_fb.get('x')},{bbox_fb.get('y')}"
        if oid in seen_ids:
            duplicate_ids.append(oid)
            print(
                f"[FG_COMPOSITE] DUPLICATE_FOREGROUND_OBJECT"
                f" jobId={job_id} specId={spec_id}"
                f" objectId={oid!r} role={layer.get('role')!r}"
                f" name={layer.get('name')!r} — skipping duplicate",
                flush=True,
            )
            continue
        seen_ids.add(oid)
        unique_layers.append(layer)

    res.unique_object_count = len(unique_layers)
    res.duplicate_count = len(duplicate_ids)
    res.duplicate_object_ids = duplicate_ids

    # ── FG_MANIFEST log ───────────────────────────────────────────────────────
    print(
        f"[FG_MANIFEST]"
        f" jobId={job_id} specId={spec_id}"
        f" objectCount={res.layer_count}"
        f" uniqueObjectCount={res.unique_object_count}"
        f" duplicateObjectIds={duplicate_ids}"
        f" roles={sorted({l.get('role') for l in unique_layers})}",
        flush=True,
    )

    # ── Composite pass ────────────────────────────────────────────────────────
    placed: set[str] = set()
    skipped: set[str] = set()
    manifest: list[dict] = []

    for layer in unique_layers:
        role = layer.get("role", "unknown")
        limg: "Image.Image | None" = layer.get("image")
        bbox = layer.get("bbox", {})
        oid = layer.get("objectId", "")

        entry: dict = {
            "objectId":          oid,
            "sourceLayerId":     layer.get("layerId", ""),
            "role":              role,
            "sourcePixelSha256": layer.get("sourcePixelSha256", ""),
            "targetBBox":        bbox,
            "zIndex":            _ROLE_ZORDER.get(role, 5),
            "compositedCount":   0,
        }

        if limg is None:
            entry["skippedReason"] = "no_image"
            skipped.add(role)
            manifest.append(entry)
            continue

        sx = int(bbox.get("x", 0))
        sy = int(bbox.get("y", 0))
        sw = int(bbox.get("width", limg.width))
        sh = int(bbox.get("height", limg.height))

        # Skip if completely outside canvas
        if sx >= target_w or sy >= target_h or sx + sw <= 0 or sy + sh <= 0:
            print(
                f"[FG_COMPOSITE] skip out-of-bounds objectId={oid!r} role={role}"
                f" bbox=({sx},{sy},{sw},{sh}) canvas={target_w}x{target_h}",
                flush=True,
            )
            entry["skippedReason"] = "out_of_bounds"
            skipped.add(role)
            manifest.append(entry)
            continue

        if limg.size != (sw, sh):
            limg = limg.resize((sw, sh), Image.LANCZOS)

        try:
            canvas.paste(limg, (sx, sy), limg)
            entry["compositedCount"] = 1
            layer["compositedCount"] = 1  # update in-place for caller
            placed.add(role)
            print(
                f"[FG_COMPOSITE_OBJECT]"
                f" jobId={job_id} specId={spec_id}"
                f" objectId={oid!r} role={role!r}"
                f" targetBBox=({sx},{sy},{sw},{sh})"
                f" compositedCount=1",
                flush=True,
            )
        except Exception as e:
            print(f"[FG_COMPOSITE] paste failed objectId={oid!r} role={role}: {e}", flush=True)
            entry["skippedReason"] = f"paste_error:{e}"
            skipped.add(role)

        manifest.append(entry)

    placed_count = len(placed)  # unique roles (legacy)
    # Bundle C-1: object-level counters (count entries, not unique roles)
    placed_object_count = sum(1 for e in manifest if e.get("compositedCount", 0) == 1)
    skipped_object_count = sum(1 for e in manifest if "skippedReason" in e)
    all_once = (
        res.duplicate_count == 0
        and skipped_object_count == 0
        and all(e.get("compositedCount") == 1 for e in manifest if "skippedReason" not in e)
    )

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
    res.placed_count = placed_count
    res.skipped_count = len(skipped)
    res.placed_object_count = placed_object_count
    res.skipped_object_count = skipped_object_count
    res.all_objects_composited_once = all_once
    res.object_manifest = manifest

    print(
        f"[FG_COMPOSITE_SUMMARY]"
        f" jobId={job_id} specId={spec_id}"
        f" inputObjectCount={res.layer_count}"
        f" uniqueObjectCount={res.unique_object_count}"
        f" placedObjectCount={placed_count}"
        f" skippedObjectCount={res.skipped_count}"
        f" duplicateCount={res.duplicate_count}"
        f" allObjectsCompositedOnce={all_once}"
        f" placed={res.placed_roles}"
        f" product={res.product_placed} logo={res.logo_placed}"
        f" headline={res.headline_placed} cta={res.cta_placed}",
        flush=True,
    )
    return res
