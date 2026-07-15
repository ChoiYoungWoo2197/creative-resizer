"""Stage 16: AI segmentation / mask generation PoC.

Flag:
  env  CREATIVE_SEGMENTATION_POC=true
  req  object_analysis.experimentalSegmentation=true

기존 production path에 영향 없음.
실패 시 빈 mask list + 오류 metadata 반환 (caller가 fallback 처리).
"""

import os

from mask_utils import (
    MASK_ROLES,
    create_mask_dict,
    bbox_to_mask_image,
    psd_alpha_to_canvas_mask,
    compute_mask_quality,
)


# ─── PoC flag ─────────────────────────────────────────────────────────────────

def is_segmentation_poc_enabled(extra_flags: dict | None = None) -> bool:
    env_on = os.environ.get("CREATIVE_SEGMENTATION_POC", "false").lower() == "true"
    req_on = bool((extra_flags or {}).get("experimentalSegmentation"))
    return env_on or req_on


# ─── role classification ──────────────────────────────────────────────────────

def _classify_mask_role(obj: dict, product_score: float) -> str:
    """Stage 14 isolation score 기반으로 mask role 결정."""
    role = obj.get("role", "unknown")

    if role == "background":
        return "background"
    if role in ("headline", "body_text", "price", "discount"):
        return "text"
    if role == "cta":
        return "cta"
    if role in ("person", "logo"):
        return "person_or_hand"
    if role == "main_image":
        if product_score >= 60.0:
            return "product"
        elif product_score >= 30.0:
            return "person_or_hand"
        else:
            return "visual_context"
    return "unknown"


# ─── mask extraction ──────────────────────────────────────────────────────────

def _try_psd_alpha_mask(obj: dict, canvas_w: int, canvas_h: int):
    """PSD smartobject/Photoroom 레이어에서 alpha mask 추출 시도.

    성공하면 (mask_img, "psd_alpha"), 실패하면 (None, None).
    """
    img_path = obj.get("imagePath") or ""
    source_type = obj.get("sourceType", "")

    is_transparent = (
        source_type in ("psd_layer_smartobject", "psd_layer")
        or "Photoroom" in img_path
        or "cutout" in img_path.lower()
    )
    if not is_transparent:
        return None, None
    if not img_path or not os.path.exists(img_path):
        return None, None

    try:
        from PIL import Image
        layer_img = Image.open(img_path).convert("RGBA")
        bbox = obj.get("bbox") or {}
        mask = psd_alpha_to_canvas_mask(layer_img, bbox, canvas_w, canvas_h)
        if mask is not None:
            return mask, "psd_alpha"
    except Exception:
        pass
    return None, None


def _bbox_coarse(obj: dict, canvas_w: int, canvas_h: int, mask_role: str):
    """bbox 기반 coarse rectangular mask 생성.

    visual_context면 source를 "visual_context"로 표시.
    반환: (mask_img, source_str) or (None, None) if bbox invalid.
    """
    bbox = obj.get("bbox") or {}
    if bbox.get("width", 0) <= 0 or bbox.get("height", 0) <= 0:
        return None, None

    feather = 4 if mask_role not in ("text", "cta") else 2
    mask = bbox_to_mask_image(bbox, canvas_w, canvas_h, feather=feather)
    source = "visual_context" if mask_role == "visual_context" else "object_bbox_coarse"
    return mask, source


# ─── external AI integration ─────────────────────────────────────────────────

