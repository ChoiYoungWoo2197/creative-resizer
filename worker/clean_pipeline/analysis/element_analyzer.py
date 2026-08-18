"""TYPE G P2: 트랙별 독립 GPT-4o 분석 + PIL crop + fal-ai/birefnet RGBA 추출.

기존 gpt4o_analyzer.py(TYPE B)는 수정하지 않는다.
TYPE G 전용 분석 모듈.

Flow:
  Product Track: GPT-4o(제품만) → bbox → PIL crop → birefnet → product_cutout.png (RGBA)
  Text Track:    GPT-4o(텍스트만) → text.json (RGBA 추출 없음)
  Person Track:  GPT-4o(인물만) → bbox → PIL crop → birefnet → person_cutout.png  (RGBA)

Outputs under output/{jobId}/clean_v1/02_element_analysis/:
  product_raw.json, text_raw.json, person_raw.json
  product_cutout.png (RGBA), person_cutout.png (RGBA)
  manifest.json (통합)

좌표계: GPT-4o → [ymin, xmin, ymax, xmax] 정규화 0~1000 → 실제 픽셀
        birefnet RGBA → Image.getbbox() → tight bbox
"""
from __future__ import annotations

import base64
import io
import json
import os
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Optional

from PIL import Image
from pydantic import BaseModel, Field

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.ELEMENT_ANALYSIS
_MODEL = "gpt-4.1"
_MAX_SIDE = 1600
_OUTPUT_SUBDIR = Path("clean_v1") / "02_element_analysis"
_FAL_BIREFNET = "fal-ai/birefnet"

# ── Pydantic 스키마 (Structured Outputs) ──────────────────────────────────────


class _ProductAnalysis(BaseModel):
    detected: bool = Field(description="이미지에 제품이 있으면 true")
    bbox_2d: list[int] = Field(
        description="[ymin, xmin, ymax, xmax] 0~1000 정규화 좌표. detected=false이면 [0,0,0,0]"
    )
    confidence: float = Field(description="0.0~1.0 감지 신뢰도")
    description: str = Field(description="제품 간단 설명")
    brand: str = Field(description="브랜드명. 없으면 빈 문자열")


class _TextItem(BaseModel):
    bbox_2d: list[int] = Field(description="[ymin, xmin, ymax, xmax] 0~1000 정규화 좌표")
    content: str = Field(description="실제 텍스트 내용")
    type: str = Field(description="headline / body / badge / logo 중 하나")
    language: str = Field(description="ko / en / mixed 중 하나")


class _TextAnalysis(BaseModel):
    detected: bool = Field(description="이미지에 텍스트가 있으면 true")
    items: list[_TextItem] = Field(description="감지된 텍스트 목록. 없으면 빈 배열")


class _PersonAnalysis(BaseModel):
    detected: bool = Field(description="이미지에 인물이 있으면 true")
    bbox_2d: list[int] = Field(
        description="[ymin, xmin, ymax, xmax] 0~1000 정규화 좌표. detected=false이면 [0,0,0,0]"
    )
    confidence: float = Field(description="0.0~1.0 감지 신뢰도")
    gender: str = Field(description="female / male / unknown 중 하나")
    age_range: str = Field(description="10s / 20s / 30s / 40s / unknown 중 하나")
    pose: str = Field(description="포즈 간단 설명")
    position: str = Field(description="left / center / right 중 하나")


# ── 시스템 프롬프트 ──────────────────────────────────────────────────────────

_PRODUCT_SYSTEM = (
    "You are a product detection specialist. "
    "Find ONLY the main product item (cosmetic container, bottle, package, etc). "
    "IGNORE: people, text overlays, background. "
    "Coordinates are [ymin, xmin, ymax, xmax] normalized 0~1000."
)
_PRODUCT_USER = (
    "Analyze this image. Return the main product's bounding box and metadata. "
    "If no product is found, return detected=false."
)

