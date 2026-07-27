"""OpenAI image-edit cleanup: remove ad objects from projected scene.

Rules:
- API is called exactly once
- Input is scaled to DALL-E-supported size (1024×1024) internally
- API response must be 1024×1024; FAIL immediately if not
- Result is scaled back to (target_width, target_height) before returning
- Returns (cleaned_image | None, fail_code, fail_reason)
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image

from worker.clean_pipeline.scene.cleanup_prompt import CLEANUP_PROMPT

_API_MODEL = "gpt-image-1"
_API_W = _API_H = 1024


def cleanup(
    projected_image: Image.Image,          # RGB, target_w × target_h
    projected_removal_mask: Image.Image,   # L, target_w × target_h; white = remove
    target_width: int,
    target_height: int,
    api_key: str,
) -> tuple[Image.Image | None, str, str]:
    """Call the DALL-E image-edit API to remove ad objects.

    Returns (cleaned_image, "", "") on success.
    Returns (None, fail_code, fail_reason) on failure.
    API is called exactly once; no retries.
    """
    if not api_key:
        return None, "NO_API_KEY", "OPENAI_API_KEY not set"

    # Scale input to DALL-E-required size
    api_img = projected_image.convert("RGB").resize((_API_W, _API_H), Image.LANCZOS)
    api_mask_l = projected_removal_mask.convert("L").resize((_API_W, _API_H), Image.NEAREST)

    # Build DALL-E mask: transparent (alpha=0) = edit here, opaque = keep
    # Our removal mask: white (255) = remove → transparent in DALL-E mask
    r, g, b = api_img.split()
    removal_arr = np.array(api_mask_l, dtype=np.uint8)
    dall_e_alpha = np.where(removal_arr > 128, 0, 255).astype(np.uint8)
    dall_e_alpha_img = Image.fromarray(dall_e_alpha, "L")
    mask_rgba = Image.merge("RGBA", (r, g, b, dall_e_alpha_img))

    # Encode as PNG bytes
    img_buf = io.BytesIO()
    api_img.convert("RGBA").save(img_buf, format="PNG")
    img_bytes = img_buf.getvalue()

    mask_buf = io.BytesIO()
    mask_rgba.save(mask_buf, format="PNG")
    mask_bytes = mask_buf.getvalue()

    # API call — exactly once
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.images.edit(
            model=_API_MODEL,
            image=("image.png", img_bytes),
            mask=("mask.png", mask_bytes),
            prompt=CLEANUP_PROMPT,
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

    # Verify API returned expected size
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
