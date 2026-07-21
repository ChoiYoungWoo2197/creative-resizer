"""Stage 20D: CTA group detection and layout.

A CTA group is a composite unit: background-rect + text + optional icon.
Detection is proximity-based: non-text layer with cta role or overlapping with
a cta text layer within 20px is considered the CTA background.
"""
from __future__ import annotations
from .schemas import CTAGroup


def _bbox_overlap_area(b1: dict, b2: dict) -> int:
    x1 = max(b1.get("x", 0), b2.get("x", 0))
    y1 = max(b1.get("y", 0), b2.get("y", 0))
    x2 = min(b1.get("x", 0) + b1.get("width", 0), b2.get("x", 0) + b2.get("width", 0))
    y2 = min(b1.get("y", 0) + b1.get("height", 0), b2.get("y", 0) + b2.get("height", 0))
    return max(0, x2 - x1) * max(0, y2 - y1)


def _expand_bbox(bbox: dict, px: int) -> dict:
    return {
        "x": bbox.get("x", 0) - px,
        "y": bbox.get("y", 0) - px,
        "width": bbox.get("width", 0) + px * 2,
        "height": bbox.get("height", 0) + px * 2,
    }


def detect_cta_groups(layers: list[dict]) -> list[CTAGroup]:
    """Identify CTA composite groups from classified layers.

    A group is formed when:
    - A text layer with role="cta" exists.
    - A non-text layer (shape/pixel) is within 20px proximity of that text layer.

    Returns list of CTAGroup; may be empty.
    """
    cta_text = [l for l in layers if l.get("role") == "cta"
                and (l.get("isTextLayer") or l.get("type") in ("type", "text"))
                and not l.get("dedupSkip")]
    if not cta_text:
        return []

    non_text = [l for l in layers
                if not l.get("isTextLayer") and l.get("type") not in ("type", "text")
                and not l.get("dedupSkip") and l.get("role") in ("cta", "unknown", "decoration")]

    groups: list[CTAGroup] = []
    for cta_layer in cta_text:
        expanded = _expand_bbox(cta_layer["bbox"], 20)
        bg_candidate = None
        best_area = 0
        for other in non_text:
            area = _bbox_overlap_area(expanded, other["bbox"])
            if area > best_area:
                best_area = area
                bg_candidate = other

        # Merge bboxes
        cbb = cta_layer["bbox"]
        if bg_candidate:
            bbb = bg_candidate["bbox"]
            gx = min(cbb.get("x", 0), bbb.get("x", 0))
            gy = min(cbb.get("y", 0), bbb.get("y", 0))
            gx2 = max(cbb.get("x", 0) + cbb.get("width", 0),
                      bbb.get("x", 0) + bbb.get("width", 0))
            gy2 = max(cbb.get("y", 0) + cbb.get("height", 0),
                      bbb.get("y", 0) + bbb.get("height", 0))
            group_bbox = {"x": gx, "y": gy, "width": gx2 - gx, "height": gy2 - gy}
            confidence = min(1.0, best_area / max(cbb["width"] * cbb["height"], 1))
        else:
            group_bbox = cbb.copy()
            confidence = 0.3

        groups.append(CTAGroup(
            group_id=f"cta_group_{cta_layer['id']}",
            text_layer_id=cta_layer["id"],
            text_content=cta_layer.get("textContent", ""),
            bg_layer_id=bg_candidate["id"] if bg_candidate else "",
            bbox=group_bbox,
            confidence=round(confidence, 3),
        ))

    return groups
