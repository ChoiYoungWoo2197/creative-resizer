"""4차-5: PSD Layer Reflow 진입점.
psd_layer_parser → layer_role_classifier → layer_reflow_engine → layer_compositor 체인.
"""
import os
import tempfile
from PIL import Image, ImageFilter

from psd_compat import open_psd_safe_with_patch
from psd_layer_parser import parse_psd_layers
from layer_role_classifier import classify_layers, get_role_stats
from layer_reflow_engine import compute_layout, calc_reflow_score, check_required_layers
from layer_compositor import compose_layers


def generate_psd_layer_reflow(file_path: str, target_w: int, target_h: int,
                               output_path: str, debug_dir: str = None) -> dict:
    """PSD 레이어 재배치 배너 생성. 항상 dict 반환 — success/error 필드로 판별.

    성공: success=True, template, usedLayerRoles, detectedRoles, extractedLayerCount,
          layerReflowScore, quality
    실패: success=False, error
    """
    result = {
        "success":              False,
        "error":                None,
        "template":             None,
        "usedLayerRoles":       [],
        "detectedRoles":        [],
        "extractedLayerCount":  0,
        "layerReflowScore":     0.0,
        "quality":              {},
        "outputPath":           None,
    }

    # MVP: 1250×560만 지원
    if not (target_w == 1250 and target_h == 560):
        result["error"] = f"unsupported target size: {target_w}x{target_h}"
        return result

    # PSD 열기
    psd, open_meta = open_psd_safe_with_patch(file_path)
    if not open_meta["success"]:
        result["error"] = f"PSD open failed: {open_meta.get('error', 'unknown')}"
        print(f"[LayerReflow] PSD open failed: {result['error']}")
        return result

    # Step 1: 레이어 추출
    job_dir = debug_dir or os.path.join(os.path.dirname(output_path), "layer_debug")
    layers = parse_psd_layers(psd, job_dir)
    result["extractedLayerCount"] = len(layers)

    if not layers:
        result["error"] = "no renderable layers found"
        return result

    # Step 2: 역할 분류
    classified = classify_layers(layers)
    stats = get_role_stats(classified)
    result["detectedRoles"] = stats["roles"]
    print(f"[LayerReflow] classified {stats['known']}/{stats['total']} layers"
          f" rate={stats['classifyRate']} roles={stats['roles']}")

    # Step 3: 레이아웃 계산
    layout = compute_layout(classified, target_w, target_h)
    if not layout["success"]:
        result["error"] = layout.get("error", "layout computation failed")
        result["quality"] = layout.get("quality", {})
        print(f"[LayerReflow] layout failed: {result['error']}")
        return result

    placements = layout["placements"]
    quality = layout["quality"]
    reflow_score = calc_reflow_score(placements, quality)
    result["layerReflowScore"] = reflow_score

    # 품질 점수 65 미만 → fallback 유도
    if reflow_score < 65:
        result["error"] = f"quality score too low: {reflow_score} < 65"
        result["quality"] = quality
        print(f"[LayerReflow] quality score {reflow_score} < 65, fallback to smart-fit")
        return result

    # Step 4: blur 배경 생성 (background 레이어 없을 때 대비)
    fallback_bg = None
    try:
        composite = psd.composite()
        if composite:
            fallback_bg = composite.convert("RGBA")
    except Exception:
        pass

    # Step 5: canvas 합성
    try:
        canvas = compose_layers(placements, classified, target_w, target_h, fallback_bg)
    except Exception as e:
        result["error"] = f"compose failed: {e}"
        print(f"[LayerReflow] compose failed: {e}")
        return result

    # Step 6: 저장
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        canvas.save(output_path, "PNG")
    except Exception as e:
        result["error"] = f"save failed: {e}"
        return result

    used_roles = list({p["role"] for p in placements if p["role"] != "background"})
    result.update({
        "success":        True,
        "template":       layout["layoutType"],
        "usedLayerRoles": used_roles,
        "quality":        quality,
        "outputPath":     output_path,
    })
    print(f"[LayerReflow] done score={reflow_score} layout={layout['layoutType']}"
          f" roles={used_roles}")
    return result
