"""Stage 19B — External AI Inpaint Provider abstraction.

Feature flags (all default OFF/compareOnly):
  BACKGROUND_AI_ENABLED=false
  BACKGROUND_AI_PROVIDER=
  BACKGROUND_AI_API_KEY=
  BACKGROUND_AI_MODEL=
  BACKGROUND_AI_TIMEOUT_SECONDS=120
  BACKGROUND_AI_MAX_RETRIES=1
  BACKGROUND_AI_COMPARE_ONLY=true

Policy prompt fragments always included to prevent AI rewriting
protected objects.
"""
from __future__ import annotations

import abc
import io
import os
import time
from PIL import Image, ImageStat

from .schemas import BackgroundCandidate

# ── safety prompt fragments ────────────────────────────────────────────────────
_SAFETY_PROMPT_FRAGMENTS = (
    "fill only masked background area",
    "do not redraw product",
    "do not redraw logo",
    "do not redraw text or cta",
    "do not redraw people",
    "no watermark",
    "no extra product copy",
    "no duplicated objects",
    "no decorative text",
    "preserve surrounding lighting and perspective",
    "no new objects",
)

_FORBIDDEN_PROMPT_KEYWORDS = (
    "product", "logo", "text", "cta", "person", "face",
    "watermark", "redrawn", "regenerate",
)


def _build_safe_prompt(user_prompt: str = "") -> str:
    safe_parts = list(_SAFETY_PROMPT_FRAGMENTS)
    if user_prompt:
        low = user_prompt.lower()
        for kw in _FORBIDDEN_PROMPT_KEYWORDS:
            if kw in low:
                user_prompt = user_prompt.replace(kw, f"<blocked:{kw}>")
        safe_parts = [user_prompt] + safe_parts
    return "; ".join(safe_parts)


# ── provider interface ────────────────────────────────────────────────────────

class BackgroundGenerationProvider(abc.ABC):
    """Abstract provider for background inpaint / outpaint."""

    @abc.abstractmethod
    def health(self) -> dict:
        """Return provider health/availability dict."""

    @abc.abstractmethod
    def inpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        prompt: str = "",
        options: dict | None = None,
    ) -> Image.Image | None:
        """Inpaint masked region. Returns result or None on failure."""

    @abc.abstractmethod
    def outpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        target_size: tuple[int, int],
        prompt: str = "",
        options: dict | None = None,
    ) -> Image.Image | None:
        """Outpaint to target_size. Returns result or None on failure."""

    @abc.abstractmethod
    def metadata(self) -> dict:
        """Return provider identification metadata."""


# ── validation helpers ────────────────────────────────────────────────────────

class _ProviderResponseValidator:
    """Validates AI provider responses against safety constraints."""

    def validate(
        self,
        result: Image.Image,
        expected_w: int,
        expected_h: int,
        generation_blocked_mask: Image.Image | None = None,
    ) -> tuple[bool, list[str]]:
        """Returns (valid, rejection_reasons)."""
        reasons: list[str] = []

        # size check
        if result.width != expected_w or result.height != expected_h:
            reasons.append(f"size_mismatch:{result.width}x{result.height}!={expected_w}x{expected_h}")

        # blank check
        try:
            stat = ImageStat.Stat(result.convert("RGB"))
            variance = sum(stat.var) / max(len(stat.var), 1)
            if variance < 0.5:
                reasons.append("output_blank")
        except Exception:
            reasons.append("stat_failed")

        # reopen check
        try:
            buf = io.BytesIO()
            result.save(buf, format="PNG")
            buf.seek(0)
            Image.open(buf).load()
        except Exception as exc:
            reasons.append(f"reopen_failed:{exc}")

        # protected mask check — pixel change in blocked region should be ~0
        if generation_blocked_mask is not None:
            try:
                import numpy as np
                blocked = np.array(generation_blocked_mask.convert("L"), dtype=bool)
                if blocked.any():
                    reasons.append("protected_region_changed:validation_skipped_no_source")
            except Exception:
                pass

        return len(reasons) == 0, reasons


_validator = _ProviderResponseValidator()


# ── Fake provider (for tests / feature-flag-off state) ───────────────────────

