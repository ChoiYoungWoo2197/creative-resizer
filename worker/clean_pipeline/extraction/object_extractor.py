"""OBJECT_EXTRACTION stage: extract movable+removable objects as RGBA crops.

Rules:
- Only objects where movable=True AND removableFromScene=True get RGBA extraction
- protected_subject → original-size mask only, no RGBA
- Polygon rasterized via polygon_mask.rasterize_polygon (anti-aliased)
- Alpha channel of RGBA comes exclusively from the polygon mask
- No bbox-as-opaque fallback, no background removal, no segmentation model

Outputs under output/{jobId}/clean_v1/03_extraction/:
  extraction.json
  objects/{id}.rgba.png
  objects/{id}.mask.png
  objects/{id}.json
  protected/{id}.mask.png
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from clean_pipeline.analysis.models import SceneManifest
from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.extraction.bg_removal import remove_background, should_remove_background, tight_crop
from clean_pipeline.extraction.models import (
    ExtractedObject,
    ExtractionResult,
    ProtectedMask,
    SourceBBox,
)
from clean_pipeline.extraction.polygon_mask import bounding_rect, rasterize_polygon
from clean_pipeline.pipeline_logger import PipelineLogger

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
        f"canonical={canonical_path} objectCount={len(manifest.objects)}",
        metrics={"objectCount": len(manifest.objects)},
    )

    try:
        canonical = Image.open(canonical_path).convert("RGBA")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open canonical: {exc}")

    w, h = canonical.width, canonical.height
    extracted: list[ExtractedObject] = []
    protected: list[ProtectedMask] = []
    decorative: list[ExtractedObject] = []

    for obj in manifest.objects:
        # ── protected_subject → full-size mask only ─────────────────────────
        if obj.role == "protected_subject":
            mask = rasterize_polygon(obj.polygon, w, h)
            mask_path = prot_dir / f"{obj.id}.mask.png"
            mask.save(str(mask_path))
            logger.artifact_written(STAGE.value, str(mask_path), f"protected mask id={obj.id}")
            protected.append(ProtectedMask(id=obj.id, role=obj.role, mask_path=str(mask_path)))
            continue

        # ── only movable + removable ad objects get RGBA ──────────────────
        if not (obj.movable and obj.removable_from_scene):
            continue

        # decorative_ad: RGBA 추출 없이 mask만 생성 → P4 removal_mask union에 포함
        if obj.role == "decorative_ad":
            dec_mask = rasterize_polygon(obj.polygon, w, h)
            dec_bbox = bounding_rect(dec_mask)
            if dec_bbox is not None:
                dx1, dy1, dx2, dy2 = dec_bbox
                cropped_dec_mask = dec_mask.crop((dx1, dy1, dx2, dy2))
                dec_mask_path = obj_dir / f"{obj.id}.mask.png"
                cropped_dec_mask.save(str(dec_mask_path), format="PNG")
                logger.artifact_written(STAGE.value, str(dec_mask_path),
                                        f"decorative mask id={obj.id} bbox={dec_bbox}")
                decorative.append(ExtractedObject(
                    id=obj.id,
                    role=obj.role,
                    required=False,
                    movable=True,
                    removable_from_scene=True,
                    source_bbox=SourceBBox(x=dx1, y=dy1, width=dx2 - dx1, height=dy2 - dy1),
                    rgba_path="",
                    mask_path=str(dec_mask_path),
                    meta_path="",
                ))
            continue

        mask = rasterize_polygon(obj.polygon, w, h)
        bbox = bounding_rect(mask)

        if bbox is None:
            if obj.required:
                return _fail(
                    logger,
                    "REQUIRED_MASK_EMPTY",
                    f"Required object '{obj.id}' (role={obj.role}) produced an empty mask — "
                    "polygon has too few points or zero area",
                )
            # Non-required with empty mask: skip silently
            continue

        x1, y1, x2, y2 = bbox

        # Merge R,G,B from canonical + mask as alpha (full size)
        r, g, b, _ = canonical.split()
        full_rgba = Image.merge("RGBA", (r, g, b, mask))

        # Crop both to min bounding rect
        cropped_rgba = full_rgba.crop((x1, y1, x2, y2))
        cropped_mask = mask.crop((x1, y1, x2, y2))

        # Background removal: if the crop sits on a dark background,
        # strip background pixels so the object composites cleanly
        # onto any new (non-dark) scene plate.
        if should_remove_background(cropped_rgba, obj.role):
            cropped_rgba = remove_background(cropped_rgba, obj.role)
            cropped_rgba = tight_crop(cropped_rgba)
            logger.artifact_written(
                STAGE.value, "(memory) bg_removal",
                f"id={obj.id} role={obj.role} dark-bg detected → background removed + tight-cropped",
            )

        # Save artefacts
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
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.artifact_written(STAGE.value, str(rgba_path), f"rgba id={obj.id} bbox={bbox}")
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
        decorative_masks=decorative,
    )

    extraction_json_path = stage_dir / "extraction.json"
    extraction_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.extraction_json_path = str(extraction_json_path)
    logger.artifact_written(STAGE.value, str(extraction_json_path), "extraction summary")

    logger.stage_pass(
        STAGE.value,
        f"{len(extracted)} objects extracted, {len(protected)} protected, {len(decorative)} decorative masks",
        metrics={"extractedCount": len(extracted), "protectedCount": len(protected), "decorativeCount": len(decorative)},
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={"extractedCount": len(extracted), "protectedCount": len(protected)},
        artifacts={"extraction_json": str(extraction_json_path)},
    ), result_obj


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
        reasons=[f"[{code}] {message}"],
    ), None
