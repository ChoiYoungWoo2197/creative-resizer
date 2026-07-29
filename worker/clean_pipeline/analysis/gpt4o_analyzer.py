"""SCENE_ANALYSIS stage — TYPE B path: GPT-4o + Structured Outputs.

OpenAI 공식 가이드 기반:
  - model  : gpt-4o-2024-08-06  (Structured Outputs 공식 지원 최초 버전)
  - API    : client.beta.chat.completions.parse()  (strict=true 자동 적용)
  - 좌표계 : [ymin, xmin, ymax, xmax] 정규화 0~1000
             → ViT 패치 그리드가 상대 비율로 인식하기 때문에 실제 픽셀값보다 정확
  - temp   : 0.0  (좌표 일관성 극대화)

출력: SceneManifest (downstream P3~P8 변경 없이 호환)
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
from clean_pipeline.analysis.gpt4o_prompt import SYSTEM_PROMPT, USER_PROMPT
from clean_pipeline.analysis.manifest_validator import validate as validate_manifest
from clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject
from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.SCENE_ANALYSIS
_MODEL = "gpt-4o-2024-08-06"
_MAX_SIDE = 1600
_OUTPUT_SUBDIR = Path("clean_v1") / "02_analysis"

# GPT-4o category → (내부 role, required)
_ROLE_MAP: dict[str, tuple[str, bool]] = {
    "main_product":     ("product",     True),
    "brand_logo":       ("logo",        False),
    "advertising_text": ("title_group", True),
}


# ── Pydantic 스키마 (Structured Outputs — strict=true 자동 적용) ──────────────

class DetectedElement(BaseModel):
    category: str = Field(
        description="main_product, brand_logo, advertising_text 중 하나",
    )
    box_2d: list[int] = Field(
        description="[ymin, xmin, ymax, xmax] 형태의 0~1000 정규화 좌표 4개 정수",
    )
    text_content: Optional[str] = Field(
        default=None,
        description="category가 advertising_text일 경우 실제 텍스트 내용",
    )


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
        f"canonical={canonical_path} model={_MODEL} path=TYPE_B",
        metrics={"imageWidth": image_width, "imageHeight": image_height, "model": _MODEL},
    )

    if not api_key:
        return _fail(logger, "NO_API_KEY", "OPENAI_API_KEY not set")

    # ── 이미지 인코딩 ─────────────────────────────────────────────────────────
    try:
        _canonical_img = Image.open(canonical_path).convert("RGB")   # 후처리용 원본 보존
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
            temperature=0.0,                 # 좌표 일관성 극대화
            response_format=AdLayoutResponse,  # strict=true 자동 적용
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "high",  # 고해상도 패치 분석
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                },
            ],
        )
    except Exception as exc:
        return _fail(logger, "API_ERROR", f"GPT-4o Structured Outputs call failed: {exc}")

    # ── 응답 파싱 ─────────────────────────────────────────────────────────────
    parsed: AdLayoutResponse | None = response.choices[0].message.parsed
    if parsed is None:
        raw_refusal = response.choices[0].message.refusal or ""
        return _fail(logger, "PARSE_FAILED", f"GPT-4o schema refusal: {raw_refusal}")

    # raw 응답 저장 (디버깅용)
    raw_path = stage_dir / "raw_response.json"
    raw_path.write_text(
        json.dumps(
            {
                "model": _MODEL,
                "type": "TYPE_B_structured_outputs",
                "detected_elements": [
                    {
                        "category": e.category,
                        "box_2d": e.box_2d,
                        "text_content": e.text_content,
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
        f"GPT-4o structured response ({len(parsed.detected_elements)} elements)",
    )

    # ── 정규화 좌표 → SceneManifest 변환 ─────────────────────────────────────
    objects: list[SceneObject] = []

    for i, elem in enumerate(parsed.detected_elements):
        role, required = _ROLE_MAP.get(elem.category, ("decorative_ad", False))

        if len(elem.box_2d) != 4:
            logger.artifact_written(
                STAGE.value, "(skip)",
                f"obj_{i}: box_2d length={len(elem.box_2d)} (expected 4)",
            )
            continue

        ymin_n, xmin_n, ymax_n, xmax_n = elem.box_2d

        # 정규화 0~1000 → 실제 픽셀 좌표
        x1 = int(xmin_n / 1000 * image_width)
        y1 = int(ymin_n / 1000 * image_height)
        x2 = int(xmax_n / 1000 * image_width)
        y2 = int(ymax_n / 1000 * image_height)

        # title_group: GPT-4o가 넓은 텍스트의 좌측 경계를 과소평가하는 경향 보정.
        # Otsu ROI가 실제 텍스트보다 오른쪽에서 시작하면 좌측 글자가 crop 밖으로 유실됨.
        # 8% 확장(1200px 기준 ≈ 96px)으로 ROI가 실제 텍스트 시작점을 포함하도록 보장.
        if role == "title_group":
            x1 = max(0, x1 - int(0.08 * image_width))

        # 이미지 경계 내로 clamp
        x1 = max(0, min(x1, image_width - 1))
        y1 = max(0, min(y1, image_height - 1))
        x2 = max(x1 + 1, min(x2, image_width))
        y2 = max(y1 + 1, min(y2, image_height))

        # 픽셀 단위 후처리: Canny/Otsu로 좌표 정밀화 (cv2 없으면 원본 유지)
        x1, y1, x2, y2 = refine_bbox(_canonical_img, x1, y1, x2, y2, role)
        w, h = x2 - x1, y2 - y1

        # bbox → 직사각형 polygon (4점)
        polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

        objects.append(SceneObject(
            id=f"obj_{i}",
            role=role,
            required=required,
            movable=True,
            removable_from_scene=True,
            bbox=BBox(x=x1, y=y1, width=w, height=h),
            polygon=polygon,
            confidence=0.9,
            group_id=None,
            z_index=i,
            text_content=elem.text_content or "",
            description=f"{elem.category} detected by {_MODEL}",
        ))

    if not objects:
        return _fail(logger, "NO_OBJECTS_DETECTED", "GPT-4o detected no objects in the image")

    manifest = SceneManifest(
        job_id=job_id,
        source_sha256=source_sha256,
        image_width=image_width,
        image_height=image_height,
        model=_MODEL,
        api_call_count=1,
        objects=objects,
    )

    # 기존 validator 통과 (polygon 필수, 좌표 범위 등)
    is_valid, fail_code, fail_reason = validate_manifest(manifest, image_width, image_height)
    if not is_valid:
        logger.stage_fail(STAGE.value, fail_code, fail_reason)
        return StageResult(
            stage=STAGE,
            status=PipelineStatus.FAIL,
            reasons=[f"[{fail_code}] {fail_reason}"],
        ), None

    # manifest.json 저장
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
        f"{len(objects)} objects [{_MODEL} TYPE B]",
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


# ── Helper ────────────────────────────────────────────────────────────────────

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
