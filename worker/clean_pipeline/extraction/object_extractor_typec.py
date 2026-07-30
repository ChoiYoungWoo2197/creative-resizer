"""TYPE C OBJECT_EXTRACTION — SAM2 픽셀 정밀 마스크 + polygon 폴백.

TYPE B object_extractor와 동일한 출력 구조이나 P3에서:
- product / logo 역할: SAM2 서비스로 픽셀 정밀 마스크 취득 시도
- SAM2 미기동·IoU 미달·품질 미달 → polygon 마스크로 폴백 (에러 없이 진행)
- title_group / protected_subject / decorative_ad 역할: 기존과 동일

폴백 여부는 meta JSON의 "maskSource" 필드로 구분:
  "sam2"    — SAM2 서비스 성공
  "polygon" — 폴백
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
from PIL import Image

from clean_pipeline.analysis.models import SceneManifest, SceneObject
from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.extraction.bg_removal import remove_background, should_remove_background
from clean_pipeline.extraction.models import (
    ExtractedObject,
    ExtractionResult,
    ProtectedMask,
    SourceBBox,
)
from clean_pipeline.extraction.polygon_mask import bounding_rect, rasterize_polygon
from clean_pipeline.extraction.sam2_client import best_match, query_sam2
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.typec_config import (
    SAM2_ELIGIBLE_ROLES,
    SAM2_MIN_IOU,
    SAM2_MIN_QUALITY_SCORE,
    SAM2_SERVICE_URL,
)

STAGE = StageName.OBJECT_EXTRACTION
_OUTPUT_SUBDIR = Path("clean_v1") / "03_extraction"


def extract(
    canonical_path: str,
    manifest: SceneManifest,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, ExtractionResult | None]:

    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    obj_dir = stage_dir / "objects"
    prot_dir = stage_dir / "protected"
    obj_dir.mkdir(parents=True, exist_ok=True)
    prot_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"canonical={canonical_path} objectCount={len(manifest.objects)} path=TYPE_C",
        metrics={"objectCount": len(manifest.objects), "sam2Url": SAM2_SERVICE_URL},
    )

    try:
        canonical_rgb = Image.open(canonical_path).convert("RGB")
        canonical = canonical_rgb.copy().convert("RGBA")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open canonical: {exc}")

    w, h = canonical.width, canonical.height

    # ── SAM2 일괄 요청 (eligible 객체만) ─────────────────────────────────────
    sam2_results_by_obj: dict[str, "Sam2Detection | None"] = {}
    eligible_objects = [
        obj for obj in manifest.objects
        if obj.role in SAM2_ELIGIBLE_ROLES and obj.movable and obj.removable_from_scene
    ]

    if eligible_objects:
        prompts = _build_sam2_prompts(eligible_objects)
        logger.artifact_written(
            STAGE.value, "(memory) sam2_request",
            f"SAM2 query: {len(eligible_objects)} objects → {SAM2_SERVICE_URL}",
        )
        sam2_detections = query_sam2(SAM2_SERVICE_URL, canonical_rgb, prompts, timeout=30)

        if sam2_detections is None:
            logger.artifact_written(
                STAGE.value, "(memory) sam2_unavailable",
                "SAM2 service not reachable — falling back to polygon for all eligible objects",
            )
            for obj in eligible_objects:
                sam2_results_by_obj[obj.id] = None
        else:
            logger.artifact_written(
                STAGE.value, "(memory) sam2_response",
                f"SAM2 returned {len(sam2_detections)} detections",
            )
            for obj in eligible_objects:
                gpt_box = (obj.bbox.x, obj.bbox.y, obj.bbox.width, obj.bbox.height)
                match = best_match(
                    gpt_box, sam2_detections,
                    min_iou=SAM2_MIN_IOU,
                    min_quality=SAM2_MIN_QUALITY_SCORE,
                )
                sam2_results_by_obj[obj.id] = match

    # ── 객체별 추출 ───────────────────────────────────────────────────────────
    extracted: list[ExtractedObject] = []
    protected: list[ProtectedMask] = []

    for obj in manifest.objects:
        # protected_subject → full-size mask only
        if obj.role == "protected_subject":
            mask = rasterize_polygon(obj.polygon, w, h)
            mask_path = prot_dir / f"{obj.id}.mask.png"
            mask.save(str(mask_path))
            logger.artifact_written(STAGE.value, str(mask_path), f"protected mask id={obj.id}")
            protected.append(ProtectedMask(id=obj.id, role=obj.role, mask_path=str(mask_path)))
            continue

        if not (obj.movable and obj.removable_from_scene):
            continue

        # decorative_ad: P4 removal mask에서 이미 제거됨 → 레이아웃에 다시 배치 불필요
        if obj.role == "decorative_ad":
            continue

        # ── 마스크 선택: SAM2 or polygon ─────────────────────────────────────
        sam2_det = sam2_results_by_obj.get(obj.id)
        if sam2_det is not None:
            mask, mask_source = _mask_from_sam2(sam2_det, w, h)
        else:
            mask = rasterize_polygon(obj.polygon, w, h)
            mask_source = "polygon"

        bbox = bounding_rect(mask)
        if bbox is None:
            if obj.required:
                return _fail(
                    logger,
                    "REQUIRED_MASK_EMPTY",
                    f"Required object '{obj.id}' (role={obj.role}) produced an empty mask",
                )
            continue

        x1, y1, x2, y2 = bbox
        r, g, b, _ = canonical.split()
        full_rgba = Image.merge("RGBA", (r, g, b, mask))
        cropped_rgba = full_rgba.crop((x1, y1, x2, y2))
        cropped_mask = mask.crop((x1, y1, x2, y2))

        if should_remove_background(cropped_rgba):
            cropped_rgba = remove_background(cropped_rgba, obj.role)
            logger.artifact_written(
                STAGE.value, "(memory) bg_removal",
                f"id={obj.id} role={obj.role} dark-bg detected → background removed",
            )

        rgba_path = obj_dir / f"{obj.id}.rgba.png"
        mask_path = obj_dir / f"{obj.id}.mask.png"
        meta_path = obj_dir / f"{obj.id}.json"

        cropped_rgba.save(str(rgba_path), format="PNG")
        cropped_mask.save(str(mask_path), format="PNG")

        src_bbox = SourceBBox(x=x1, y=y1, width=x2 - x1, height=y2 - y1)
        meta = {
            "id": obj.id,
            "role": obj.role,
            "required": obj.required,
            "movable": obj.movable,
            "removableFromScene": obj.removable_from_scene,
            "sourceBbox": {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1},
            "rgbaPath": str(rgba_path),
            "maskPath": str(mask_path),
            "maskSource": mask_source,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.artifact_written(
            STAGE.value, str(rgba_path),
            f"rgba id={obj.id} bbox={bbox} maskSource={mask_source}",
        )
        logger.artifact_written(STAGE.value, str(mask_path), f"mask id={obj.id}")

        extracted.append(
            ExtractedObject(
                id=obj.id,
                role=obj.role,
                required=obj.required,
                movable=obj.movable,
                removable_from_scene=obj.removable_from_scene,
                source_bbox=src_bbox,
                rgba_path=str(rgba_path),
                mask_path=str(mask_path),
                meta_path=str(meta_path),
            )
        )

    result_obj = ExtractionResult(
        job_id=job_id,
        objects=extracted,
        protected=protected,
    )
    extraction_json_path = stage_dir / "extraction.json"
    extraction_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.extraction_json_path = str(extraction_json_path)
    logger.artifact_written(STAGE.value, str(extraction_json_path), "extraction summary")

    sam2_count = sum(1 for o in extracted if _read_mask_source(stage_dir, o.id) == "sam2")
    logger.stage_pass(
        STAGE.value,
        f"TYPE C: {len(extracted)} extracted ({sam2_count} SAM2 / {len(extracted)-sam2_count} polygon), "
        f"{len(protected)} protected",
        metrics={
            "extractedCount": len(extracted),
            "sam2Count": sam2_count,
            "polygonCount": len(extracted) - sam2_count,
            "protectedCount": len(protected),
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "extractedCount": len(extracted),
            "sam2Count": sam2_count,
            "protectedCount": len(protected),
        },
        artifacts={"extraction_json": str(extraction_json_path)},
    ), result_obj


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_sam2_prompts(objects: list[SceneObject]) -> list[dict]:
    """객체 description → SAM2 prompts 리스트."""
    prompts: list[dict] = []
    for obj in objects:
        texts = []
        if obj.text_content:
            texts.append(obj.text_content)
        if obj.description:
            texts.append(obj.description)
        if not texts:
            texts = [obj.role]
        prompts.append({"role": obj.role, "texts": texts})
    return prompts


def _mask_from_sam2(det, img_w: int, img_h: int) -> tuple[Image.Image, str]:
    """SAM2 Detection → 풀사이즈 L 마스크 이미지."""
    try:
        mask_img = det.decode_mask()
        if mask_img.size != (img_w, img_h):
            mask_img = mask_img.resize((img_w, img_h), Image.NEAREST)
        return mask_img, "sam2"
    except Exception:
        return Image.new("L", (img_w, img_h), 0), "polygon"


def _read_mask_source(stage_dir: Path, obj_id: str) -> str:
    try:
        meta = json.loads((stage_dir / "objects" / f"{obj_id}.json").read_text(encoding="utf-8"))
        return meta.get("maskSource", "polygon")
    except Exception:
        return "polygon"


def _fail(logger, code, message):
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