class FakeBackgroundProvider(BackgroundGenerationProvider):
    """Deterministic fake provider — no external calls, no API key needed.

    Returns a solid-color image derived from the input image border average.
    Useful for unit tests and dry-run verification.
    """

    def health(self) -> dict:
        return {"available": True, "provider": "fake", "realInference": False}

    def inpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        prompt: str = "",
        options: dict | None = None,
    ) -> Image.Image | None:
        try:
            img_rgb = image.convert("RGB")
            mk = mask.convert("L")
            border_color = self._border_mean(img_rgb)
            fill = self._gradient_fill(img_rgb.size, border_color)
            result = img_rgb.copy()
            result.paste(fill, (0, 0), mk)
            return result
        except Exception:
            return None

    def outpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        target_size: tuple[int, int],
        prompt: str = "",
        options: dict | None = None,
    ) -> Image.Image | None:
        try:
            tw, th = target_size
            img_rgb = image.convert("RGB")
            border_color = self._border_mean(img_rgb)
            canvas = self._gradient_fill((tw, th), border_color)
            # center existing image
            px = (tw - img_rgb.width) // 2
            py = (th - img_rgb.height) // 2
            canvas.paste(img_rgb, (max(0, px), max(0, py)))
            return canvas
        except Exception:
            return None

    def metadata(self) -> dict:
        return {"provider": "fake", "model": "fake-v0", "realInference": False}

    @staticmethod
    def _gradient_fill(size: tuple[int, int], base_color: tuple[int, int, int]) -> Image.Image:
        """Deterministic top-to-bottom gradient fill to avoid blank-check rejection.

        Variance must be >= 0.5 to pass _basic_contamination_check.
        A ±8 R-channel gradient over image height gives variance ~5.
        """
        import numpy as np
        w, h = size
        r0, g0, b0 = base_color
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        row_indices = np.arange(h, dtype=np.float32)
        r_channel = np.clip(r0 + (row_indices * 8.0 / max(h - 1, 1)).astype(np.uint8), 0, 255)
        arr[:, :, 0] = r_channel[:, None]
        arr[:, :, 1] = g0
        arr[:, :, 2] = b0
        return Image.fromarray(arr, "RGB")

    @staticmethod
    def _border_mean(img: Image.Image) -> tuple[int, int, int]:
        w, h = img.size
        pixels: list[tuple[int, int, int]] = []
        for x in range(w):
            pixels.append(img.getpixel((x, 0))[:3])
            pixels.append(img.getpixel((x, h - 1))[:3])
        for y in range(1, h - 1):
            pixels.append(img.getpixel((0, y))[:3])
            pixels.append(img.getpixel((w - 1, y))[:3])
        if not pixels:
            return (128, 128, 128)
        r = int(sum(p[0] for p in pixels) / len(pixels))
        g = int(sum(p[1] for p in pixels) / len(pixels))
        b = int(sum(p[2] for p in pixels) / len(pixels))
        return (r, g, b)


# ── HTTP provider — delegates to OpenAI ──────────────────────────────────────

