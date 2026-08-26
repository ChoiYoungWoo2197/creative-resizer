"""TYPE G P4 — 비율 유지 투영 → 배경 위 합성.

Gemini 공식 (원본 구도 보존 스케일 투영):
  S  = min(W_target/W_source, H_target/H_source)
  Ox = (W_target - W_source * S) / 2
  Oy = (H_target - H_source * S) / 2
  x' = x * S + Ox,  y' = y * S + Oy
  w' = w * S,       h' = h * S

대상: depth=0, role != "bg", visible=True 인 최상위 레이어만.
(그룹이면 bg 서브레이어를 제외한 자식을 개별 합성)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.psd.psd_layer_reader import LayerInfo, _parse_role
from clean_pipeline.psd.smart_layout_engine import SmartLayoutEngine

STAGE = StageName.ELEMENT_COMPOSITE
_OUTPUT_SUBDIR = Path("clean_v1") / "04_composite"


def composite_elements(
    psd_path: str,
    layers: list[LayerInfo],
    bg_path: str,
    source_w: int,
    source_h: int,
    target_w: int,
    target_h: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
    psd=None,
    sz_scaled_w: int | None = None,
    sz_scaled_h: int | None = None,
    sz_pad_left: int | None = None,
    sz_pad_top: int | None = None,
    safe_top: int = 0,
    safe_right: int = 0,
    safe_bottom: int = 0,
    safe_left: int = 0,
) -> tuple[StageResult, dict | None]:
    """비-bg 레이어를 letterbox 변환 좌표로 배경에 합성.

    Returns (StageResult, {"result_path": str} | None)
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"element composite src={source_w}x{source_h} -> {target_w}x{target_h}",
    )

    # ── 1. 레이아웃 모드 결정 + letterbox 계수 계산 ──────────────────────────
    engine = SmartLayoutEngine(
        source_size=(source_w, source_h),
        target_size=(target_w, target_h),
    )
    mode = engine.determine_mode()

    safe_zone_mode = (
        sz_scaled_w is not None and sz_scaled_h is not None
        and sz_pad_left is not None and sz_pad_top is not None
    )

    if safe_zone_mode:
        # P3 outpaint와 동일한 좌표계 사용:
        # 원본을 sz_scaled 컨테이너에 letterbox → (pad_left, pad_top) 위치에 배치
        S = min(sz_scaled_w / source_w, sz_scaled_h / source_h)
        Ox = sz_pad_left + (sz_scaled_w - source_w * S) / 2
        Oy = sz_pad_top  + (sz_scaled_h - source_h * S) / 2
        print(
            f"[{STAGE.value}][MODE] {mode} SAFE_ZONE S={S:.4f} "
            f"Ox={Ox:.1f} Oy={Oy:.1f} "
            f"sz_scaled={sz_scaled_w}x{sz_scaled_h} pad=({sz_pad_left},{sz_pad_top})",
            flush=True,
        )
    else:
        # safe_zone 없음 — 전체 캔버스 기준 letterbox (기존 로직)
        S = min(target_w / source_w, target_h / source_h)
        Ox = (target_w - source_w * S) / 2
        Oy = (target_h - source_h * S) / 2
        print(
            f"[{STAGE.value}][MODE] {mode} LETTERBOX S={S:.4f} Ox={Ox:.1f} Oy={Oy:.1f}",
            flush=True,
        )

    # ── 2. P3 배경 캔버스 열기 ───────────────────────────────────────────────
    try:
        canvas = Image.open(bg_path).convert("RGBA")
    except Exception as exc:
        return _fail(logger, "BG_OPEN_FAILED", f"배경 이미지 열기 실패: {exc}")

    # ── 3. PSD 열기 (type_g_pipeline에서 이미 열어 전달했으면 재사용) ─────────────
    if psd is None:
        try:
            from psd_tools import PSDImage
            psd = PSDImage.open(psd_path)
        except Exception as exc:
            return _fail(logger, "PSD_OPEN_FAILED", f"PSD 열기 실패: {exc}")

    # ── 4-5. 레이아웃 모드에 따른 레이어 합성 ──────────────────────────────────
    # bg 계열 서브레이어 이름 집합 — AP_LAYOUT 그룹 합성 시 제외
    _BG_SUBLAYER_NAMES = frozenset({"bg", "배경", "background"})

    placed_layers: list[dict] = []  # P5 layout JSON용 레이어별 배치 정보

    if mode == "AP_LAYOUT":
        layers_dir = stage_dir / "layers"
        layers_dir.mkdir(exist_ok=True)

        effective_depth = _find_effective_depth(layers)
        top_layers = [
            l for l in layers
            if l.depth == effective_depth and l.role != "bg"
            and l.name.lower() not in _BG_SUBLAYER_NAMES and l.visible
        ]
        print(f"[{STAGE.value}][EFFECTIVE_DEPTH] depth={effective_depth} top_layer_count={len(top_layers)}", flush=True)
        placed_count = 0
        for layer_info in top_layers:
            layer = _find_layer(psd, layer_info.name)
            if layer is None:
                print(f"[{STAGE.value}][LAYER_NOT_FOUND] name={layer_info.name!r}", flush=True)
                continue

            if layer.is_group():
                for child in layer:
                    if child.name.lower() in _BG_SUBLAYER_NAMES:
                        print(f"[{STAGE.value}][BG_SUBLAYER_SKIP] name={child.name!r}", flush=True)
                        continue

                    child_role = _parse_role(child.name)
                    # 역할 있는 그룹(badge 등)은 통째로 합성, 역할 없는 그룹만 하위 분해
                    if not child.is_group() or child_role != "unknown":
                        safe = _safe_filename(f"{layer_info.name}__{child.name}")
                        placement = _place_layer(
                            child, canvas, psd, S, Ox, Oy, STAGE.value,
                            layer_save_path=layers_dir / f"{safe}.png",
                        )
                        if placement:
                            placement["role"] = child_role or layer_info.role
                            placement["layer_file"] = f"clean_v1/04_composite/layers/{safe}.png"
                            placed_layers.append(placement)
                            placed_count += 1
                    else:
                        # 역할 없는 그룹 → 하위 레이어 개별 분해
                        sub_targets = [
                            sc for sc in child
                            if sc.name.lower() not in _BG_SUBLAYER_NAMES and sc.is_visible()
                        ]
                        if len(sub_targets) > 1:
                            print(f"[{STAGE.value}][SUBLAYER_EXPAND] parent={child.name!r} count={len(sub_targets)}", flush=True)
                            for sub in sub_targets:
                                safe = _safe_filename(f"{layer_info.name}__{child.name}__{sub.name}")
                                placement = _place_layer(
                                    sub, canvas, psd, S, Ox, Oy, STAGE.value,
                                    layer_save_path=layers_dir / f"{safe}.png",
                                )
                                if placement:
                                    placement["role"] = _parse_role(sub.name)
                                    placement["layer_file"] = f"clean_v1/04_composite/layers/{safe}.png"
                                    placed_layers.append(placement)
                                    placed_count += 1
                        else:
                            safe = _safe_filename(f"{layer_info.name}__{child.name}")
                            placement = _place_layer(
                                child, canvas, psd, S, Ox, Oy, STAGE.value,
                                layer_save_path=layers_dir / f"{safe}.png",
                            )
                            if placement:
                                placement["role"] = child_role or layer_info.role
                                placement["layer_file"] = f"clean_v1/04_composite/layers/{safe}.png"
                                placed_layers.append(placement)
                                placed_count += 1
            else:
                safe = _safe_filename(layer_info.name)
                placement = _place_layer(
                    layer, canvas, psd, S, Ox, Oy, STAGE.value,
                    layer_save_path=layers_dir / f"{safe}.png",
                )
                if placement:
                    placement["role"] = layer_info.role
                    placement["layer_file"] = f"clean_v1/04_composite/layers/{safe}.png"
                    placed_layers.append(placement)
                    placed_count += 1

        total_count = len(top_layers)
    else:
        # SmartLayoutEngine 기반 leaf-layer 합성 (EDGE_ANCHORING / VERTICAL_STACKING)
        layers_dir = stage_dir / "layers"
        layers_dir.mkdir(exist_ok=True)
        layout_dict = engine.calculate_layout(layers)
        placed_count, placed_layers = _composite_with_layout(
            psd, canvas, layout_dict, STAGE.value,
            layers_dir=layers_dir,
            layer_infos=layers,
        )
        total_count = len(layout_dict)

    # ── 6. 결과 저장 ─────────────────────────────────────────────────────────
    result_path = str(stage_dir / "result.png")
    canvas.convert("RGB").save(result_path)
    logger.artifact_written(
        STAGE.value, result_path,
        f"{placed_count}/{total_count} 레이어 합성 완료",
    )

    # ── 7. P5 layout_result.json 저장 (모든 모드 공통) ───────────────────────
    layout_result_path: str | None = None
    if placed_layers:
        layout_data = {
            "source_w": source_w,
            "source_h": source_h,
            "target_w": target_w,
            "target_h": target_h,
            "mode": mode,
            "scale": round(S, 6),
            "offset_x": round(Ox, 2),
            "offset_y": round(Oy, 2),
            "bg_file": _relative_path(bg_path, Path(output_dir) / job_id),
            "layers": placed_layers,
            "safe_zone": {
                "top": safe_top,
                "right": safe_right,
                "bottom": safe_bottom,
                "left": safe_left,
            },
        }
        layout_result_path = str(stage_dir / f"layout_result_{target_w}x{target_h}.json")
        with open(layout_result_path, "w", encoding="utf-8") as f:
            json.dump(layout_data, f, ensure_ascii=False, indent=2)
        print(f"[{STAGE.value}][LAYOUT_RESULT_SAVED] path={layout_result_path}", flush=True)

    # ── 8. P2↔P4 merged layers JSON (PSD 전체 트리 + 렌더 좌표 매핑) ─────────
    p2_layers_path = Path(output_dir) / job_id / "clean_v1" / "02_psd_layers" / "layers.json"
    if placed_layers and p2_layers_path.exists():
        rendered_map: dict[str, dict] = {
            pl["name"]: {
                "x": pl["render_x"],
                "y": pl["render_y"],
                "w": pl["render_w"],
                "h": pl["render_h"],
                "scale": pl["scale"],
                "layer_file": pl.get("layer_file"),
            }
            for pl in placed_layers
        }
        with open(p2_layers_path, encoding="utf-8") as f:
            p2_data = json.load(f)
        merged_layers = [
            {**layer, "rendered": rendered_map.get(layer["name"].strip("[] \t"))}
            for layer in p2_data.get("layers", [])
        ]
        merged_path = str(stage_dir / f"layers_merged_{target_w}x{target_h}.json")
        with open(merged_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "jobId": job_id,
                    "psdPath": p2_data.get("psdPath"),
                    "psdSize": p2_data.get("psdSize"),
                    "target_w": target_w,
                    "target_h": target_h,
                    "mode": mode,
                    "scale_S": round(S, 6),
                    "offset_x": round(Ox, 2),
                    "offset_y": round(Oy, 2),
                    "bg_file": _relative_path(bg_path, Path(output_dir) / job_id),
                    "layers": merged_layers,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"[{STAGE.value}][LAYERS_MERGED_SAVED] path={merged_path}", flush=True)

    logger.stage_pass(
        STAGE.value,
        f"element composite PASS mode={mode} "
        f"{'safe_zone' if safe_zone_mode else 'letterbox'} "
        f"placed={placed_count}/{total_count}",
        metrics={
            "mode": mode,
            "safeZoneMode": safe_zone_mode,
            "placedCount": placed_count,
            "totalCount": total_count,
            "scale": round(S, 4),
            "offsetX": round(Ox, 1),
            "offsetY": round(Oy, 1),
        },
    )

    result: dict = {"result_path": result_path}
    if layout_result_path:
        result["layout_result_path"] = layout_result_path
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "mode": mode,
            "safeZoneMode": safe_zone_mode,
            "placedCount": placed_count,
            "scale": round(S, 4),
            "offsetX": round(Ox, 1),
            "offsetY": round(Oy, 1),
        },
        artifacts={"result": result_path},
    ), result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _relative_path(abs_path: str, base: Path) -> str:
    """abs_path 를 base 기준 상대경로 문자열(슬래시)로 반환. 실패 시 abs_path 원본."""
    try:
        return str(Path(abs_path).relative_to(base)).replace("\\", "/")
    except ValueError:
        return abs_path


