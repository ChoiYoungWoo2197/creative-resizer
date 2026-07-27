"""OpenAI image-edit cleanup: remove ad objects / fill outpaint regions.

Rules:
- API is called exactly once
- Input image is sent at native resolution (no squishing) — gpt-image-1 accepts non-square input
- Mask is sent at native resolution, same dimensions as input
- size="1024x1024" is always specified to guarantee a fixed output size
- API response must be 1024×1024; FAIL immediately if not
- Result is scaled back to (target_width, target_height) before returning
- Returns (cleaned_image | None, fail_code, fail_reason)
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image

from clean_pipeline.scene.cleanup_prompt import CLEANUP_PROMPT

_API_MODEL = "gpt-image-1"
_API_W = _API_H = 1024   # expected output size; input may be any size


def cleanup(
    projected_image: Image.Image,          # RGB, any size
    projected_removal_mask: Image.Image,   # L, same size as projected_image; white = edit region
    target_width: int,
    target_height: int,
    api_key: str,
    prompt: str = CLEANUP_PROMPT,
) -> tuple[Image.Image | None, str, str]:
    """Call the gpt-image-1 image-edit API to fill the masked region.

    Sends the image at native resolution (no pre-squishing) so the model has
    undistorted context and accurate mask alignment.

    Returns (cleaned_image, "", "") on success.
    Returns (None, fail_code, fail_reason) on failure.
    API is called exactly once; no retries.
    """
    if not api_key:
        return None, "NO_API_KEY", "OPENAI_API_KEY not set"

    # Build DALL-E mask at native resolution: transparent (alpha=0) = edit, opaque = keep
    src_rgb = projected_image.convert("RGB")
    mask_l = projected_removal_mask.convert("L")

    r, g, b = src_rgb.split()
    removal_arr = np.array(mask_l, dtype=np.uint8)
    dall_e_alpha = np.where(removal_arr > 128, 0, 255).astype(np.uint8)
    dall_e_alpha_img = Image.fromarray(dall_e_alpha, "L")
    mask_rgba = Image.merge("RGBA", (r, g, b, dall_e_alpha_img))

    # Encode as PNG bytes at native resolution — no squishing
    img_buf = io.BytesIO()
    src_rgb.convert("RGBA").save(img_buf, format="PNG")
    img_bytes = img_buf.getvalue()

    mask_buf = io.BytesIO()
    mask_rgba.save(mask_buf, format="PNG")
    mask_bytes = mask_buf.getvalue()

    # API call — exactly once, output forced to 1024×1024
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.images.edit(
            model=_API_MODEL,
            image=("image.png", img_bytes),
            mask=("mask.png", mask_bytes),
            prompt=prompt,
            size="1024x1024",
        )
    except Exception as exc:
        return None, "API_ERROR", f"images.edit failed: {exc}"

    # Decode response — b64_json or URL (SDK version dependent)
    try:
        item = response.data[0]
        b64 = getattr(item, "b64_json", None)
        if b64:
            decoded = base64.b64decode(b64)
        else:
            url = getattr(item, "url", None)
            if not url:
                return None, "EMPTY_RESPONSE", "API returned neither b64_json nor url"
            import urllib.request
            with urllib.request.urlopen(url) as r:  # noqa: S310
                decoded = r.read()
        api_output = Image.open(io.BytesIO(decoded))
    except Exception as exc:
        return None, "RESPONSE_DECODE_FAILED", f"Failed to decode API response: {exc}"

    # Verify API returned expected 1024×1024
    if api_output.size != (_API_W, _API_H):
        return (
            None,
            "CLEANUP_SIZE_MISMATCH",
            f"API returned {api_output.size[0]}×{api_output.size[1]}, "
            f"expected {_API_W}×{_API_H}",
        )

    # Scale back to target dimensions
    cleanup_img = api_output.convert("RGB").resize(
        (target_width, target_height), Image.LANCZOS
    )
    return cleanup_img, "", ""
