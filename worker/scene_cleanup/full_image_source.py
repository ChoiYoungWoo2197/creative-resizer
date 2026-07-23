"""Bundle D-1: Build FullImageSource from already-loaded composite image."""
from __future__ import annotations

from scene_cleanup.models import FullImageSource


def build_full_image_source(
    *,
    source_image: object,
    source_path: str,
    source_file_sha256: str,
    composite_sha256: str,
    source_type: str,
    has_native_layers: bool,
    composite_render_method: str,
) -> FullImageSource:
    """Wrap already-loaded PIL Image as FullImageSource for semantic cleanup.

    Raises RuntimeError if source_image is None or not a PIL Image.
    """
    if source_image is None:
        raise RuntimeError("FULL_IMAGE_SOURCE_MISSING: source_image is None")

    from PIL import Image
    if not isinstance(source_image, Image.Image):
        raise RuntimeError(
            f"FULL_IMAGE_SOURCE_INVALID: expected PIL.Image.Image, "
            f"got {type(source_image).__name__}"
        )

    w, h = source_image.size
    if w <= 0 or h <= 0:
        raise RuntimeError(f"FULL_IMAGE_SOURCE_ZERO_SIZE: {w}x{h}")

    return FullImageSource(
        image=source_image,
        source_path=source_path or "",
        source_type=source_type or "unknown",
        source_file_sha256=source_file_sha256 or "",
        composite_sha256=composite_sha256 or "",
        width=w,
        height=h,
        has_native_layers=has_native_layers,
        composite_render_method=composite_render_method or source_type or "unknown",
    )