def _safe_filename(name: str) -> str:
    """레이어명을 파일명으로 사용할 수 있게 특수문자를 언더스코어로 변환."""
    return re.sub(r"[^\w-]", "_", name)


def _place_layer(
    layer,
    canvas: Image.Image,
    psd,
    S: float,
    Ox: float,
    Oy: float,
    stage_name: str,
    layer_save_path: Path | None = None,
) -> dict | None:
    """단일 psd-tools 레이어를 letterbox 좌표로 canvas에 합성.

    성공 시 배치 정보 dict 반환 (P5 layout JSON용). 실패 시 None.
    """
    try:
        img = layer.composite()
    except Exception as exc:
        print(f"[{stage_name}][COMPOSITE_SKIP] name={layer.name!r} err={exc}", flush=True)
        return None

    if img is None:
        return None

    img = img.convert("RGBA")
    b = layer.bbox
    lw, lh = b[2] - b[0], b[3] - b[1]
    if lw <= 0 or lh <= 0:
        return None

    if img.size == (psd.width, psd.height):
        cropped = img.crop((b[0], b[1], b[2], b[3]))
    else:
        cropped = img

    new_w = max(1, int(lw * S))
    new_h = max(1, int(lh * S))
    scaled = cropped.resize((new_w, new_h), Image.LANCZOS)

    new_x = int(b[0] * S + Ox)
    new_y = int(b[1] * S + Oy)

    canvas.paste(scaled, (new_x, new_y), scaled)

    if layer_save_path:
        try:
            scaled.save(str(layer_save_path))
        except Exception as exc:
            print(f"[{stage_name}][LAYER_SAVE_FAIL] name={layer.name!r} err={exc}", flush=True)

    print(
        f"[{stage_name}][LAYER_PLACED] name={layer.name!r} "
        f"pos=({new_x},{new_y}) size={new_w}x{new_h}",
        flush=True,
    )
    return {
        "name": layer.name.strip("[] \t"),
        "render_x": new_x,
        "render_y": new_y,
        "render_w": new_w,
        "render_h": new_h,
        "scale": round(S, 6),
    }