class ExternalInpaintProvider(BackgroundGenerationProvider):
    """HTTP-based external AI inpaint/outpaint.

    Delegates to OpenAIInpaintProvider when BACKGROUND_AI_API_KEY is set.
    API key is NEVER logged or stored in artifacts.

    Env vars:
      BACKGROUND_AI_API_KEY  — required for real inference
      BACKGROUND_AI_MODEL    — default: gpt-image-1
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "",
        timeout: int = 120,
        max_retries: int = 1,
    ) -> None:
        from .openai_provider import OpenAIInpaintProvider
        self._openai = OpenAIInpaintProvider(
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
        self._max_retries = max_retries
        self._available = self._openai.is_configured()

    def health(self) -> dict:
        h = self._openai.health()
        h["provider"] = "external_http"
        return h

    def inpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        prompt: str = "",
        options: dict | None = None,
    ) -> Image.Image | None:
        if not self._available:
            return None
        return self._openai.inpaint(image=image, mask=mask, prompt=prompt, options=options)

    def outpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        target_size: tuple[int, int],
        prompt: str = "",
        options: dict | None = None,
    ) -> Image.Image | None:
        if not self._available:
            return None
        tw, th = target_size
        # Resize source to target, build full-canvas generation mask, call inpaint
        src_resized = image.resize((tw, th), Image.LANCZOS)
        mask_resized = mask.resize((tw, th), Image.LANCZOS)
        return self._openai.inpaint(
            image=src_resized, mask=mask_resized, prompt=prompt, options=options
        )

    def metadata(self) -> dict:
        m = self._openai.metadata()
        m["provider"] = "external_http"
        return m


# ── provider result normalizer ────────────────────────────────────────────────

def normalize_provider_result(
    value: "Image.Image | None | tuple",
) -> "tuple[Image.Image | None, str]":
    """Normalize provider.inpaint() / outpaint() result to (image, provider_name).

    Different providers have different return contracts:
      - BackgroundGenerationProvider subclasses → Image | None
      - ProviderFallbackChain → (Image | None, str)

    This function is the single boundary where all provider results are unified.
    Call it immediately after every provider.inpaint() / provider.outpaint() call.

    Raises:
      TypeError with AI_PROVIDER_INVALID_* prefix for unrecognized types.
    """
    if value is None:
        return None, "none"

    if isinstance(value, tuple):
        if len(value) != 2:
            raise TypeError(
                f"AI_PROVIDER_INVALID_TUPLE_LENGTH:{len(value)}"
            )
        image, provider_name = value
        if image is not None and not isinstance(image, Image.Image):
            raise TypeError(
                f"AI_PROVIDER_INVALID_IMAGE_TYPE:{type(image).__name__}"
            )
        return image, str(provider_name)

    if not isinstance(value, Image.Image):
        raise TypeError(
            f"AI_PROVIDER_INVALID_RESULT_TYPE:{type(value).__name__}"
        )

    return value, "unknown"


# ── fallback chain ────────────────────────────────────────────────────────────

class ProviderFallbackChain:
    """Tries providers in order, returns first non-None result."""

    def __init__(self, providers: list[BackgroundGenerationProvider]) -> None:
        self._providers = providers

    def inpaint(self, *args, **kwargs) -> tuple[Image.Image | None, str]:
        for p in self._providers:
            result = p.inpaint(*args, **kwargs)
            if result is not None:
                return result, p.metadata().get("provider", "unknown")
        return None, "none"

    def outpaint(self, *args, **kwargs) -> tuple[Image.Image | None, str]:
        for p in self._providers:
            result = p.outpaint(*args, **kwargs)
            if result is not None:
                return result, p.metadata().get("provider", "unknown")
        return None, "none"


# ── factory ───────────────────────────────────────────────────────────────────

class ProviderFactory:
    """Creates the appropriate provider chain from environment config.

    Production (ALLOW_FAKE_PROVIDER=false, the default):
      - enable_external=True + key set  → ExternalInpaintProvider only (no fake fallback)
      - enable_external=True + no key   → RuntimeError (fail-closed)
      - enable_external=False           → RuntimeError (fail-closed)

    Testing (ALLOW_FAKE_PROVIDER=true):
      - enable_external=True + key set  → ProviderFallbackChain([external, fake])
      - enable_external=False           → FakeBackgroundProvider
      use_fake_for_test=True always returns FakeBackgroundProvider regardless of env.
    """

    @staticmethod
    def create(
        enable_external: bool = False,
        use_fake_for_test: bool = False,
    ) -> BackgroundGenerationProvider | ProviderFallbackChain:
        if use_fake_for_test:
            return FakeBackgroundProvider()

        allow_fake = os.environ.get("ALLOW_FAKE_PROVIDER", "false").lower() in ("true", "1", "yes")

        external = ExternalInpaintProvider(
            timeout=int(os.environ.get("BACKGROUND_AI_TIMEOUT_SECONDS", 120)),
            max_retries=int(os.environ.get("BACKGROUND_AI_MAX_RETRIES", 1)),
        )
        key_available = external.health()["available"]

        if enable_external and key_available:
            if allow_fake:
                return ProviderFallbackChain([external, FakeBackgroundProvider()])
            return external

        if allow_fake:
            return FakeBackgroundProvider()

        # Fail-closed: no AI provider available and fake is not permitted.
        print(
            f"[AI_PROVIDER_FAILURE] No AI provider available"
            f" (enable_external={enable_external}, key_available={key_available},"
            f" ALLOW_FAKE_PROVIDER=false). Set BACKGROUND_AI_API_KEY or"
            f" ALLOW_FAKE_PROVIDER=true for local testing.",
            flush=True,
        )
        raise RuntimeError(
            "[AI_PROVIDER_FAILURE] No AI provider available and fake is not permitted in production."
            " Set BACKGROUND_AI_API_KEY or ALLOW_FAKE_PROVIDER=true for local testing."
        )


# ── external inpaint runner ───────────────────────────────────────────────────

def run_external_inpaint(
    source_image: Image.Image,
    removal_mask: Image.Image,
    provider: BackgroundGenerationProvider | None = None,
    prompt: str = "",
    compare_only: bool = True,
    generation_blocked_mask: Image.Image | None = None,
) -> BackgroundCandidate:
    """Run external inpaint and return a BackgroundCandidate.

    compare_only=True: runs the pipeline and evaluates but does not apply result.
    """
    t0 = time.time()
    prov = provider or FakeBackgroundProvider()
    meta = prov.metadata()
    prov_name = meta.get("provider", "external")

    safe_prompt = _build_safe_prompt(prompt)
    expected_w, expected_h = source_image.size

    result_img = prov.inpaint(source_image, removal_mask, prompt=safe_prompt)
    elapsed = int((time.time() - t0) * 1000)

    if result_img is None:
        return BackgroundCandidate(
            candidate_id=f"external_{prov_name}_inpaint",
            provider=prov_name,
            method="external_inpaint",
            accepted=False,
            rejection_reasons=["provider_returned_none"],
            elapsed_ms=elapsed,
        )

    valid, reasons = _validator.validate(result_img, expected_w, expected_h, generation_blocked_mask)
    real_inference = meta.get("realInference", False)

    c = BackgroundCandidate(
        candidate_id=f"external_{prov_name}_inpaint",
        provider=prov_name,
        method="external_inpaint",
        image=result_img if valid else None,
        score=75.0 if (valid and real_inference) else (50.0 if valid else 0.0),
        accepted=False,  # set by QualityGate
        rejection_reasons=reasons,
        elapsed_ms=elapsed,
        extras={
            "realInference": real_inference,
            "compareOnly": compare_only,
            "safePromptUsed": True,
        },
    )
    return c
