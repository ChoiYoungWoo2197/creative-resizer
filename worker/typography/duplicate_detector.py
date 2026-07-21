"""Stage 20D: Duplicate text detection.

Prevents title/body_text from appearing twice when:
- A group composite already includes the text, AND the individual text layer is also extracted.
- Two separate text layers contain identical or near-identical content.

Rule:
  1. Group-composite layers with isGroupComposite=True contain their children visually.
     → Text/smartobject children should NOT be re-rendered on top.
  2. Two text layers in the same role with similar text → keep the one with higher font size
     or lower layer_order (original stack order).

Never deletes layers from the list — marks them with dedupSkip=True.
"""
from __future__ import annotations
import unicodedata


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "").strip().lower())


def _similarity(a: str, b: str) -> float:
    """Jaccard token similarity on character bigrams."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    def bigrams(s: str) -> set:
        return {s[i:i+2] for i in range(len(s) - 1)} if len(s) > 1 else {s}
    ba, bb = bigrams(a), bigrams(b)
    if not ba and not bb:
        return 1.0
    intersection = len(ba & bb)
    union = len(ba | bb)
    return intersection / union if union else 0.0


def detect_duplicates(layers: list[dict], similarity_threshold: float = 0.90) -> list[dict]:
    """Mark duplicate text layers with dedupSkip=True.

    Returns new list (original not mutated).
    """
    result = [{**l, "dedupSkip": False} for l in layers]

    # Pass 1: Text layers whose parent group was composite-rendered should be skipped.
    # We identify them by checking if there is a group layer (isGroupComposite=True)
    # whose bbox contains this layer's bbox.
    group_bboxes = [
        l["bbox"] for l in result
        if l.get("isGroupComposite") and not l.get("dedupSkip")
    ]

    for layer in result:
        if layer.get("dedupSkip"):
            continue
        if not layer.get("isTextLayer") and layer.get("type") not in ("type", "text"):
            continue
        if layer.get("isGroupComposite"):
            continue  # the group itself is kept
        lbb = layer.get("bbox", {})
        lx, ly, lw, lh = lbb.get("x", 0), lbb.get("y", 0), lbb.get("width", 0), lbb.get("height", 0)
        for gbb in group_bboxes:
            gx, gy, gw, gh = gbb.get("x", 0), gbb.get("y", 0), gbb.get("width", 0), gbb.get("height", 0)
            # Check if layer bbox is contained within group bbox (with 5px tolerance)
            if (gx - 5 <= lx and ly >= gy - 5
                    and lx + lw <= gx + gw + 5 and ly + lh <= gy + gh + 5):
                layer["dedupSkip"] = True
                layer["dedupReason"] = "covered_by_group_composite"
                break

    # Pass 2: Same-role text similarity dedup
    text_layers = [l for l in result if not l.get("dedupSkip")
                   and (l.get("isTextLayer") or l.get("type") in ("type", "text"))]

    seen: list[dict] = []
    for layer in text_layers:
        text = _norm(layer.get("textContent", ""))
        if not text:
            seen.append(layer)
            continue
        duplicate_found = False
        for prev in seen:
            if prev.get("dedupSkip"):
                continue
            if prev.get("role") != layer.get("role"):
                continue
            sim = _similarity(text, _norm(prev.get("textContent", "")))
            if sim >= similarity_threshold:
                # Keep the one with larger font size; tie → keep prev (lower layer_order)
                prev_size = prev.get("fontSize", 0) or 0
                curr_size = layer.get("fontSize", 0) or 0
                if curr_size > prev_size:
                    prev["dedupSkip"] = True
                    prev["dedupReason"] = f"similar_text_lower_font({sim:.2f})"
                    seen.append(layer)
                else:
                    layer["dedupSkip"] = True
                    layer["dedupReason"] = f"similar_text_lower_font({sim:.2f})"
                duplicate_found = True
                break
        if not duplicate_found:
            seen.append(layer)

    return result


def count_deduped(layers: list[dict]) -> int:
    return sum(1 for l in layers if l.get("dedupSkip"))
