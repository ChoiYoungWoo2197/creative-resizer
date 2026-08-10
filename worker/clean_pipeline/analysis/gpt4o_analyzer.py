"""SCENE_ANALYSIS stage — TYPE B path: GPT-4.1 + Structured Outputs.

OpenAI 공식 가이드 기반:
  - model  : gpt-4.1  (Structured Outputs 지원)
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

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from clean_pipeline.analysis.bbox_refiner import refine as refine_bbox, scan_product_bottom_1d
from clean_pipeline.analysis.gpt4o_prompt import SYSTEM_PROMPT, USER_PROMPT
from clean_pipeline.analysis.manifest_validator import validate as validate_manifest
from clean_pipeline.analysis.models import BBox, SceneManifest, SceneObject
from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.SCENE_ANALYSIS
_MODEL = "gpt-4.1"
_MAX_SIDE = 1600
_OUTPUT_SUBDIR = Path("clean_v1") / "02_analysis"

# GPT role → (내부 role, required)
# logo/badge는 매핑 없음 → 감지돼도 skip
_ROLE_MAP: dict[str, tuple[str, bool]] = {
    "product":           ("product",          True),
    "sub_product":       ("product",          False),
    "title_group":       ("title_group",      True),
    "person_zone":       ("protected_subject", False),
    "protected_subject": ("protected_subject", False),
    "decorative_panel":  ("decorative_ad",    False),
}

# role별 z_index — 낮을수록 먼저 합성(배경 쪽), 높을수록 나중(전경 쪽)
# title_group/logo 텍스트 레이어는 product 위에 표시되어야 함
_ROLE_Z_INDEX: dict[str, int] = {
    "product":           0,
    "protected_subject": 0,
    "decorative_ad":     0,
    "logo":              5,
    "title_group":      10,
}

# inpaint_mask 대상 역할 및 padding
_INPAINT_MASK_ROLES: frozenset[str] = frozenset({"title_group", "logo"})
_INPAINT_PAD: int = 10


# ── Pydantic 스키마 (Structured Outputs — strict=true 자동 적용) ──────────────

class DetectedElement(BaseModel):
    role: str = Field(
        description="product, sub_product, title_group, badge, person_zone 중 하나",
    )
    bbox_2d: list[int] = Field(
        description="[ymin, xmin, ymax, xmax] 형태의 0~1000 정규화 좌표 4개 정수",
    )
    text_content: Optional[str] = Field(
        default=None,
        description="role이 title_group/badge일 경우 실제 텍스트 내용",
    )


class AdLayoutResponse(BaseModel):
    reasoning_summary: str = Field(
        description="Phase 1~3 CoT 분석 요약",
    )
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
                "reasoning_summary": parsed.reasoning_summary,
                "detected_elements": [
                    {
                        "role": e.role,
                        "bbox_2d": e.bbox_2d,
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
    for line in parsed.reasoning_summary.split("\n"):
        line = line.strip()
        if line:
            logger.artifact_written(STAGE.value, "(reasoning)", line)

    # ── 정규화 좌표 → SceneManifest 변환 ─────────────────────────────────────
    objects: list[SceneObject] = []

    for i, elem in enumerate(parsed.detected_elements):
        if elem.role not in _ROLE_MAP:
            logger.artifact_written(
                STAGE.value, "(skip)",
                f"obj_{i}: role='{elem.role}' not in _ROLE_MAP — skip",
            )
            continue

        role, required = _ROLE_MAP[elem.role]

        if len(elem.bbox_2d) != 4:
            logger.artifact_written(
                STAGE.value, "(skip)",
                f"obj_{i}: bbox_2d length={len(elem.bbox_2d)} (expected 4)",
            )
            continue

        ymin_n, xmin_n, ymax_n, xmax_n = elem.bbox_2d

        # Role Hierarchy 할당 로그 — GPT role → 내부 role 매핑 + 정규화 좌표
        text_hint = f' text="{elem.text_content[:20]}"' if elem.text_content else ""
        logger.artifact_written(
            STAGE.value, "(role-hierarchy)",
            f"[Role] obj_{i}: {elem.role} → {role}{text_hint} "
            f"bbox_norm=({ymin_n},{xmin_n},{ymax_n},{xmax_n})",
        )

        # 정규화 0~1000 → 실제 픽셀 좌표
        x1 = int(xmin_n / 1000 * image_width)
        y1 = int(ymin_n / 1000 * image_height)
        x2 = int(xmax_n / 1000 * image_width)
        y2 = int(ymax_n / 1000 * image_height)

        # 이미지 경계 내로 clamp
        x1 = max(0, min(x1, image_width - 1))
        y1 = max(0, min(y1, image_height - 1))
        x2 = max(x1 + 1, min(x2, image_width))
        y2 = max(y1 + 1, min(y2, image_height))

        # product만 Canny로 정밀화 (SAM2 crop bbox 품질). title_group/logo는 lama 마스크로 처리.
        if role == "product":
            x1, y1, x2, y2 = refine_bbox(_canonical_img, x1, y1, x2, y2, role)
        w, h = x2 - x1, y2 - y1

        # bbox → 직사각형 polygon (4점)
        polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

        # protected_subject(person_zone): 제거·이동 대상이 아니라 충돌 회피용 참조 영역
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
            z_index=_ROLE_Z_INDEX.get(role, i),
            text_content=elem.text_content or "",
            description=f"{elem.role} detected by {_MODEL}",
        ))

    # 픽셀 스캔 후처리: product y2를 실제 용기 바닥까지 확장.
    # GPT는 용기 표면 라벨 텍스트 하단을 용기 끝으로 오인하므로
    # 어두운 배경에서 하향 스캔으로 실제 바닥을 확정한다.
    product_obj = next((o for o in objects if o.role == "product"), None)
    if product_obj is not None:
        old_y2 = product_obj.bbox.y + product_obj.bbox.height
        new_y2 = scan_product_bottom_1d(
            _canonical_img,
            product_obj.bbox.x,
            product_obj.bbox.y,
            product_obj.bbox.x + product_obj.bbox.width,
            old_y2,
            image_height,
        )
        if new_y2 > old_y2:
            px1 = product_obj.bbox.x
            py1 = product_obj.bbox.y
            px2 = px1 + product_obj.bbox.width
            product_obj.bbox.height = new_y2 - py1
            product_obj.polygon = [[px1, py1], [px2, py1], [px2, new_y2], [px1, new_y2]]
            logger.artifact_written(
                STAGE.value, "(pixel-scan)",
                f"product y2 extended {old_y2} → {new_y2} (+{new_y2 - old_y2}px)",
            )

    if not objects:
        return _fail(logger, "NO_OBJECTS_DETECTED", "GPT-4o detected no objects in the image")

    objects = _filter_product_overlapping_titles(objects, logger)

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

    # inpaint_mask.png: title_group / logo bbox (+_INPAINT_PAD px) → 흰색 마스크
    # Bbox 범위만 마스킹 (y2를 하단 전체로 과도하게 확장하지 않음)
    inpaint_mask = Image.new("L", (image_width, image_height), 0)
    _idraw = ImageDraw.Draw(inpaint_mask)
    inpaint_targets: list[str] = []
    for obj in objects:
        if obj.role in _INPAINT_MASK_ROLES:
            mx1 = max(0, obj.bbox.x - _INPAINT_PAD)
            my1 = max(0, obj.bbox.y - _INPAINT_PAD)
            mx2 = min(image_width,  obj.bbox.x + obj.bbox.width  + _INPAINT_PAD)
            my2 = min(image_height, obj.bbox.y + obj.bbox.height + _INPAINT_PAD)
            _idraw.rectangle([mx1, my1, mx2, my2], fill=255)
            inpaint_targets.append(f"{obj.role}({obj.id})")
    inpaint_mask_path = stage_dir / "inpaint_mask.png"
    inpaint_mask.save(str(inpaint_mask_path))
    logger.artifact_written(
        STAGE.value, str(inpaint_mask_path),
        f"inpaint mask targets={inpaint_targets} pad={_INPAINT_PAD}px",
    )

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
            "inpaint_mask": str(inpaint_mask_path),
        },
    ), manifest


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


def _filter_product_overlapping_titles(
    objects: list[SceneObject],
    logger: PipelineLogger,
) -> list[SceneObject]:
    """title_group/logo가 product bbox 안에 60% 이상 포함되면 제거 (guardrail)."""
    product_objs = [o for o in objects if o.role == "product"]
    if not product_objs:
        return objects
    filtered = []
    for obj in objects:
        if obj.role in {"title_group", "logo"}:
            removed = False
            for prod in product_objs:
                ix1 = max(obj.bbox.x, prod.bbox.x)
                iy1 = max(obj.bbox.y, prod.bbox.y)
                ix2 = min(obj.bbox.x + obj.bbox.width, prod.bbox.x + prod.bbox.width)
                iy2 = min(obj.bbox.y + obj.bbox.height, prod.bbox.y + prod.bbox.height)
                if ix2 > ix1 and iy2 > iy1:
                    inter = (ix2 - ix1) * (iy2 - iy1)
                    obj_area = obj.bbox.width * obj.bbox.height
                    if obj_area > 0 and inter / obj_area > 0.6:
                        logger.artifact_written(
                            STAGE.value, "(guardrail)",
                            f"[P2 Guardrail] removed {obj.role} {obj.id} — "
                            f"{inter / obj_area:.0%} inside product bbox",
                        )
                        removed = True
                        break
            if not removed:
                filtered.append(obj)
        else:
            filtered.append(obj)
    return filtered
