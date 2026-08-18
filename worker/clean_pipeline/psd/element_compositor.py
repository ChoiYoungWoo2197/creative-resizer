"""TYPE G P4 — 비-bg 레이어 추출 + letterbox 변환 → 배경 위 합성.

Gemini 공식 (원본 구도 보존 스케일 투영):
  S  = min(W_target/W_source, H_target/H_source)
  Ox = (W_target - W_source * S) / 2
  Oy = (H_target - H_source * S) / 2
  x' = x * S + Ox,  y' = y * S + Oy
  w' = w * S,       h' = h * S

대상: depth=0, role != "bg", visible=True 인 최상위 레이어만.
(그룹이면 composite()가 하위 레이어 전체를 렌더링하므로 이중 합성 없음)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.psd.psd_layer_reader import LayerInfo

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

    # ── 1. letterbox 변환 계수 ───────────────────────────────────────────────
    S = min(target_w / source_w, target_h / source_h)
    Ox = (target_w - source_w * S) / 2
    Oy = (target_h - source_h * S) / 2
    print(
        f"[{STAGE.value}][TRANSFORM] S={S:.4f} Ox={Ox:.1f} Oy={Oy:.1f}",
        flush=True,
    )

    # ── 2. P3 배경 캔버스 열기 ───────────────────────────────────────────────
    try:
        canvas = Image.open(bg_path).convert("RGBA")
    except Exception as exc:
        return _fail(logger, "BG_OPEN_FAILED", f"배경 이미지 열기 실패: {exc}")

    # ── 3. PSD 열기 ─────────────────────────────────────────────────────────
    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(psd_path)
    except Exception as exc:
        return _fail(logger, "PSD_OPEN_FAILED", f"PSD 열기 실패: {exc}")

    # ── 4. 대상 레이어 필터 — depth=0, role!=bg, visible ────────────────────
    top_layers = [
        l for l in layers
        if l.depth == 0 and l.role != "bg" and l.visible
    ]

    # ── 5. 레이어별 composite → 변환 → 합성 ──────────────────────────────────
    placed_count = 0
    for layer_info in top_layers:
        layer = _find_layer(psd, layer_info.name)
        if layer is None:
            print(f"[{STAGE.value}][LAYER_NOT_FOUND] name={layer_info.name!r}", flush=True)
            continue

        try:
            img = layer.composite()
        except Exception as exc:
            print(f"[{STAGE.value}][COMPOSITE_SKIP] name={layer_info.name!r} err={exc}", flush=True)
            continue

        if img is None:
            continue

        img = img.convert("RGBA")

        # bbox: psd-tools plain tuple (left, top, right, bottom)
        b = layer.bbox
        lw, lh = b[2] - b[0], b[3] - b[1]
        if lw <= 0 or lh <= 0:
            continue

        # composite()가 full-canvas 크기를 반환하면 레이어 영역만 크롭
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
        placed_count += 1
        print(
            f"[{STAGE.value}][LAYER_PLACED] name={layer_info.name!r} role={layer_info.role} "
            f"pos=({new_x},{new_y}) size={new_w}x{new_h}",
            flush=True,
        )

    # ── 6. 결과 저장 ─────────────────────────────────────────────────────────
    result_path = str(stage_dir / "result.png")
    canvas.convert("RGB").save(result_path)
    logger.artifact_written(
        STAGE.value, result_path,
        f"{placed_count}/{len(top_layers)} 레이어 합성 완료",
    )

    logger.stage_pass(
        STAGE.value,
        f"element composite PASS placed={placed_count}/{len(top_layers)}",
        metrics={
            "placedCount": placed_count,
            "topLayerCount": len(top_layers),
            "scale": round(S, 4),
            "offsetX": round(Ox, 1),
            "offsetY": round(Oy, 1),
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "placedCount": placed_count,
            "scale": round(S, 4),
            "offsetX": round(Ox, 1),
            "offsetY": round(Oy, 1),
        },
        artifacts={"result": result_path},
    ), {"result_path": result_path}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_layer(parent, name: str):
    for layer in parent:
        if layer.name == name:
            return layer
        if layer.is_group():
            found = _find_layer(layer, name)
            if found:
                return found
    return None


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
