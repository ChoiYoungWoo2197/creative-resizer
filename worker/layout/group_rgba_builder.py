"""Stage E P1-C: CTA/title group RGBA builder.

Creates actual RGBA composites for semantic groups (CTA, title), not just
relationship metadata. The composited RGBA object is what the compositor places.

CTA group structure:
  text + background + icon + border

Title group structure:
  text + highlight + emphasis shape + shadow/outline

Required output contract:
  groupImageCreated=True
  allRequiredChildrenRendered=True
  missingChildObjectIds=[]
  No duplicate child compositing

Logs:
  [GROUP_RGBA_BUILD] with composite metadata
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field


@dataclass
class GroupBuildResult:
    """Result of building a group RGBA composite."""
    group_id: str = ""
    group_type: str = ""
    group_image: object = None      # PIL RGBA Image
    group_image_created: bool = False
    all_required_children_rendered: bool = False
    missing_child_object_ids: list = field(default_factory=list)
    rendered_child_object_ids: list = field(default_factory=list)
    duplicate_child_ids: list = field(default_factory=list)
    width: int = 0
    height: int = 0


class GroupRGBABuilder:
    """Builds RGBA composite images for CTA and title semantic groups.

    Each group is composited from its child objects in definition order.
    Children are placed by their bounding boxes (x, y, w, h).
    """

    def build_group_image(
        self,
        child_images: dict,
        layout: dict,
        *,
        group_id: str = "",
        group_type: str = "cta",
    ) -> GroupBuildResult:
        """Build an RGBA composite for a group.

        Args:
            child_images: dict mapping objectId → PIL RGBA Image (or None)
            layout: dict with:
              width, height: canvas dimensions for the group
              children: list of dicts with objectId, x, y, w, h, required

        Returns:
            GroupBuildResult with group_image and contract fields
        """
        from PIL import Image

        canvas_w = int(layout.get("width", 0))
        canvas_h = int(layout.get("height", 0))
        children = layout.get("children", [])

        if canvas_w <= 0 or canvas_h <= 0:
            return GroupBuildResult(
                group_id=group_id,
                group_type=group_type,
                group_image_created=False,
                all_required_children_rendered=False,
            )

        # Create blank RGBA canvas
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

        rendered_ids: list[str] = []
        missing_ids: list[str] = []
        seen_ids: set[str] = set()
        duplicate_ids: list[str] = []

        for child in children:
            oid = child.get("objectId", "")
            required = child.get("required", False)

            if not oid:
                continue

            # Duplicate detection
            if oid in seen_ids:
                duplicate_ids.append(oid)
                continue
            seen_ids.add(oid)

            child_img = child_images.get(oid)
            if child_img is None:
                if required:
                    missing_ids.append(oid)
                continue

            # Place child on canvas
            try:
                cx = int(child.get("x", 0))
                cy = int(child.get("y", 0))
                cw = int(child.get("w", child_img.width))
                ch = int(child.get("h", child_img.height))

                # Resize child if needed
                if (child_img.width, child_img.height) != (cw, ch):
                    child_img = child_img.resize((cw, ch), Image.LANCZOS)

                # Convert to RGBA
                if child_img.mode != "RGBA":
                    child_img = child_img.convert("RGBA")

                canvas.paste(child_img, (cx, cy), child_img)
                rendered_ids.append(oid)
            except Exception:
                if required:
                    missing_ids.append(oid)

        all_rendered = len(missing_ids) == 0

        result = GroupBuildResult(
            group_id=group_id,
            group_type=group_type,
            group_image=canvas,
            group_image_created=True,
            all_required_children_rendered=all_rendered,
            missing_child_object_ids=missing_ids,
            rendered_child_object_ids=rendered_ids,
            duplicate_child_ids=duplicate_ids,
            width=canvas_w,
            height=canvas_h,
        )
        log_group_rgba_build(result)
        return result


def log_group_rgba_build(result: GroupBuildResult, *, job_id: str = "", spec_id: str = "") -> None:
    """Emit [GROUP_RGBA_BUILD] log."""
    print(
        f"[GROUP_RGBA_BUILD] jobId={job_id} specId={spec_id}"
        f" groupId={result.group_id!r}"
        f" groupType={result.group_type!r}"
        f" groupImageCreated={result.group_image_created}"
        f" allRequiredChildrenRendered={result.all_required_children_rendered}"
        f" renderedCount={len(result.rendered_child_object_ids)}"
        f" missingChildObjectIds={result.missing_child_object_ids}"
        f" duplicateChildIds={result.duplicate_child_ids}"
        f" size={result.width}x{result.height}",
        flush=True,
    )
