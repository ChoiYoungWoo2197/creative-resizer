"""SCENE_ANALYSIS stage: one AI call on canonical.png → SceneManifest.

AI is called exactly once. No retries. No fallbacks.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from PIL import Image

from worker.clean_pipeline.analysis.manifest_builder import build as build_manifest
from worker.clean_pipeline.analysis.manifest_validator import validate as validate_manifest
from worker.clean_pipeline.analysis.models import SceneManifest
from worker.clean_pipeline.analysis.prompt import SYSTEM_PROMPT, USER_PROMPT
from worker.clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from worker.clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.SCENE_ANALYSIS
_MODEL = "o3"
_MAX_SIDE = 1600
_MAX_TOKENS = 4096
_OUTPUT_SUBDIR = Path("clean_v1") / "02_analysis"


def analyze(
    canonical_path: str,
    image_width: int,
    image_height: int,
    source_sha256: str,
    api_key: str,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, SceneManifest | None]:

    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"canonical={canonical_path} model={_MODEL}",
        metrics={"imageWidth": image_width, "imageHeight": image_height},
    )

    if not api_key:
        return _fail(logger, "NO_API_KEY", "OPENAI_API_KEY not set")

    # Encode canonical.png as base64 JPEG
    try:
        img = Image.open(canonical_path).convert("RGB")
        if max(img.width, img.height) > _MAX_SIDE:
            scale = _MAX_SIDE / max(img.width, img.height)
            img = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        return _fail(logger, "IMAGE_ENCODE_FAILED", f"Failed to encode canonical: {exc}")

    # AI call — exactly once
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
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                },
            ],
        )
        api_call_count = 1
    except Exception as exc:
        return _fail(logger, "API_ERROR", f"OpenAI call failed: {exc}")

    # Extract content
    raw_text = ""
    if response.choices:
        raw_text = response.choices[0].message.content or ""
    if not raw_text.strip():
        return _fail(logger, "EMPTY_RESPONSE", "AI returned empty content")

    # Save raw response
    raw_path = stage_dir / "raw_response.json"
    raw_path.write_text(
        json.dumps({"model": _MODEL, "content": raw_text}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.artifact_written(STAGE.value, str(raw_path), f"raw AI response apiCallCount={api_call_count}")

    # Parse JSON (strip markdown fences if present)
    json_text = _extract_json(raw_text)
    try:
        raw = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return _fail(logger, "PARSE_FAILED", f"JSON parse error: {exc} — raw: {raw_text[:200]}")

    # Build manifest
    manifest, build_err = build_manifest(raw, job_id, source_sha256, image_width, image_height)
    if manifest is None:
        return _fail(logger, "PARSE_FAILED", f"Manifest build failed: {build_err}")

    manifest.api_call_count = api_call_count

    # Validate manifest
    is_valid, fail_code, fail_reason = validate_manifest(manifest, image_width, image_height)
    if not is_valid:
        logger.stage_fail(STAGE.value, fail_code, fail_reason)
        return StageResult(
            stage=STAGE,
            status=PipelineStatus.FAIL,
            reasons=[f"[{fail_code}] {fail_reason}"],
        ), None

    # Save manifest
    manifest_path = stage_dir / "manifest.json"
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    logger.artifact_written(
        STAGE.value, str(manifest_path),
        f"{len(manifest.objects)} objects",
    )

    removable_count = sum(1 for o in manifest.objects if o.removable_from_scene)
    required_count = sum(1 for o in manifest.objects if o.required)

    logger.stage_pass(
        STAGE.value,
        f"{len(manifest.objects)} objects ({removable_count} removable, {required_count} required)",
        metrics={
            "objectCount": len(manifest.objects),
            "removableCount": removable_count,
            "requiredCount": required_count,
            "apiCallCount": api_call_count,
            "model": _MODEL,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "objectCount": len(manifest.objects),
            "removableCount": removable_count,
            "requiredCount": required_count,
            "apiCallCount": api_call_count,
        },
        artifacts={
            "manifest": str(manifest_path),
            "raw_response": str(raw_path),
        },
    ), manifest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (```json or ```) and last closing ```
        end = len(lines) - 1
        while end > 0 and lines[end].strip() == "```":
            end -= 1
        text = "\n".join(lines[1:end + 1])
    return text


def _fail(
    logger: PipelineLogger,
    code: str,
    message: str,
) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[message],
    ), None