def _try_external_segmentation(
    artboard_img,
    canvas_w: int,
    canvas_h: int,
    native_source: str,
    native_bbox: dict | None,
    job_id: str | None,
    warnings: list[str],
) -> tuple[dict | None, dict]:
    """외부 segmentation 서비스 호출 + 최적 detection 선택.

    반환: (best_detection | None, selector_result_dict)
    실패 시: (None, {}) — warnings에 오류 추가, job 계속 진행.
    """
    try:
        from external_segmentation_client import call_segment, is_enabled
        from external_mask_selector import (
            select_best_external_detection,
            decide_mask_source,
        )
        from mask_quality import score_native_mask
    except ImportError as e:
        warnings.append(f"externalSegmentation_import_failed:{e}")
        return None, {}

    if not is_enabled():
        return None, {}

    if artboard_img is None:
        warnings.append("externalSegmentation_skipped:no_artboard_img")
        return None, {}

    detections, ext_meta = call_segment(
        artboard_img,
        source_type="artboard",
        target_roles=["product"],
        job_id=job_id,
    )

    if not detections:
        return None, ext_meta

    best_det = select_best_external_detection(
        detections,
        role="product",
        native_bbox=native_bbox,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
    )

    native_score = score_native_mask(native_source, native_bbox or {}, canvas_w, canvas_h)
    selector = decide_mask_source(
        native_source=native_source,
        native_score=native_score,
        external_detection=best_det,
        external_metadata=ext_meta,
        job_id=job_id,
    )

    return best_det, {**ext_meta, **selector}


def generate_mask_with_external_ai(
    image,
    prompt: str = "",
    api_key: str | None = None,
):
    """외부 AI segmentation (레거시 stub — 실제 연동은 _try_external_segmentation 사용)."""
    return None


# ─── main entry ──────────────────────────────────────────────────────────────

