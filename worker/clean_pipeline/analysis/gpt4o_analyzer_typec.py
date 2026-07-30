"""TYPE C SCENE_ANALYSIS — 계층적 텍스트 그룹화 + person_zone + decorative_panel.

TYPE B gpt4o_analyzer와 동일한 구조이나:
- DetectedElement에 group_name 필드 추가
- advertising_text를 의미 그룹별로 여러 개 허용 (main_copy / benefit / sub_copy)
- group_name → SceneObject.group_id 에 저장
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Optional

from PIL import Image
from pydantic import BaseModel, Field

from clean_pipeline.analysis.bbox_refiner import refine as refine_bbox
from clean_pipeline.analysis.gpt4o_prompt_typec import SYSTEM_PROMPT, USER_PROMPT
from clean_pipeline.analysis.manifest_validator import validate as validate_manifest
from clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject
from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.SCENE_ANALYSIS
_MODEL = "gpt-4o-2024-08-06"
_MAX_SIDE = 1600
_OUTPUT_SUBDIR = Path("clean_v1") / "02_analysis"

_ROLE_MAP: dict[str, tuple[str, bool]] = {
    "main_product":      ("product",          True),
    "brand_logo":        ("logo",             False),
    "advertising_text":  ("title_group",      True),
    "person_zone":       ("protected_subject", False),
    "decorative_panel":  ("decorative_ad",    False),
}


# ── Pydantic 스키마 ────────────────────────────────────────────────────────────

class DetectedElement(BaseModel):
    category: str = Field(description="main_product, brand_logo, advertising_text, person_zone, decorative_panel 중 하나")
    box_2d: list[int] = Field(description="[ymin, xmin, ymax, xmax] 0~1000 정규화 좌표")
    text_content: Optional[str] = Field(default=None, description="advertising_text의 실제 텍스트 내용")
    group_name: Optional[str] = Field(default=None, description="advertising_text 그룹명: main_copy, benefit, sub_copy")


class AdLayoutResponse(BaseModel):
    detected_elements: list[DetectedElement]


# ── 공개 API ──────────────────────────────────────────────────────────────────

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
        f"canonical={canonical_path} model={_MODEL} path=TYPE_C",
        metrics={"imageWidth": image_width, "imageHeight": image_height, "model": _MODEL},
    )

    if not api_key:
        return _fail(logger, "NO_API_KEY", "OPENAI_API_KEY not set")

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

    # ── GPT-4o Structured Outputs 호출 ───────────────────────────────────────
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.beta.chat.completions.parse(
            model=_MODEL,
            temperature=0.0,
            response_format=AdLayoutResponse,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                },
            ],
        )
    except Exception as exc:
        return _fail(logger, "API_ERROR", f"GPT-4o call failed: {exc}")

    parsed: AdLayoutResponse | None = response.choices[0].message.parsed
    if parsed is None:
        raw_refusal = response.choices[0].message.refusal or ""
        return _fail(logger, "PARSE_FAILED", f"GPT-4o schema refusal: {raw_refusal}")

    # raw 응답 저장
    raw_path = stage_dir / "raw_response.json"
    raw_path.write_text(
        json.dumps(
            {
                "model": _MODEL,
                "type": "TYPE_C_structured_outputs",
                "detected_elements": [
                    {
                        "category": e.category,
                        "box_2d": e.box_2d,
                        "text_content": e.text_content,
                        "group_name": e.group_name,
                    }
                    for e in parsed.detected_elements
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.artifact_written(
        STAGE.value, str(raw_path),
        f"TYPE C GPT-4o response ({len(parsed.detected_elements)} elements)",
    )

    # ── 정규화 좌표 → SceneManifest 변환 ─────────────────────────────────────
    objects: list[SceneObject] = []
    text_group_counter: dict[str, int] = {}  # group_name 중복 시 suffix

    for i, elem in enumerate(parsed.detected_elements):
        role, required = _ROLE_MAP.get(elem.category, ("decorative_ad", False))

        if len(elem.box_2d) != 4:
            continue

        ymin_n, xmin_n, ymax_n, xmax_n = elem.box_2d
        x1 = int(xmin_n / 1000 * image_width)
        y1 = int(ymin_n / 1000 * image_height)
        x2 = int(xmax_n / 1000 * image_width)
        y2 = int(ymax_n / 1000 * image_height)

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

        # group_name → group_id (advertising_text 계층 구분)
        group_id: Optional[str] = None
        obj_id = f"obj_{i}"
        if elem.category == "advertising_text" and elem.group_name:
            group_id = elem.group_name
            # 같은 group_name이 여러 개면 suffix 붙임
            cnt = text_group_counter.get(elem.group_name, 0)
            text_group_counter[elem.group_name] = cnt + 1
            if cnt > 0:
                group_id = f"{elem.group_name}_{cnt}"

        objects.append(SceneObject(
            id=obj_id,
            role=role,
            required=required,
            movable=not is_protected,
            removable_from_scene=not is_protected,
            bbox=BBox(x=x1, y=y1, width=w, height=h),
            polygon=polygon,
            confidence=0.9,
            group_id=group_id,
            z_index=i,
            text_content=elem.text_content or "",
            description=f"{elem.category}{'[' + group_id + ']' if group_id else ''} by {_MODEL}",
        ))

    if not objects:
        return _fail(logger, "NO_OBJECTS_DETECTED", "GPT-4o detected no objects")

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
            stage=STAGE, status=PipelineStatus.FAIL,
            reasons=[f"[{fail_code}] {fail_reason}"],
        ), None

    manifest_path = stage_dir / "manifest.json"
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    text_groups = [o for o in objects if o.role == "title_group"]
    removable_count = sum(1 for o in objects if o.removable_from_scene)

    logger.artifact_written(
        STAGE.value, str(manifest_path),
        f"{len(objects)} objects ({len(text_groups)} text groups, {removable_count} removable)",
    )

    group_summary = ", ".join(
        f"{o.group_id or o.role}({o.bbox.width}×{o.bbox.height})" for o in objects
    )
    logger.stage_pass(
        STAGE.value,
        f"TYPE C: {len(objects)} objects [{group_summary}]",
        metrics={
            "objectCount": len(objects),
            "textGroupCount": len(text_groups),
            "removableCount": removable_count,
            "apiCallCount": 1,
        },
    )

    return StageResult(
        stage=STAGE, status=PipelineStatus.PASS,
        metrics={"objectCount": len(objects), "textGroupCount": len(text_groups)},
        artifacts={"manifest": str(manifest_path), "raw_response": str(raw_path)},
    ), manifest


def _fail(logger, code, message):
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(stage=STAGE, status=PipelineStatus.FAIL, reasons=[message]), None