_TEXT_SYSTEM = (
    "You are a text detection specialist. "
    "Find ALL text elements: headlines, body copy, badges, promotional text, Korean/English characters. "
    "IGNORE: people, products, background objects. "
    "Coordinates are [ymin, xmin, ymax, xmax] normalized 0~1000."
)
_TEXT_USER = (
    "Analyze this image. Return all visible text elements with bounding boxes. "
    "If no text is found, return detected=false with empty items array."
)

_PERSON_SYSTEM = (
    "You are a person detection specialist. "
    "Find ONLY the human model/person in the image. "
    "IGNORE: products, text, background objects. "
    "Coordinates are [ymin, xmin, ymax, xmax] normalized 0~1000."
)
_PERSON_USER = (
    "Analyze this image. Return the person's bounding box and metadata. "
    "If no person is found, return detected=false."
)


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
) -> tuple[StageResult, dict | None]:
    """트랙별 독립 GPT-4o 분석 + birefnet RGBA 추출.

    Returns (StageResult, result_dict):
      result_dict = {
        "manifest_path": str,
        "product_cutout": str | None,
        "person_cutout":  str | None,
        "manifest": dict,
      }
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"element analysis — 3 tracks / model={_MODEL}",
        metrics={"imageWidth": image_width, "imageHeight": image_height, "model": _MODEL},
    )

    if not api_key:
        return _fail(logger, "NO_API_KEY", "OPENAI_API_KEY not set")

    # 이미지 공통 인코딩 (3 트랙 재사용)
    try:
        b64, scale = _encode_image(canonical_path)
    except Exception as exc:
        return _fail(logger, "IMAGE_ENCODE_FAILED", f"Cannot encode canonical: {exc}")

    fal_key = os.environ.get("FAL_KEY", "")

    manifest: dict = {
        "jobId": job_id,
        "sourceSha256": source_sha256,
        "imageWidth": image_width,
        "imageHeight": image_height,
        "model": _MODEL,
        "apiCallCount": 0,
        "tracks": {},
    }
    product_cutout: str | None = None
    person_cutout: str | None = None

    # ── Product Track ─────────────────────────────────────────────────────────
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
    except Exception as exc:
        return _fail(logger, "OPENAI_IMPORT_FAILED", f"OpenAI client 초기화 실패: {exc}")

    product_result = _analyze_product_track(
        client, b64, image_width, image_height, scale, stage_dir, logger
    )
    manifest["apiCallCount"] += 1
    manifest["tracks"]["product"] = product_result

    if product_result.get("detected") and fal_key:
        bbox_px = product_result.get("bbox_px")
        if bbox_px:
            product_cutout = _extract_rgba(
                canonical_path, bbox_px, stage_dir / "product_cutout.png",
                fal_key, "product", logger
            )
            product_result["cutout_path"] = product_cutout

    logger.artifact_written(
        STAGE.value, "(product-track)",
        f"detected={product_result.get('detected')} cutout={'yes' if product_cutout else 'no'}",
    )

    # ── Text Track ────────────────────────────────────────────────────────────
    text_result = _analyze_text_track(
        client, b64, image_width, image_height, scale, stage_dir, logger
    )
    manifest["apiCallCount"] += 1
    manifest["tracks"]["text"] = text_result

    logger.artifact_written(
        STAGE.value, "(text-track)",
        f"detected={text_result.get('detected')} items={len(text_result.get('items', []))}",
    )

    # ── Person Track ──────────────────────────────────────────────────────────
    person_result = _analyze_person_track(
        client, b64, image_width, image_height, scale, stage_dir, logger
    )
    manifest["apiCallCount"] += 1
    manifest["tracks"]["person"] = person_result

    if person_result.get("detected") and fal_key:
        bbox_px = person_result.get("bbox_px")
        if bbox_px:
            person_cutout = _extract_rgba(
                canonical_path, bbox_px, stage_dir / "person_cutout.png",
                fal_key, "person", logger
            )
            person_result["cutout_path"] = person_cutout

    logger.artifact_written(
        STAGE.value, "(person-track)",
        f"detected={person_result.get('detected')} cutout={'yes' if person_cutout else 'no'}",
    )

    # ── manifest.json 저장 ────────────────────────────────────────────────────
    manifest_path = stage_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.artifact_written(STAGE.value, str(manifest_path), "TYPE G manifest.json 저장")

    detected_tracks = [k for k, v in manifest["tracks"].items() if v.get("detected")]
    logger.stage_pass(
        STAGE.value,
        f"PASS — detected={detected_tracks} apiCallCount={manifest['apiCallCount']}",
        metrics={
            "apiCallCount": manifest["apiCallCount"],
            "detectedTracks": detected_tracks,
            "productCutout": bool(product_cutout),
            "personCutout": bool(person_cutout),
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "apiCallCount": manifest["apiCallCount"],
            "detectedTracks": detected_tracks,
        },
        artifacts={
            "manifest": str(manifest_path),
            **({"product_cutout": product_cutout} if product_cutout else {}),
            **({"person_cutout": person_cutout} if person_cutout else {}),
        },
    ), {
        "manifest_path": str(manifest_path),
        "product_cutout": product_cutout,
        "person_cutout": person_cutout,
        "manifest": manifest,
    }


# ── 트랙별 분석 ───────────────────────────────────────────────────────────────


def _analyze_product_track(
    client,
    b64: str,
    image_width: int,
    image_height: int,
    scale: float,
    stage_dir: Path,
    logger: PipelineLogger,
) -> dict:
    try:
        response = client.beta.chat.completions.parse(
            model=_MODEL,
            temperature=0.0,
            response_format=_ProductAnalysis,
            messages=[
                {"role": "system", "content": _PRODUCT_SYSTEM},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                    {"type": "text", "text": _PRODUCT_USER},
                ]},
            ],
        )
        parsed: _ProductAnalysis = response.choices[0].message.parsed
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(product-warn)", f"GPT-4o product track 실패: {exc}")
        return {"detected": False, "error": str(exc)}

    result = {"detected": parsed.detected, "confidence": parsed.confidence,
              "description": parsed.description, "brand": parsed.brand}

    if parsed.detected and len(parsed.bbox_2d) == 4:
        bbox_px = _norm_to_px(parsed.bbox_2d, image_width, image_height)
        result["bbox_px"] = bbox_px
        result["bbox_norm"] = parsed.bbox_2d

    (stage_dir / "product_raw.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _analyze_text_track(
    client,
    b64: str,
    image_width: int,
    image_height: int,
    scale: float,
    stage_dir: Path,
    logger: PipelineLogger,
) -> dict:
    try:
        response = client.beta.chat.completions.parse(
            model=_MODEL,
            temperature=0.0,
            response_format=_TextAnalysis,
            messages=[
                {"role": "system", "content": _TEXT_SYSTEM},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                    {"type": "text", "text": _TEXT_USER},
                ]},
            ],
        )
        parsed: _TextAnalysis = response.choices[0].message.parsed
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(text-warn)", f"GPT-4o text track 실패: {exc}")
        return {"detected": False, "items": [], "error": str(exc)}

    items = []
    for item in parsed.items:
        entry = {"content": item.content, "type": item.type, "language": item.language}
        if len(item.bbox_2d) == 4:
            entry["bbox_px"] = _norm_to_px(item.bbox_2d, image_width, image_height)
            entry["bbox_norm"] = item.bbox_2d
        items.append(entry)

    result = {"detected": parsed.detected, "items": items}
    (stage_dir / "text_raw.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _analyze_person_track(
    client,
    b64: str,
    image_width: int,
    image_height: int,
    scale: float,
    stage_dir: Path,
    logger: PipelineLogger,
) -> dict:
    try:
        response = client.beta.chat.completions.parse(
            model=_MODEL,
            temperature=0.0,
            response_format=_PersonAnalysis,
            messages=[
                {"role": "system", "content": _PERSON_SYSTEM},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                    {"type": "text", "text": _PERSON_USER},
                ]},
            ],
        )
        parsed: _PersonAnalysis = response.choices[0].message.parsed
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(person-warn)", f"GPT-4o person track 실패: {exc}")
        return {"detected": False, "error": str(exc)}

    result = {"detected": parsed.detected, "confidence": parsed.confidence,
              "gender": parsed.gender, "age_range": parsed.age_range,
              "pose": parsed.pose, "position": parsed.position}

    if parsed.detected and len(parsed.bbox_2d) == 4:
        bbox_px = _norm_to_px(parsed.bbox_2d, image_width, image_height)
        result["bbox_px"] = bbox_px
        result["bbox_norm"] = parsed.bbox_2d

    (stage_dir / "person_raw.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


# ── RGBA 추출 (PIL crop → birefnet) ───────────────────────────────────────────


def _extract_rgba(
    canonical_path: str,
    bbox_px: dict,
    output_path: Path,
    fal_key: str,
    track_name: str,
    logger: PipelineLogger,
) -> str | None:
    """bbox 영역 PIL crop → birefnet → RGBA PNG 저장. 실패 시 None."""
    try:
        img = Image.open(canonical_path).convert("RGB")
        x1, y1 = bbox_px["x"], bbox_px["y"]
        x2 = x1 + bbox_px["width"]
        y2 = y1 + bbox_px["height"]

        # 경계 clamp
        x1 = max(0, x1); y1 = max(0, y1)
        x2 = min(img.width, x2); y2 = min(img.height, y2)
        if x2 <= x1 or y2 <= y1:
            logger.artifact_written(STAGE.value, f"({track_name}-warn)", "bbox 영역이 유효하지 않음")
            return None

        crop = img.crop((x1, y1, x2, y2))
    except Exception as exc:
        logger.artifact_written(STAGE.value, f"({track_name}-warn)", f"PIL crop 실패: {exc}")
        return None

    try:
        rgba_img = _call_birefnet(crop, fal_key)
    except Exception as exc:
        logger.artifact_written(STAGE.value, f"({track_name}-warn)", f"birefnet 실패: {exc}")
        return None

    rgba_img.save(str(output_path))

    # tight bbox 계산 (불투명 픽셀만)
    tight = rgba_img.getbbox()
    logger.artifact_written(
        STAGE.value, str(output_path),
        f"{track_name} RGBA cutout saved tight_bbox={tight}",
    )
    return str(output_path)


def _call_birefnet(img: Image.Image, fal_key: str) -> Image.Image:
    """PIL Image → birefnet → RGBA PIL Image."""
    import fal_client
    os.environ.setdefault("FAL_KEY", fal_key)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    image_url = fal_client.upload(buf.read(), content_type="image/png")

    t0 = time.time()
    result = fal_client.subscribe(
        _FAL_BIREFNET,
        arguments={"image_url": image_url, "model": "General Use (Light)"},
        with_logs=False,
    )
    elapsed = round(time.time() - t0, 2)

    url = (result.get("image") or {}).get("url") or ""
    if not url:
        images = result.get("images") or []
        url = images[0].get("url", "") if images else ""
    if not url:
        raise RuntimeError(f"birefnet 응답에 URL 없음 ({elapsed}s)")

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=60, context=ctx) as resp:
        raw = resp.read()
    return Image.open(io.BytesIO(raw)).convert("RGBA")


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def _encode_image(canonical_path: str) -> tuple[str, float]:
    """canonical 이미지를 base64 JPEG로 인코딩. (b64_str, scale) 반환."""
    img = Image.open(canonical_path).convert("RGB")
    scale = 1.0
    if max(img.width, img.height) > _MAX_SIDE:
        scale = _MAX_SIDE / max(img.width, img.height)
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.LANCZOS,
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii"), scale


def _norm_to_px(bbox_2d: list[int], w: int, h: int) -> dict:
    """[ymin, xmin, ymax, xmax] 0~1000 → {x, y, width, height} 픽셀."""
    ymin, xmin, ymax, xmax = bbox_2d
    x1 = max(0, int(xmin / 1000 * w))
    y1 = max(0, int(ymin / 1000 * h))
    x2 = min(w, int(xmax / 1000 * w))
    y2 = min(h, int(ymax / 1000 * h))
    return {"x": x1, "y": y1, "width": max(1, x2 - x1), "height": max(1, y2 - y1)}


def _fail(logger: PipelineLogger, code: str, message: str) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