def run_segmentation_poc(
    creative_object_set: dict,
    artboard_img,           # PIL Image | None
    canvas_w: int,
    canvas_h: int,
    output_dir: str | None = None,
    job_id: str | None = None,
    extra_flags: dict | None = None,
) -> tuple[list[dict], dict]:
    """CreativeObjectSet의 각 object에 대해 segmentation mask를 생성한다.

    반환: (masks_list, poc_metadata)
    실패 시 ([], error_metadata) — job을 죽이지 않는다.
    """
    prefix = f"[{job_id or 'job'}][SegPoc]"

    if not is_segmentation_poc_enabled(extra_flags):
        return [], {"segmentationPocEnabled": False}

    print(f"{prefix} starting canvas={canvas_w}x{canvas_h}")

    masks: list[dict] = []
    warnings: list[str] = []
    counter = 0
    product_mask_selected = False
    product_mask_id = None
    quality_scores: list[float] = []

    try:
        from layout_compiler import score_product_candidate

        objects = (creative_object_set or {}).get("objects", [])

        for obj in objects:
            obj_id = obj.get("id", f"obj_{counter}")
            obj_role = obj.get("role", "unknown")

            # Stage 14 isolation score (main_image만)
            if obj_role == "main_image":
                prod_score = score_product_candidate(obj, canvas_w, canvas_h)
            else:
                prod_score = 50.0

            mask_role = _classify_mask_role(obj, prod_score)

            # background는 mask 생성 대상 아님 (배경 자체이므로)
            if mask_role == "background":
                continue

            # 1. PSD alpha mask 시도
            mask_img, source = _try_psd_alpha_mask(obj, canvas_w, canvas_h)

            # 2. bbox coarse mask fallback
            if mask_img is None:
                mask_img, source = _bbox_coarse(obj, canvas_w, canvas_h, mask_role)

            if mask_img is None:
                warnings.append(f"maskSkipped:{obj_id} no_bbox_and_no_imagePath")
                continue

            # quality 계산
            bbox = obj.get("bbox") or {}
            quality = compute_mask_quality(source, bbox, canvas_w, canvas_h, prod_score)
            quality_scores.append(quality["overallScore"])

            # debug 저장 (실패해도 무시)
            mask_path = None
            if output_dir:
                try:
                    os.makedirs(output_dir, exist_ok=True)
                    fname = f"result.mask.{mask_role}.{counter:02d}.png"
                    mask_path = os.path.join(output_dir, fname)
                    mask_img.save(mask_path)
                except Exception as e:
                    warnings.append(f"maskSaveFailed:{e}")

            counter += 1
            mask_id = f"mask_{mask_role}_{counter:03d}"

            mask_dict = create_mask_dict(
                mask_id=mask_id,
                object_id=obj_id,
                role=mask_role,
                source=source,
                bbox=bbox,
                canvas_w=canvas_w,
                canvas_h=canvas_h,
                confidence=quality["sourcePriority"],
                mask_img=mask_img,
                mask_path=mask_path,
                quality=quality,
            )
            masks.append(mask_dict)

            # product mask 첫 번째만 기록
            if mask_role == "product" and not product_mask_selected:
                product_mask_selected = True
                product_mask_id = mask_id

        avg_quality = round(
            sum(quality_scores) / max(len(quality_scores), 1), 3
        )

        # ── 외부 AI segmentation 시도 (product mask 보완) ───────────────────────
        ext_meta: dict = {}
        product_native_bbox = None
        product_native_source = "object_bbox_coarse"
        for m in masks:
            if m.get("role") == "product":
                product_native_bbox = m.get("bbox")
                product_native_source = m.get("source", "object_bbox_coarse")
                break

        try:
            best_det, ext_sel = _try_external_segmentation(
                artboard_img=artboard_img,
                canvas_w=canvas_w,
                canvas_h=canvas_h,
                native_source=product_native_source,
                native_bbox=product_native_bbox,
                job_id=job_id,
                warnings=warnings,
            )
            ext_meta = ext_sel

            # external mask 사용 결정이 내려진 경우 product mask 교체
            if best_det and ext_sel.get("useExternal"):
                from external_mask_selector import extract_mask_png_from_detection
                ext_mask_img = extract_mask_png_from_detection(best_det)
                if ext_mask_img is not None:
                    ext_bbox = best_det.get("bbox", product_native_bbox or {})
                    ext_conf = best_det.get("maskConfidence", 0.6)
                    ext_mask_path = None
                    if output_dir:
                        try:
                            os.makedirs(output_dir, exist_ok=True)
                            ext_mask_path = os.path.join(output_dir, "result.external.product.00.mask.png")
                            ext_mask_img.save(ext_mask_path)
                        except Exception as e:
                            warnings.append(f"extMaskSaveFailed:{e}")

                    # 기존 product mask 제거 후 교체
                    masks = [m for m in masks if m.get("role") != "product"]
                    counter += 1
                    ext_mask_id = f"mask_product_ext_{counter:03d}"
                    from mask_utils import create_mask_dict as _create_mask_dict
                    masks.append(_create_mask_dict(
                        mask_id=ext_mask_id,
                        object_id="external_ai",
                        role="product",
                        source="external_grounded_sam2",
                        bbox=ext_bbox,
                        canvas_w=canvas_w,
                        canvas_h=canvas_h,
                        confidence=ext_conf,
                        mask_img=ext_mask_img,
                        mask_path=ext_mask_path,
                        quality={
                            "overallScore": best_det.get("maskQualityScore", 0.0),
                            "edgeSharpness": best_det.get("edgeSharpness", 0.0),
                            "sourcePriority": ext_conf,
                        },
                    ))
                    product_mask_id = ext_mask_id
                    product_mask_selected = True
                    print(f"{prefix} external mask 채택: score={ext_sel.get('externalMaskScore',0):.1f}")

        except Exception as e:
            warnings.append(f"externalSegmentation_error:{e}")

        meta = {
            "segmentationPocEnabled":  True,
            "segmentationProvider":    "psd_alpha+object_bbox_coarse",
            "masksGenerated":          len(masks),
            "productMaskSelected":     product_mask_selected,
            "productMaskId":           product_mask_id,
            "maskQualityScore":        avg_quality,
            "maskFallbackUsed":        False,
            "maskWarnings":            warnings,
            **ext_meta,
        }
        print(
            f"{prefix} done masks={len(masks)} "
            f"product={product_mask_selected} quality={avg_quality:.3f} "
            f"externalUsed={ext_meta.get('externalSegmentationUsed', False)}"
        )
        return masks, meta

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"{prefix} FAILED: {e}")
        return [], {
            "segmentationPocEnabled": True,
            "segmentationProvider":   "none",
            "masksGenerated":         0,
            "productMaskSelected":    False,
            "productMaskId":          None,
            "maskQualityScore":       0.0,
            "maskFallbackUsed":       True,
            "maskWarnings":           [f"segmentationFailed:{e}"],
        }
