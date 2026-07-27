"""Unified Pipeline V2: GPT Vision full-image object analysis.

Calls OpenAI Chat Completions (vision) to detect all ad objects in the canonical
source image with bbox, role, polygon, and text content.

No fake/mock fallback — raises RuntimeError on any failure.
"""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request

from PIL import Image

from unified_v2.contracts import V2DetectedObject

_GPT_MODEL = "gpt-4o"
_MAX_TOKENS = 2048
_API_TIMEOUT = 90

_SYSTEM_PROMPT = """\
You are an ad creative object detector. Analyze this advertisement image and list ALL visible foreground objects.

For EACH object return:
- object_id: unique string, e.g. "obj_0", "obj_1"
- role: exactly one of: product, title, headline, body_text, logo, cta, badge, decorative, human_subject
- bbox: {x, y, width, height} in pixels from the image top-left corner (integer values)
- polygon: array of [x, y] integer pairs approximating the object outline (at least 4 points); use [] if shape is purely rectangular
- text_content: the exact visible text inside the object; use "" for non-text roles
- confidence: float 0.0–1.0

Rules:
- Do NOT include the background or background scenery.
- List every distinct text block, logo, product, and CTA button separately.
- Bounding boxes must fit inside the image.

Respond ONLY with a JSON object in this exact format, no markdown fences:
{"objects": [ {fields above}, ... ]}
"""


def analyze_source_objects(
    canonical_image: Image.Image,
    job_id: str = "",
) -> tuple[list[V2DetectedObject], str]:
    """Call GPT Vision to detect ad objects in canonical_image.

    Returns:
        (detected_objects, model_used)

    Raises:
        RuntimeError if BACKGROUND_AI_API_KEY is absent, the API call fails,
        or the response cannot be parsed. Fail-closed — no empty fallback.
    """
    api_key = os.environ.get("BACKGROUND_AI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "V2_GPT_ANALYSIS_FAILED: BACKGROUND_AI_API_KEY not set — cannot run GPT Vision analysis"
        )

    # Encode canonical image as JPEG base64 (high quality, bounded size)
    rgb = canonical_image.convert("RGB")
    # Downscale if very large to stay within token budget
    max_side = 1600
    if max(rgb.width, rgb.height) > max_side:
        scale = max_side / max(rgb.width, rgb.height)
        rgb = rgb.resize(
            (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))),
            Image.LANCZOS,
        )
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=90)
    b64_image = base64.b64encode(buf.getvalue()).decode("ascii")

    payload = {
        "model": _GPT_MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": _SYSTEM_PROMPT},
                ],
            }
        ],
    }

    encoded = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=encoded,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    print(f"[V2_GPT_ANALYSIS_START] jobId={job_id} model={_GPT_MODEL!r} imageSize={rgb.width}x{rgb.height}", flush=True)

    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"V2_GPT_API_ERROR: HTTP {exc.code} — {body[:300]}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"V2_GPT_API_ERROR: {exc}") from exc

    try:
        api_resp = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"V2_GPT_RESPONSE_NOT_JSON: {exc} raw={raw[:200]}") from exc

    model_used = api_resp.get("model", _GPT_MODEL)
    choices = api_resp.get("choices", [])
    if not choices:
        raise RuntimeError(f"V2_GPT_NO_CHOICES: api_resp={raw[:300]}")

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("V2_GPT_EMPTY_CONTENT: GPT returned empty message content")

    # Strip accidental markdown fences
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        cleaned = "\n".join(inner).strip() if inner else cleaned

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"V2_GPT_PARSE_FAILED: {exc} content={content[:400]}"
        ) from exc

    raw_objects = parsed.get("objects", [])
    if not isinstance(raw_objects, list):
        raise RuntimeError(f"V2_GPT_BAD_FORMAT: 'objects' is not a list — {type(raw_objects)}")

    detected: list[V2DetectedObject] = []
    for i, obj in enumerate(raw_objects):
        if not isinstance(obj, dict):
            continue
        bbox = obj.get("bbox", {})
        if not isinstance(bbox, dict):
            bbox = {}
        detected.append(
            V2DetectedObject(
                object_id=str(obj.get("object_id", f"obj_{i}")),
                role=str(obj.get("role", "decorative")),
                bbox={
                    "x": int(bbox.get("x", 0)),
                    "y": int(bbox.get("y", 0)),
                    "width": int(bbox.get("width", 0)),
                    "height": int(bbox.get("height", 0)),
                },
                polygon=[
                    [int(pt[0]), int(pt[1])]
                    for pt in obj.get("polygon", [])
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2
                ],
                text_content=str(obj.get("text_content", "")),
                confidence=float(obj.get("confidence", 0.5)),
            )
        )

    print(
        f"[V2_GPT_ANALYSIS_END] jobId={job_id}"
        f" model={model_used!r}"
        f" rawObjects={len(raw_objects)}"
        f" parsedObjects={len(detected)}",
        flush=True,
    )

    return detected, model_used
