"""D-2: Post-process raw alpha mask (noise removal, feather).

Numpy/Pillow only. scipy.ndimage used if available for component analysis.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def refine_alpha_mask(
    rgba_image: Image.Image,
    *,
    min_component_size: int = 50,
    feather_radius: float = 1.5,
    fill_holes: bool = True,
) -> tuple[Image.Image, dict]:
    """Refine RGBA alpha mask: noise removal, hole fill, gentle feather.

    Returns (refined_rgba, metrics_dict).
    """
    if rgba_image.mode != "RGBA":
        rgba_image = rgba_image.convert("RGBA")

    arr = np.array(rgba_image)
    alpha = arr[:, :, 3].copy()

    # Trivially uniform — nothing to refine
    if np.all(alpha == 0) or np.all(alpha > 0):
        return rgba_image, {"component_count": 0, "refined": False}

    binary = (alpha > 127).astype(np.uint8)
    component_count = 1

    try:
        from scipy import ndimage as ndi

        labeled, component_count = ndi.label(binary)

        if component_count > 1 and min_component_size > 0:
            sizes = np.bincount(labeled.ravel())
            for comp_id in range(1, component_count + 1):
                if comp_id < len(sizes) and sizes[comp_id] < min_component_size:
                    binary[labeled == comp_id] = 0

        if fill_holes:
            binary = ndi.binary_fill_holes(binary).astype(np.uint8)

    except ImportError:
        pass

    refined_alpha = (alpha * binary).astype(np.uint8)

    # Gentle Gaussian feather on alpha channel
    if feather_radius > 0:
        alpha_img = Image.fromarray(refined_alpha, mode="L")
        alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(radius=feather_radius))
        refined_alpha = np.array(alpha_img)

    result_arr = arr.copy()
    result_arr[:, :, 3] = refined_alpha
    result = Image.fromarray(result_arr, mode="RGBA")

    return result, {
        "component_count": component_count,
        "refined": True,
        "feather_radius": feather_radius,
    }
