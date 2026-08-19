"""TYPE G P4 — Smart Layout Engine.

종횡비 기반 3-모드 레이아웃 분기:
  AP_LAYOUT        : 0.5 < tgt_ar < 2.5 and ratio < 1.5  — 기존 letterbox 중앙 투영
  EDGE_ANCHORING   : tgt_ar >= 2.5 or ratio >= 1.5        — 가로 띠배너, 역할별 앵커링
  VERTICAL_STACKING: tgt_ar <= 0.5                        — 세로 띠배너, 역할별 수직 스택

입력 layers: psd_layer_reader.LayerInfo 리스트 (bbox는 dict 형식)
출력: {(name, depth): {new_x1, new_y1, new_x2, new_y2, scale_used}}
"""
from __future__ import annotations

from typing import Any

from clean_pipeline.psd.psd_layer_reader import LayerInfo

# bg 역할이지만 role="unknown"인 픽셀 레이어 명시 제외 목록
_BG_PIXEL_NAMES: frozenset[str] = frozenset({"배경", "background"})


class SmartLayoutEngine:
    def __init__(
        self,
        source_size: tuple[int, int],
        target_size: tuple[int, int],
        margin: int = 30,
    ) -> None:
        self.src_w, self.src_h = source_size
        self.tgt_w, self.tgt_h = target_size
        self.margin = margin

        self.src_ar = self.src_w / self.src_h
        self.tgt_ar = self.tgt_w / self.tgt_h
        self.ratio = self.tgt_ar / self.src_ar

    def determine_mode(self) -> str:
        """종횡비 기반 레이아웃 모드 결정.

        tgt_ar <= 0.5 → VERTICAL_STACKING (극단적 세로 띠배너)
        tgt_ar >= 2.5 or ratio >= 1.5 → EDGE_ANCHORING (극단적 가로 띠배너)
        그 외 → AP_LAYOUT (일반 배너, 정방형 포함)
        """
        if self.tgt_ar <= 0.5:
            return "VERTICAL_STACKING"
        if self.tgt_ar >= 2.5 or self.ratio >= 1.5:
            return "EDGE_ANCHORING"
        return "AP_LAYOUT"

    def calculate_layout(
        self, layers: list[LayerInfo]
    ) -> dict[tuple[str, int], dict[str, Any]]:
        """레이어 목록을 받아 각 레이어의 변환 좌표를 반환.

        _fill_missing_bboxes를 먼저 실행해 그룹 레이어의 bbox를 채운다.
        """
        self._fill_missing_bboxes(layers)

        # AP_LAYOUT은 element_compositor.py의 기존 depth=0 경로에서 처리하므로
        # calculate_layout은 EDGE_ANCHORING / VERTICAL_STACKING 전용으로만 호출된다.
        targets = self._filter_targets(layers)
        mode = self.determine_mode()

        if mode == "EDGE_ANCHORING":
            return self._process_edge_anchoring(targets)
        if mode == "VERTICAL_STACKING":
            return self._process_vertical_stacking(targets)
        return self._process_ap_layout(targets)

    # ── 타겟 필터링 ────────────────────────────────────────────────────────────

    def _filter_targets(self, layers: list[LayerInfo]) -> list[LayerInfo]:
        """배치 대상 leaf 레이어 선별.

        - kind=group은 제외 (badge만 예외: 자식이 unknown으로 가려져 있어 그룹 단위 처리)
        - bg 픽셀 레이어 명시 제외 (배경, background)
        - role=unknown은 제외 (단, name="model"/"person"은 폴백 허용)
        """
        result = []
        for l in layers:
            if not getattr(l, "visible", True):
                continue
            if l.bbox is None:
                continue
            if l.name in _BG_PIXEL_NAMES:
                continue
            if l.kind == "group" and l.role != "badge":
                continue
            if l.role in ("bg", "unknown") and l.name.lower() not in ("model", "person"):
                continue
            result.append(l)
        return result

    # ── 1. AP_LAYOUT ──────────────────────────────────────────────────────────

    def _process_ap_layout(
        self, layers: list[LayerInfo]
    ) -> dict[tuple[str, int], dict[str, Any]]:
        S = min(self.tgt_w / self.src_w, self.tgt_h / self.src_h)
        Ox = (self.tgt_w - self.src_w * S) / 2.0
        Oy = (self.tgt_h - self.src_h * S) / 2.0
        results: dict[tuple[str, int], dict[str, Any]] = {}
        for l in layers:
            x1, y1, x2, y2 = _unpack(l.bbox)
            nx1 = int(round(x1 * S + Ox))
            ny1 = int(round(y1 * S + Oy))
            nx2 = int(round(nx1 + (x2 - x1) * S))
            ny2 = int(round(ny1 + (y2 - y1) * S))
            results[(l.name, l.depth)] = {
                "new_x1": nx1, "new_y1": ny1, "new_x2": nx2, "new_y2": ny2,
                "scale_used": round(S, 4),
            }
        return results

    # ── 2. EDGE_ANCHORING ─────────────────────────────────────────────────────

    def _process_edge_anchoring(
        self, layers: list[LayerInfo]
    ) -> dict[tuple[str, int], dict[str, Any]]:
        # 높이 꽉 채움 스케일 (최대 1.0 — 확대 금지)
        S = min(1.0, self.tgt_h / self.src_h)
        Oy = (self.tgt_h - self.src_h * S) / 2.0

        results: dict[tuple[str, int], dict[str, Any]] = {}
        logo_layer = next((l for l in layers if l.role == "logo"), None)
        prod_layer = next((l for l in layers if l.role == "product"), None)
        model_layer = next(
            (l for l in layers
             if l.role in ("person", "model") or l.name in ("model", "person")),
            None,
        )
        text_layers = [
            l for l in layers
            if (l.role in ("title", "body") and l.kind != "group") or l.role == "badge"
        ]

        # [우측] Model — 캔버스 우측 끝까지
        model_left_limit = self.tgt_w - self.margin
        if model_layer:
            x1, y1, x2, y2 = _unpack(model_layer.bbox)
            w = (x2 - x1) * S
            nx2 = self.tgt_w
            nx1 = int(round(nx2 - w))
            ny1 = int(round(y1 * S + Oy))
            ny2 = int(round(ny1 + (y2 - y1) * S))
            results[(model_layer.name, model_layer.depth)] = _coords(nx1, ny1, nx2, ny2, S)
            model_left_limit = nx1

        # [좌측] Product — 세로 중앙
        left_anchor_max = self.margin
        if prod_layer:
            x1, y1, x2, y2 = _unpack(prod_layer.bbox)
            w, h = (x2 - x1) * S, (y2 - y1) * S
            nx1 = self.margin
            ny1 = int(round((self.tgt_h - h) / 2.0))
            nx2 = int(round(nx1 + w))
            ny2 = int(round(ny1 + h))
            results[(prod_layer.name, prod_layer.depth)] = _coords(nx1, ny1, nx2, ny2, S)
            left_anchor_max = nx2 + self.margin

        # [중앙] Logo + Text — union 블록으로 가로/세로 완전 중앙 정렬
        # 원본 상대 Y 위치를 보존하면서 블록 전체를 중앙 배치
        center_items: list[LayerInfo] = []
        if logo_layer:
            center_items.append(logo_layer)
        center_items.extend(text_layers)

        if center_items:
            ux1 = min(_unpack(l.bbox)[0] for l in center_items)
            uy1 = min(_unpack(l.bbox)[1] for l in center_items)
            ux2 = max(_unpack(l.bbox)[2] for l in center_items)
            uy2 = max(_unpack(l.bbox)[3] for l in center_items)

            union_w = (ux2 - ux1) * S
            union_h = (uy2 - uy1) * S

            avail_w = max(100, model_left_limit - left_anchor_max)

            # 가용폭 초과 시 center_items만 추가 축소
            text_scale = S
            if union_w > avail_w:
                text_scale = S * (avail_w / union_w)
                union_w = (ux2 - ux1) * text_scale
                union_h = (uy2 - uy1) * text_scale

            new_union_x1 = left_anchor_max + (avail_w - union_w) / 2.0
            new_union_y1 = (self.tgt_h - union_h) / 2.0

            for l in center_items:
                x1, y1, x2, y2 = _unpack(l.bbox)
                nx1 = int(round(new_union_x1 + (x1 - ux1) * text_scale))
                ny1 = int(round(new_union_y1 + (y1 - uy1) * text_scale))
                nx2 = int(round(nx1 + (x2 - x1) * text_scale))
                ny2 = int(round(ny1 + (y2 - y1) * text_scale))
                results[(l.name, l.depth)] = _coords(nx1, ny1, nx2, ny2, text_scale)

        return results

    # ── 3. VERTICAL_STACKING ──────────────────────────────────────────────────

    def _process_vertical_stacking(
        self, layers: list[LayerInfo]
    ) -> dict[tuple[str, int], dict[str, Any]]:
        results: dict[tuple[str, int], dict[str, Any]] = {}
        # 가로 폭 기준 피팅 스케일 (최대 1.0)
        S_fit = min(1.0, (self.tgt_w - self.margin * 2) / self.src_w)

        logo_layer = next((l for l in layers if l.role == "logo"), None)
        prod_layer = next((l for l in layers if l.role == "product"), None)
        model_layer = next(
            (l for l in layers
             if l.role in ("person", "model") or l.name in ("model", "person")),
            None,
        )
        text_layers = [
            l for l in layers
            if (l.role in ("title", "body") and l.kind != "group") or l.role == "badge"
        ]

        top_offset = self.margin

        # [상단] Product — 가로 중앙
        if prod_layer:
            x1, y1, x2, y2 = _unpack(prod_layer.bbox)
            w, h = (x2 - x1) * S_fit, (y2 - y1) * S_fit
            nx1 = int(round((self.tgt_w - w) / 2.0))
            ny1 = top_offset
            nx2 = int(round(nx1 + w))
            ny2 = int(round(ny1 + h))
            results[(prod_layer.name, prod_layer.depth)] = _coords(nx1, ny1, nx2, ny2, S_fit)
            top_offset = ny2 + self.margin

        # [상단] Logo — Product 아래, 가로 중앙
        if logo_layer:
            x1, y1, x2, y2 = _unpack(logo_layer.bbox)
            w, h = (x2 - x1) * S_fit, (y2 - y1) * S_fit
            nx1 = int(round((self.tgt_w - w) / 2.0))
            ny1 = top_offset
            nx2 = int(round(nx1 + w))
            ny2 = int(round(ny1 + h))
            results[(logo_layer.name, logo_layer.depth)] = _coords(nx1, ny1, nx2, ny2, S_fit)
            top_offset = ny2 + self.margin

        # [하단] Text Group — union bbox 기준, 하단 앵커
        bottom_offset = self.tgt_h - self.margin
        if text_layers:
            ux1 = min(_unpack(l.bbox)[0] for l in text_layers)
            uy1 = min(_unpack(l.bbox)[1] for l in text_layers)
            ux2 = max(_unpack(l.bbox)[2] for l in text_layers)
            uy2 = max(_unpack(l.bbox)[3] for l in text_layers)
            union_w = (ux2 - ux1) * S_fit
            union_h = (uy2 - uy1) * S_fit
            new_union_y1 = bottom_offset - union_h
            new_union_x1 = (self.tgt_w - union_w) / 2.0
            for l in text_layers:
                x1, y1, x2, y2 = _unpack(l.bbox)
                nx1 = int(round(new_union_x1 + (x1 - ux1) * S_fit))
                ny1 = int(round(new_union_y1 + (y1 - uy1) * S_fit))
                nx2 = int(round(nx1 + (x2 - x1) * S_fit))
                ny2 = int(round(ny1 + (y2 - y1) * S_fit))
                results[(l.name, l.depth)] = _coords(nx1, ny1, nx2, ny2, S_fit)
            bottom_offset = int(round(new_union_y1)) - self.margin

        # [중앙] Model — 가용 높이 채움, 우측 밀착
        if model_layer:
            x1, y1, x2, y2 = _unpack(model_layer.bbox)
            lw, lh = x2 - x1, y2 - y1
            available_h = max(100, bottom_offset - top_offset)
            # 높이/폭 중 캔버스를 벗어나지 않는 상한 선택
            S_fill = max(S_fit, min(available_h / lh, self.tgt_w / lw))
            w, h = lw * S_fill, lh * S_fill
            nx1 = max(0, int(round(self.tgt_w - w)))
            ny1 = top_offset
            nx2 = int(round(nx1 + w))
            ny2 = int(round(ny1 + h))
            results[(model_layer.name, model_layer.depth)] = _coords(nx1, ny1, nx2, ny2, S_fill)

        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fill_missing_bboxes(self, layers: list[LayerInfo]) -> None:
        """bbox가 None인 그룹의 자식 레이어 bbox를 Union으로 채운다.

        레이어 목록이 DFS 순서로 정렬되어 있다는 전제 하에 동작한다.
        """
        for i, group in enumerate(layers):
            if group.bbox is not None:
                continue
            child_bboxes: list[dict] = []
            for j in range(i + 1, len(layers)):
                child = layers[j]
                if child.depth <= group.depth:
                    break
                if child.bbox is not None:
                    child_bboxes.append(child.bbox)
            if child_bboxes:
                group.bbox = {
                    "left":   min(cb["left"]   for cb in child_bboxes),
                    "top":    min(cb["top"]     for cb in child_bboxes),
                    "right":  max(cb["right"]   for cb in child_bboxes),
                    "bottom": max(cb["bottom"]  for cb in child_bboxes),
                }


# ── Module-level helpers ───────────────────────────────────────────────────────


def _unpack(bbox: dict[str, int]) -> tuple[int, int, int, int]:
    """LayerInfo.bbox dict → (left, top, right, bottom) 언패킹."""
    return bbox["left"], bbox["top"], bbox["right"], bbox["bottom"]


def _coords(
    nx1: int, ny1: int, nx2: int, ny2: int, scale: float
) -> dict[str, Any]:
    return {
        "new_x1": nx1, "new_y1": ny1,
        "new_x2": nx2, "new_y2": ny2,
        "scale_used": round(scale, 4),
    }
