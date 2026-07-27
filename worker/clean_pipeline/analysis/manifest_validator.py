"""Validate SceneManifest against structural and business rules."""
from __future__ import annotations

from clean_pipeline.analysis.models import SceneManifest


def validate(
    manifest: SceneManifest,
    image_width: int,
    image_height: int,
) -> tuple[bool, str, str]:
    """Return (is_valid, fail_code, reason). fail_code and reason are '' on success."""

    # 1. Empty manifest
    if not manifest.objects:
        return False, "MANIFEST_EMPTY", "Manifest contains no objects"

    # 2. ID duplicates
    ids = [o.id for o in manifest.objects]
    seen: set[str] = set()
    dupes = [oid for oid in ids if oid in seen or seen.add(oid)]  # type: ignore[func-returns-value]
    if dupes:
        return False, "DUPLICATE_IDS", f"Duplicate object ids: {dupes}"

    for obj in manifest.objects:
        # 3. protected_subject rules
        if obj.role == "protected_subject":
            if obj.movable or obj.removable_from_scene:
                return (
                    False,
                    "PROTECTED_SUBJECT_VIOLATION",
                    f"Object '{obj.id}' is protected_subject but has movable={obj.movable} "
                    f"or removableFromScene={obj.removable_from_scene}",
                )

        # 4. Required objects must have polygon
        if obj.required and not obj.polygon:
            return (
                False,
                "REQUIRED_POLYGON_MISSING",
                f"Required object '{obj.id}' (role={obj.role}) has no polygon",
            )

        # 5. Coordinates within bounds
        b = obj.bbox
        if b.x < 0 or b.y < 0 or b.x + b.width > image_width or b.y + b.height > image_height:
            return (
                False,
                "COORDINATES_OUT_OF_BOUNDS",
                f"Object '{obj.id}' bbox [{b.x},{b.y},{b.width},{b.height}] "
                f"exceeds image {image_width}x{image_height}",
            )
        for pt in obj.polygon:
            if len(pt) >= 2:
                px, py = int(pt[0]), int(pt[1])
                if px < 0 or py < 0 or px > image_width or py > image_height:
                    return (
                        False,
                        "COORDINATES_OUT_OF_BOUNDS",
                        f"Object '{obj.id}' polygon point {pt} exceeds image {image_width}x{image_height}",
                    )

    # 6. At least one removable ad object
    removable = [o for o in manifest.objects if o.removable_from_scene]
    if not removable:
        return False, "NO_REMOVABLE_OBJECTS", "Manifest has no removable advertising objects"

    return True, "", ""
