"""PSD 레이어 트리 읽기 — psd-tools 기반.

PSD 파일을 열어 레이어 트리를 재귀 순회하고 각 레이어의
name·kind·bounds·role을 stdout(docker logs)에 출력한다.
GPT-4o 호출 없음, 디스크 I/O 최소화.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

_STAGE = StageName.ELEMENT_ANALYSIS.value
_OUTPUT_SUBDIR = Path("clean_v1") / "02_psd_layers"

# 레이어명 prefix → role 매핑 (대괄호 없는 이름이 기본)
_PREFIX_ROLE: dict[str, str] = {
    "title-group": "title-group",
    "product-group": "product-group",
    "title": "title",
    "body": "body",
    "badge": "badge",
    "product": "product",
    "person": "person",
    "logo": "logo",
    "bg": "bg",
}


@dataclass
class LayerInfo:
    name: str
    kind: str                        # 'group' | 'type' | 'pixel' | 'smartobject' | 'shape' | 'unknown'
    role: str                        # prefix 기반 역할, 매핑 없으면 'unknown'
    depth: int                       # 트리 깊이 (루트=0)
    bbox: dict[str, int] | None      # group이면 None (폴더라 bbox 불필요)
    visible: bool
    children_count: int


def _parse_role(layer_name: str) -> str:
    # 대괄호 컨벤션([logo], [person] 등) 및 공백 제거 후 매칭
    lower = layer_name.lower().strip("[] \t")
    for prefix, role in _PREFIX_ROLE.items():
        if lower.startswith(prefix):
            return role
    return "unknown"


def _layer_kind(layer: Any) -> str:
    # psd-tools: layer.kind은 이미 string ('pixel', 'group', 'type', 'smartobject' 등)
    try:
        return str(layer.kind)
    except Exception:
        return "unknown"


def _traverse(layer: Any, depth: int, result: list[LayerInfo]) -> None:
    kind = _layer_kind(layer)
    is_group = layer.is_group()
    children = list(layer) if is_group else []
    if is_group:
        bbox = None
    else:
        b = layer.bbox  # psd-tools: plain tuple (left, top, right, bottom)
        bbox = {"left": b[0], "top": b[1], "right": b[2], "bottom": b[3]}
    result.append(LayerInfo(
        name=layer.name,
        kind=kind,
        role=_parse_role(layer.name),
        depth=depth,
        bbox=bbox,
        visible=layer.is_visible(),
        children_count=len(children),
    ))
    for child in children:
        _traverse(child, depth + 1, result)


def read_layers(
    psd_path: str,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
    psd=None,
) -> tuple[StageResult, dict[str, Any] | None]:
    """PSD 레이어 트리를 읽고 로그로 출력한다.

    Returns:
        (StageResult, result_dict | None)
        result_dict keys: layers_json_path, layer_count, layers
    """
    logger.stage_start(_STAGE, f"PSD layer read - {os.path.basename(psd_path)}")

    try:
        from psd_tools import PSDImage
    except ImportError:
        return _fail(logger, "PSD_TOOLS_NOT_INSTALLED", "psd-tools 라이브러리 미설치")

    if not os.path.isfile(psd_path):
        return _fail(logger, "PSD_FILE_NOT_FOUND", f"PSD 파일 없음: {psd_path}")

    if psd is None:
        try:
            psd = PSDImage.open(psd_path)
        except Exception as exc:
            return _fail(logger, "PSD_OPEN_FAILED", f"PSD 열기 실패: {exc}")

    layers: list[LayerInfo] = []
    for top_layer in psd:
        _traverse(top_layer, depth=0, result=layers)

    # 레이어별 로그 — docker logs / server logs에서 가독성 우선
    psd_name = os.path.basename(psd_path)
    print(
        f"[{_STAGE}][PSD_LAYER_TREE] jobId={job_id} file={psd_name} "
        f"size={psd.width}x{psd.height} total_layers={len(layers)}",
        flush=True,
    )
    for info in layers:
        indent = "  " * info.depth
        bounds = (
            f"({info.bbox['left']},{info.bbox['top']},{info.bbox['right']},{info.bbox['bottom']})"
            if info.bbox else "-"
        )
        print(
            f"[{_STAGE}][PSD_LAYER] {indent}[{info.kind}] {info.name!r} "
            f"role={info.role} bounds={bounds} visible={info.visible}",
            flush=True,
        )

    # 레이어 목록 JSON 저장
    out_path = str(Path(output_dir) / job_id / _OUTPUT_SUBDIR / "layers.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "jobId": job_id,
                "psdPath": psd_path,
                "psdSize": {"width": psd.width, "height": psd.height},
                "layerCount": len(layers),
                "layers": [asdict(info) for info in layers],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.artifact_written(_STAGE, out_path, f"PSD layers JSON - {len(layers)} layers")

    detected_roles = sorted({info.role for info in layers if info.role != "unknown"})
    sr = StageResult(
        stage=StageName.ELEMENT_ANALYSIS,
        status=PipelineStatus.PASS,
        metrics={
            "layerCount": len(layers),
            "psdWidth": psd.width,
            "psdHeight": psd.height,
            "detectedRoles": detected_roles,
        },
    )
    logger.stage_pass(
        _STAGE,
        f"PSD layer read complete - {len(layers)} layers, roles={detected_roles}",
        metrics=sr.metrics,
    )
    return sr, {
        "layers_json_path": out_path,
        "layer_count": len(layers),
        "layers": layers,
    }


def _fail(
    logger: PipelineLogger,
    code: str,
    message: str,
) -> tuple[StageResult, None]:
    logger.stage_fail(_STAGE, code, message)
    return StageResult(
        stage=StageName.ELEMENT_ANALYSIS,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
