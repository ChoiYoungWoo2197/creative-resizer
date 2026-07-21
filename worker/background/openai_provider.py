"""Stage 20.3: Real OpenAI image editing provider.

Uses OpenAI /v1/images/edits endpoint (gpt-image-1 or dall-e-2).
Pure stdlib HTTP — no openai SDK dependency.

Env vars (read by ExternalInpaintProvider, not hardcoded here):
  BACKGROUND_AI_API_KEY   — required for real inference
  BACKGROUND_AI_MODEL     — default: gpt-image-1

Security:
  - API key is NEVER logged, printed, or stored in artifacts.
  - Only configured=true/false and keyLength are allowed in outputs.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional

from PIL import Image

_OPENAI_EDIT_URL = "https://api.openai.com/v1/images/edits"
_DEFAULT_MODEL = "gpt-image-1"
_DEFAULT_TIMEOUT = 120


# ── Multipart builder ─────────────────────────────────────────────────────────

def _build_multipart(
    fields: dict[str, str],
    files: dict[str, tuple[str, str, bytes]],
) -> tuple[bytes, str]:
    """Build multipart/form-data body.

    fields: {name: value}
    files:  {field_name: (filename, content_type, data_bytes)}
    Returns (body, boundary).
    """
    boundary = "Stage203Boundary" + uuid.uuid4().hex
    crlf = b"\r\n"
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}".encode("utf-8") + crlf
        )

    for field_name, (filename, content_type, data) in files.items():
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n"
            f"\r\n"
        ).encode("utf-8")
        parts.append(header + data + crlf)

    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


# ── Image / mask conversion ───────────────────────────────────────────────────

def _to_png_bytes_rgba(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _gen_allowed_to_openai_mask_bytes(gen_allowed_mask: Image.Image) -> bytes:
    """Convert generationAllowedMask to OpenAI edit mask PNG.

    generationAllowedMask: L-mode, 255=generate (edit area), 0=preserve
    OpenAI mask: RGBA PNG, alpha=0 (transparent) = edit area

    Conversion: mask_alpha = 255 - gen_allowed_L
    """
    from PIL import ImageOps
    mask_l = gen_allowed_mask.convert("L")
    # RGBA canvas: RGB=white, alpha=inverted(gen_allowed)
    mask_rgba = Image.new("RGBA", mask_l.size, (255, 255, 255, 255))
    mask_rgba.putalpha(ImageOps.invert(mask_l))
    buf = io.BytesIO()
    mask_rgba.save(buf, format="PNG")
    return buf.getvalue()


# ── OpenAI Provider ───────────────────────────────────────────────────────────

class OpenAIInpaintProvider:
    """Real OpenAI image editing provider for source-faithful repair.

    Calls /v1/images/edits with image + mask + prompt.
    Compatible with BackgroundGenerationProvider interface.
    """

    PROVIDER_NAME = "openai"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "",
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key or os.environ.get("BACKGROUND_AI_API_KEY", "")
        self._model = model or os.environ.get("BACKGROUND_AI_MODEL", _DEFAULT_MODEL)
        self._timeout = timeout
        self._configured = bool(self._api_key)

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return self._model

    def is_configured(self) -> bool:
        return self._configured

    def health(self) -> dict:
        return {
            "available": self._configured,
            "provider": self.PROVIDER_NAME,
            "providerName": self.PROVIDER_NAME,
            "model": self._model,
            "modelName": self._model,
            "realInference": self._configured,
            "apiKeyConfigured": self._configured,
        }

    def metadata(self) -> dict:
        return {
            "provider": self.PROVIDER_NAME,
            "providerName": self.PROVIDER_NAME,
            "model": self._model,
            "modelName": self._model,
            "realInference": self._configured,
            "apiKeyConfigured": self._configured,
            # API key intentionally NOT included
        }

    def inpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        prompt: str = "",
        options: dict | None = None,
    ) -> Image.Image | None:
        """Inpaint masked region using OpenAI images.edit.

        mask: L-mode, 255=generate, 0=preserve
              (same convention as generationAllowedMask)
        Returns PIL Image or None on failure.
        """
        if not self._configured:
            return None
        opts = options or {}
        timeout = opts.get("timeout_seconds", self._timeout)
        try:
            return self._call_api(
                image=image,
                mask=mask,
                prompt=prompt,
                target_w=image.width,
                target_h=image.height,
                timeout=timeout,
            )
        except Exception as exc:
            print(f"[OpenAI] inpaint error: {type(exc).__name__}: {str(exc)[:120]}")
            return None

    def generate_repair(
        self,
        *,
        reference_image: Image.Image,
        generation_allowed_mask: Image.Image,
        target_width: int,
        target_height: int,
        prompt: str,
        prompt_version: str = "",
        timeout_seconds: int = 0,
        request_id: str = "",
    ) -> dict:
        """Full-metadata API call for Stage 20.3 diagnostics.

        Returns dict with success, provider, model, requestId, elapsedMs,
        outputImage, errorCode, errorMessage, promptVersion, responseHash.
        API key is NEVER in the return value.
        """
        t0 = time.time()
        rid = request_id or str(uuid.uuid4())

        if not self._configured:
            return {
                "success": False,
                "provider": self.PROVIDER_NAME,
                "model": self._model,
                "requestId": rid,
                "elapsedMs": 0,
                "outputImage": None,
                "errorCode": "PROVIDER_NOT_CONFIGURED",
                "errorMessage": "BACKGROUND_AI_API_KEY not set",
                "promptVersion": prompt_version,
                "responseHash": "",
                "actualApiCalled": False,
            }

        timeout = timeout_seconds or self._timeout
        error_code = ""
        error_message = ""
        img: Image.Image | None = None

        try:
            img = self._call_api(
                image=reference_image,
                mask=generation_allowed_mask,
                prompt=prompt,
                target_w=target_width,
                target_h=target_height,
                timeout=timeout,
            )
            if img is None:
                error_code = "EMPTY_RESPONSE"
                error_message = "API returned no image"
        except urllib.error.HTTPError as exc:
            error_code = f"HTTP_{exc.code}"
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            # Mask key: only log error code and sanitized body
            error_message = f"HTTP {exc.code}: {body[:200]}"
        except urllib.error.URLError as exc:
            error_code = "NETWORK_ERROR"
            error_message = str(exc.reason)[:200]
        except Exception as exc:
            error_code = "API_ERROR"
            error_message = f"{type(exc).__name__}: {str(exc)[:200]}"

        elapsed = int((time.time() - t0) * 1000)

        response_hash = ""
        if img is not None:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            response_hash = hashlib.sha256(buf.getvalue()).hexdigest()[:16]

        return {
            "success": img is not None,
            "provider": self.PROVIDER_NAME,
            "model": self._model,
            "requestId": rid,
            "elapsedMs": elapsed,
            "outputImage": img,
            "errorCode": error_code,
            "errorMessage": error_message,
            "promptVersion": prompt_version,
            "responseHash": response_hash,
            "actualApiCalled": True,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _call_api(
        self,
        image: Image.Image,
        mask: Image.Image,
        prompt: str,
        target_w: int,
        target_h: int,
        timeout: int,
    ) -> Image.Image | None:
        """Execute multipart POST to OpenAI /v1/images/edits."""
        image_bytes = _to_png_bytes_rgba(image)
        mask_bytes = _gen_allowed_to_openai_mask_bytes(mask)

        fields = {
            "model": self._model,
            "n": "1",
            "size": "auto",
            "prompt": prompt[:4000],  # API limit guard
        }
        files = {
            "image": ("image.png", "image/png", image_bytes),
            "mask":  ("mask.png",  "image/png", mask_bytes),
        }

        body, boundary = _build_multipart(fields, files)

        req = urllib.request.Request(
            _OPENAI_EDIT_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))

        # Parse response — gpt-image-1 returns b64_json, dall-e-2 may return url
        data_items = response_data.get("data", [])
        if not data_items:
            return None

        item = data_items[0]
        b64 = item.get("b64_json", "")
        if b64:
            img_bytes = base64.b64decode(b64)
        else:
            url = item.get("url", "")
            if not url:
                return None
            with urllib.request.urlopen(url, timeout=60) as r:
                img_bytes = r.read()

        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        # Resize to target if API returned a different size
        if img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), Image.LANCZOS)

        return img