def _find_effective_depth(layers: list[LayerInfo]) -> int:
    """역할 있는 레이어의 최소 depth — 배경/banner 같은 래퍼 그룹을 건너뜀."""
    depths = [l.depth for l in layers if l.role not in ("unknown", "bg")]
    return min(depths) if depths else 0


def _find_layer(parent, name: str):
    for layer in parent:
        if layer.name == name:
            return layer
        if layer.is_group():
            found = _find_layer(layer, name)
            if found:
                return found
    return None


def _find_layer_at_depth(parent, name: str, target_depth: int, _depth: int = 0):
    """(name, depth) 기준으로 PSD 트리에서 레이어를 찾는다."""
    for layer in parent:
        if _depth == target_depth and layer.name == name:
            return layer
        if layer.is_group() and _depth < target_depth:
            found = _find_layer_at_depth(layer, name, target_depth, _depth + 1)
            if found:
                return found
    return None


def _composite_with_layout(
    psd,
    canvas,
    layout_dict: dict,
    stage_name: str,
    layers_dir: Path | None = None,
    layer_infos: list[LayerInfo] | None = None,
) -> tuple[int, list[dict]]:
    """SmartLayoutEngine layout_dict 좌표로 leaf 레이어를 배경에 합성.

    Returns (placed_count, placed_layers) — placed_layers는 layout_result.json용 공통 스키마.
    """
    role_map: dict[str, str] = {li.name: (li.role or "unknown") for li in (layer_infos or [])}
    placed_count = 0
    placed_layers: list[dict] = []

    for (layer_name, layer_depth), coords in layout_dict.items():
        layer = _find_layer_at_depth(psd, layer_name, layer_depth)
        if layer is None:
            print(
                f"[{stage_name}][LAYER_NOT_FOUND] name={layer_name!r} depth={layer_depth}",
                flush=True,
            )
            continue

        try:
            img = layer.composite()
        except Exception as exc:
            print(
                f"[{stage_name}][COMPOSITE_SKIP] name={layer_name!r} err={exc}",
                flush=True,
            )
            continue

        if img is None:
            continue

        img = img.convert("RGBA")
        b = layer.bbox
        lw, lh = b[2] - b[0], b[3] - b[1]
        if lw <= 0 or lh <= 0:
            continue

        if img.size == (psd.width, psd.height):
            cropped = img.crop((b[0], b[1], b[2], b[3]))
        else:
            cropped = img

        new_w = max(1, coords["new_x2"] - coords["new_x1"])
        new_h = max(1, coords["new_y2"] - coords["new_y1"])
        scaled = cropped.resize((new_w, new_h), Image.LANCZOS)

        new_x = coords["new_x1"]   # 음수 허용 — Pillow 자동 clip (중앙/우측 정렬 overflow 처리)
        new_y = max(0, coords["new_y1"])
        canvas.paste(scaled, (new_x, new_y), scaled)
        placed_count += 1

        safe = _safe_filename(layer_name)
        layer_file_rel: str | None = None
        if layers_dir is not None:
            layer_save_path = layers_dir / f"{safe}.png"
            try:
                scaled.save(str(layer_save_path))
                layer_file_rel = f"clean_v1/04_composite/layers/{safe}.png"
            except Exception as exc:
                print(f"[{stage_name}][LAYER_SAVE_FAIL] name={layer_name!r} err={exc}", flush=True)

        placement: dict = {
            "name": layer_name.strip("[] \t"),
            "render_x": new_x,
            "render_y": new_y,
            "render_w": new_w,
            "render_h": new_h,
            "scale": round(coords["scale_used"], 6),
            "role": role_map.get(layer_name, "unknown"),
        }
        if layer_file_rel:
            placement["layer_file"] = layer_file_rel
        placed_layers.append(placement)

        print(
            f"[{stage_name}][LAYER_PLACED] name={layer_name!r} depth={layer_depth} "
            f"pos=({new_x},{new_y}) size={new_w}x{new_h} scale={coords['scale_used']}",
            flush=True,
        )
    return placed_count, placed_layers


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
