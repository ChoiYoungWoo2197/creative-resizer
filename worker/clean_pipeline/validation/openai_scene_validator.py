"""OpenAI GPT-4o scene validation — exactly 1 API call.

Sends three images (canonical, source_projection, scene_plate) plus the manifest
in one user message and parses the JSON response.
"""
from __future__ import annotations

import base64
import io
import json

from PIL import Image

from clean_pipeline.analysis.models import SceneManifest
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.validation.scene_models import AIValidationResult

_MODEL = "gpt-4o"
_MAX_TOKENS = 1024  # JSON response is small; gpt-4o does not use reasoning tokens
_MAX_SIDE = 1024

SYSTEM_PROMPT = (
    "You are an expert advertising-image quality inspector.\n"
    "You receive three images in this exact order:\n"
    "  1. CANONICAL — the original advertisement source image\n"
    "  2. SOURCE_PROJECTION — the canonical scaled to fit the target canvas\n"
    "  3. SCENE_PLATE — the cleaned scene with advertising objects removed\n\n"
    "You also receive a scene manifest that classifies each object by role.\n\n"
    "ROLE DEFINITIONS (critical — read carefully):\n"
    "  protected_subject — person, model, or mascot that is part of the NATURAL SCENE.\n"
    "    These MUST remain visible in SCENE_PLATE. Do NOT list them in remainingObjectIds.\n"
    "    Do NOT count them as ad objects remaining.\n"
    "  product / title_group / logo / decorative_ad / text_overlay — advertising elements.\n"
    "    These must be ABSENT from SCENE_PLATE.\n\n"
    "Inspect SCENE_PLATE carefully, comparing it against CANONICAL and SOURCE_PROJECTION.\n"
    "Respond with ONLY a valid JSON object — no markdown fences, no explanation — "
    "containing these exact fields:\n"
    "  adObjectsRemaining      (bool)         — true only if a non-protected AD element is still visible\n"
    "  remainingObjectIds      (list[string]) — IDs of visible AD objects only; NEVER include protected_subject IDs\n"
    "  protectedSubjectPreserved (bool)       — true if protected_subject objects are intact in SCENE_PLATE\n"
    "  visibleSeamDetected     (bool)         — true if obvious edit seams are visible\n"
    "  duplicatedFragmentsDetected (bool)     — true if any region appears duplicated/mirrored\n"
    "  newlyGeneratedTextDetected  (bool)     — true if new text appeared that was not in CANONICAL\n"
    "  sceneNaturalnessScore   (float 0-1)    — how natural the cleaned scene looks\n"
    "  pass                    (bool)         — overall verdict\n"
    "  reasons                 (list[string]) — brief justification"
)


def _encode(path: str) -> str:
    img = Image.open(path).convert("RGB")
    if max(img.width, img.height) > _MAX_SIDE:
        scale = _MAX_SIDE / max(img.width, img.height)
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.LANCZOS,
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _manifest_summary(manifest: SceneManifest) -> str:
    protected = [o for o in manifest.objects if o.role == "protected_subject"]
    ad_objects = [o for o in manifest.objects if o.role != "protected_subject"]

    lines = [f"imageSize: {manifest.image_width}×{manifest.image_height}"]

    lines.append("\nAD OBJECTS TO REMOVE (must be absent from SCENE_PLATE):")
    if ad_objects:
        for o in ad_objects:
            lines.append(f"  id={o.id}  role={o.role}  desc=\"{o.description}\"")
    else:
        lines.append("  (none)")

    lines.append("\nPROTECTED SUBJECTS (must remain — never flag as ad objects):")
    if protected:
        for o in protected:
            lines.append(f"  id={o.id}  role={o.role}  desc=\"{o.description}\"")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


def _parse(text: str) -> tuple[AIValidationResult | None, str, str]:
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        end = len(lines) - 1
        while end > 0 and lines[end].strip() == "```":
            end -= 1
        raw = "\n".join(lines[1: end + 1])
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, "AI_RESPONSE_PARSE_FAILED", f"JSON parse error: {exc} — raw: {raw[:200]}"

    try:
        return AIValidationResult(
            ad_objects_remaining=bool(d.get("adObjectsRemaining", False)),
            remaining_object_ids=list(d.get("remainingObjectIds", [])),
            protected_subject_preserved=bool(d.get("protectedSubjectPreserved", True)),
            visible_seam_detected=bool(d.get("visibleSeamDetected", False)),
            duplicated_fragments_detected=bool(d.get("duplicatedFragmentsDetected", False)),
            newly_generated_text_detected=bool(d.get("newlyGeneratedTextDetected", False)),
            scene_naturalness_score=float(d.get("sceneNaturalnessScore", 1.0)),
            passed=bool(d.get("pass", True)),
            reasons=list(d.get("reasons", [])),
            api_call_count=1,
        ), "", ""
    except Exception as exc:
        return None, "AI_RESPONSE_PARSE_FAILED", f"Field extraction failed: {exc}"


def validate(
    canonical_path: str,
    source_projection_path: str,
    scene_plate_path: str,
    manifest: SceneManifest,
    api_key: str,
    logger: PipelineLogger,
    stage: str = "SCENE_VALIDATION",
) -> tuple[AIValidationResult | None, str, str]:
    """Call GPT-4o exactly once with all three images.

    Returns (AIValidationResult, "", "") on success.
    Returns (None, fail_code, fail_reason) on error.
    """
    if not api_key:
        return None, "NO_API_KEY", "OPENAI_API_KEY not set"

    try:
        b64_canonical = _encode(canonical_path)
        b64_projection = _encode(source_projection_path)
        b64_plate = _encode(scene_plate_path)
    except Exception as exc:
        return None, "IMAGE_ENCODE_FAILED", f"Failed to encode images: {exc}"

    manifest_text = _manifest_summary(manifest)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=_MODEL,
            max_completion_tokens=_MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"SCENE MANIFEST:\n{manifest_text}\n\n"
                                                 "Evaluate the following three images in order:"},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64_canonical}", "detail": "high"}},
                        {"type": "text", "text": "[1] CANONICAL — original advertisement"},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64_projection}", "detail": "high"}},
                        {"type": "text", "text": "[2] SOURCE_PROJECTION — canonical on target canvas"},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64_plate}", "detail": "high"}},
                        {"type": "text", "text": "[3] SCENE_PLATE — cleaned result to evaluate"},
                    ],
                },
            ],
        )
    except Exception as exc:
        return None, "API_ERROR", f"AI call failed: {exc}"

    raw_text = ""
    if response.choices:
        raw_text = response.choices[0].message.content or ""
    if not raw_text.strip():
        return None, "EMPTY_RESPONSE", "AI returned empty content"

    logger.artifact_written(stage, "(memory) ai_validation_raw", "apiCallCount=1")

    return _parse(raw_text)
