"""SCENE_ANALYSIS stage — TYPE CLAUDE path: Claude Vision + Tool Use.

gpt4o_analyzer.py와 동일한 입출력 스펙:
  - 함수 시그니처: analyze(canonical_path, image_width, image_height,
                          source_sha256, api_key, output_dir, job_id, logger)
  - 출력: SceneManifest (manifest.json)
  - box_2d: [ymin, xmin, ymax, xmax] 정규화 0~1000
  - downstream(P3~P8) 완전 호환

내부 API만 Anthropic SDK + Tool Use 방식으로 교체.
api_key 파라미터는 사용되지 않음 — ANTHROPIC_API_KEY 환경변수를 사용.
활성화: CLEAN_PIPELINE_TYPE=CLAUDE
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

from PIL import Image

from clean_pipeline.analysis.bbox_refiner import refine as refine_bbox
from clean_pipeline.analysis.gpt4o_prompt import SYSTEM_PROMPT, USER_PROMPT
from clean_pipeline.analysis.manifest_validator import validate as validate_manifest
from clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject
from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.SCENE_ANALYSIS
_MODEL = "claude-sonnet-5"
_MAX_SIDE = 1600
_MAX_TOKENS = 4096
_OUTPUT_SUBDIR = Path("clean_v1") / "02_analysis"

# gpt4o_analyzer.py와 동일한 role 매핑
_ROLE_MAP: dict[str, tuple[str, bool]] = {
    "main_product":      ("product",           True),
    "brand_logo":        ("logo",              False),
    "advertising_text":  ("title_group",       True),
    "person_zone":       ("protected_subject", False),
    "decorative_panel":  ("decorative_ad",     False),
}

# gpt4o_analyzer의 AdLayoutResponse와 동일한 구조를 Tool Use 스키마로 정의
_TOOL_NAME = "report_ad_layout"
_TOOL: dict = {
    "name": _TOOL_NAME,
    "description": "광고 이미지에서 감지한 요소들의 바운딩 박스를 보고한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "detected_elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": [
                                "main_product",
                                "brand_logo",
                                "advertising_text",
                                "person_zone",
                                "decorative_panel",
                            ],
                        },
                        "box_2d": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "[ymin, xmin, ymax, xmax] 0~1000 정규화 좌표",
                        },
                        "text_content": {
                            "type": ["string", "null"],
                            "description": "advertising_text인 경우 실제 텍스트 내용",
                        },
                    },
                    "required": ["category", "box_2d"],
                },
            }
        },
        "required": ["detected_elements"],
    },
}


# ── 공개 API ──────────────────────────────────────────────────────────────────


def analyze(
    canonical_path: str,
    image_width: int,
    image_height: int,
    source_sha256: str,
    api_key: str,  # noqa: ARG001 — OpenAI key, 사용 안 함. ANTHROPIC_API_KEY env 사용.
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, SceneManifest | None]:

    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"canonical={canonical_path} model={_MODEL} path=CLAUDE",
        metrics={"imageWidth": image_width, "imageHeight": image_height, "model": _MODEL},
    )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        return _fail(logger, "NO_API_KEY", "ANTHROPIC_API_KEY not set")

    # ── 이미지 인코딩 ─────────────────────────────────────────────────────────
    try:
        _canonical_img = Image.open(canonical_path).convert("RGB")
        img = _canonical_img
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

    # ── Anthropic Tool Use 호출 ───────────────────────────────────────────────
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[_TOOL],
            tool_choice={"type": "any"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                }
            ],
        )
    except Exception as exc:
        return _fail(logger, "API_ERROR", f"Anthropic Tool Use call failed: {exc}")

    # ── Tool Use 응답 추출 ─────────────────────────────────────────────────────
    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        return _fail(logger, "PARSE_FAILED", "Claude returned no tool_use block")

    raw_input: dict = tool_block.input
    detected_elements: list[dict] = raw_input.get("detected_elements", [])

    # raw 응답 저장 (디버깅용)
    raw_path = stage_dir / "raw_response.json"
    raw_path.write_text(
        json.dumps(
            {
                "model": _MODEL,
                "type": "CLAUDE_tool_use",
                "detected_elements": detected_elements,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.artifact_written(
        STAGE.value, str(raw_path),
        f"Claude tool_use response ({len(detected_elements)} elements)",
    )

    # ── 정규화 좌표 → SceneManifest 변환 (gpt4o_analyzer.py와 동일) ──────────
    objects: list[SceneObject] = []

    for i, elem in enumerate(detected_elements):
        category = elem.get("category", "")
        role, required = _ROLE_MAP.get(category, ("decorative_ad", False))

        box_2d = elem.get("box_2d", [])
        if len(box_2d) != 4:
            logger.artifact_written(
                STAGE.value, "(skip)",
                f"obj_{i}: box_2d length={len(box_2d)} (expected 4)",
            )
            continue

        ymin_n, xmin_n, ymax_n, xmax_n = box_2d

        x1 = int(xmin_n / 1000 * image_width)
        y1 = int(ymin_n / 1000 * image_height)
        x2 = int(xmax_n / 1000 * image_width)
        y2 = int(ymax_n / 1000 * image_height)

        # title_group: 좌측 8% 확장 (gpt4o_analyzer.py와 동일)
        if role == "title_group":
            x1 = max(0, x1 - int(0.08 * image_width))

        x1 = max(0, min(x1, image_width - 1))
        y1 = max(0, min(y1, image_height - 1))
        x2 = max(x1 + 1, min(x2, image_width))
        y2 = max(y1 + 1, min(y2, image_height))

        x1, y1, x2, y2 = refine_bbox(_canonical_img, x1, y1, x2, y2, role)
        w, h = x2 - x1, y2 - y1

        polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

        is_protected = role == "protected_subject"
        objects.append(SceneObject(
            id=f"obj_{i}",
            role=role,
            required=required,
            movable=not is_protected,
            removable_from_scene=not is_protected,
            bbox=BBox(x=x1, y=y1, width=w, height=h),
            polygon=polygon,
            confidence=0.9,
            group_id=None,
            z_index=i,
            text_content=elem.get("text_content") or "",
            description=f"{category} detected by {_MODEL}",
        ))

    if not objects:
        return _fail(logger, "NO_OBJECTS_DETECTED", "Claude detected no objects in the image")

    manifest = SceneManifest(
        job_id=job_id,
        source_sha256=source_sha256,
        image_width=image_width,
        image_height=image_height,
        model=_MODEL,
        api_call_count=1,
        objects=objects,
    )

    is_valid, fail_code, fail_reason = validate_manifest(manifest, image_width, image_height)
    if not is_valid:
        logger.stage_fail(STAGE.value, fail_code, fail_reason)
        return StageResult(
            stage=STAGE,
            status=PipelineStatus.FAIL,
            reasons=[f"[{fail_code}] {fail_reason}"],
        ), None

    manifest_path = stage_dir / "manifest.json"
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    removable_count = sum(1 for o in objects if o.removable_from_scene)
    required_count = sum(1 for o in objects if o.required)

    logger.artifact_written(
        STAGE.value, str(manifest_path),
        f"{len(objects)} objects ({removable_count} removable, {required_count} required)",
    )
    logger.stage_pass(
        STAGE.value,
        f"{len(objects)} objects [{_MODEL} CLAUDE]",
        metrics={
            "objectCount": len(objects),
            "removableCount": removable_count,
            "requiredCount": required_count,
            "apiCallCount": 1,
            "model": _MODEL,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "objectCount": len(objects),
            "removableCount": removable_count,
            "requiredCount": required_count,
            "apiCallCount": 1,
        },
        artifacts={
            "manifest": str(manifest_path),
            "raw_response": str(raw_path),
        },
    ), manifest


# ── Helpers ───────────────────────────────────────────────────────────────────


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
