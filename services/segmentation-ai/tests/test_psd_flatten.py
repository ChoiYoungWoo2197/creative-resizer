"""tests/test_psd_flatten.py — Stage 18.3 PSD flatten 파이프라인 테스트 (30개).

psd_flatten.py의 각 전략·메타·폴백 경로를 mock으로 검증한다.
실제 PSD 파일 불필요 — unittest.mock.patch로 psd-tools 동작 제어.
"""
from __future__ import annotations

import io
import sys
import os
import json
from unittest.mock import patch, MagicMock, call

import pytest
from PIL import Image

# psd_flatten 모듈 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from psd_flatten import (
    flatten_input,
    _fill_psd_tools_meta,
    _open_psd,
    _try_composite_strategies,
    _count_layers,
)


# ─── 유틸 ─────────────────────────────────────────────────────────────────────

def _png_bytes(w: int = 60, h: int = 40, color=(128, 64, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpg_bytes(w: int = 60, h: int = 40) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def _fake_psd_bytes() -> bytes:
    """8BPS 매직 + 더미 데이터 (psd-tools 미사용 경로 테스트용)."""
    return b"8BPS" + b"\x00" * 26


def _mock_psd_cls(composite_result=None, composite_error=None, layer_count=3):
    """psd-tools PSDImage 클래스 mock."""
    inst = MagicMock()
    inst.width = 100
    inst.height = 100
    inst.color_mode = "RGB"
    inst.__iter__ = MagicMock(return_value=iter(["l"] * layer_count))

    if composite_error is not None:
        inst.composite.side_effect = composite_error
    elif composite_result is None:
        inst.composite.return_value = Image.new("RGB", (100, 100), (10, 20, 30))
    else:
        inst.composite.return_value = composite_result

    cls = MagicMock()
    cls.open.return_value = inst
    return cls, inst


def _patch_psd_tools(meta_updates: dict, psd_cls=None):
    """_fill_psd_tools_meta를 patch하고 선택적으로 PSDImage도 교체."""
    def fill(m):
        m.update({
            "psdToolsInstalled": True,
            "psdToolsVersion": "1.9.31",
            "psdToolsImportSucceeded": True,
        })
        m.update(meta_updates)

    patches = [patch("psd_flatten._fill_psd_tools_meta", side_effect=fill)]
    if psd_cls is not None:
        patches.append(patch("psd_flatten.PSDImage", psd_cls, create=True))
    return patches


def _apply_patches(patches):
    """context manager 없이 patch 리스트 적용 (중첩 with 대신 사용)."""
    started = [p.__enter__() for p in patches]
    return patches, started


def _stop_patches(patches):
    for p in patches:
        p.__exit__(None, None, None)


# ─── 1. 비 PSD 이미지 경로 (5개) ──────────────────────────────────────────────

def test_png_returns_pillow_image_method():
    img, method, meta = flatten_input(_png_bytes(), "test.png")
    assert method == "pillow_image"


def test_png_returns_rgb_image():
    img, _, _ = flatten_input(_png_bytes(), "test.png")
    assert img.mode == "RGB"


def test_jpg_returns_pillow_image_method():
    _, method, _ = flatten_input(_jpg_bytes(), "test.jpg")
    assert method == "pillow_image"


def test_pillow_image_meta_source_type():
    _, _, meta = flatten_input(_png_bytes(), "x.png")
    assert meta["sourceType"] == "image"


def test_pillow_image_meta_flatten_method():
    _, _, meta = flatten_input(_png_bytes(), "x.png")
    assert meta["flattenMethod"] == "pillow_image"


# ─── 2. 공통 메타 필드 (5개) ──────────────────────────────────────────────────

def test_meta_execution_location_png():
    _, _, meta = flatten_input(_png_bytes())
    assert meta["flattenExecutionLocation"] == "segmentation_ai_container"


def test_meta_source_filename():
    _, _, meta = flatten_input(_png_bytes(), "myfile.png")
    assert meta["sourceFilename"] == "myfile.png"


def test_meta_source_file_size_bytes():
    data = _png_bytes()
    _, _, meta = flatten_input(data, "x.png")
    assert meta["sourceFileSizeBytes"] == len(data)


def test_meta_flatten_ms_present():
    _, _, meta = flatten_input(_png_bytes())
    assert "flattenMs" in meta
    assert meta["flattenMs"] >= 0


def test_meta_warnings_is_list():
    _, _, meta = flatten_input(_png_bytes())
    assert isinstance(meta["warnings"], list)


# ─── 3. PSD 매직 감지 (2개) ───────────────────────────────────────────────────

def test_psd_magic_sets_source_type_psd():
    """b'8BPS' 시작 → sourceType=psd."""
    psd_bytes = _fake_psd_bytes()
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": False, "psdToolsImportSucceeded": False,
    }))
    img_open_patch = patch("psd_flatten.Image.open", return_value=Image.new("RGB", (10, 10)))
    with fill_patch, img_open_patch:
        _, _, meta = flatten_input(psd_bytes, "test.psd")
    assert meta["sourceType"] == "psd"


def test_psd_magic_sets_source_mime_type():
    psd_bytes = _fake_psd_bytes()
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": False, "psdToolsImportSucceeded": False,
    }))
    img_open_patch = patch("psd_flatten.Image.open", return_value=Image.new("RGB", (10, 10)))
    with fill_patch, img_open_patch:
        _, _, meta = flatten_input(psd_bytes, "test.psd")
    assert meta["sourceMimeType"] == "image/vnd.adobe.photoshop"


# ─── 4. _fill_psd_tools_meta (4개) ────────────────────────────────────────────

def test_fill_meta_installed_true():
    meta = {}
    with patch("importlib.metadata.version", return_value="1.9.31"):
        with patch.dict("sys.modules", {"psd_tools": MagicMock()}):
            _fill_psd_tools_meta(meta)
    assert meta["psdToolsInstalled"] is True


def test_fill_meta_version_populated():
    meta = {}
    with patch("importlib.metadata.version", return_value="1.9.31"):
        with patch.dict("sys.modules", {"psd_tools": MagicMock()}):
            _fill_psd_tools_meta(meta)
    assert meta["psdToolsVersion"] == "1.9.31"


def test_fill_meta_not_installed():
    meta = {}
    with patch("importlib.metadata.version", side_effect=Exception("not found")):
        _fill_psd_tools_meta(meta)
    assert meta["psdToolsInstalled"] is False
    assert meta["psdToolsVersion"] is None
    assert meta["psdToolsImportSucceeded"] is False


def test_fill_meta_import_fail_sets_false():
    meta = {}
    with patch("importlib.metadata.version", return_value="1.9.31"):
        with patch.dict("sys.modules", {"psd_tools": None}):
            _fill_psd_tools_meta(meta)
    assert meta["psdToolsInstalled"] is True
    assert meta["psdToolsImportSucceeded"] is False


# ─── 5. _try_composite_strategies (5개) ───────────────────────────────────────

def test_composite_default_success():
    mock_psd = MagicMock()
    mock_psd.composite.return_value = Image.new("RGB", (80, 60))
    result, strategy, errors = _try_composite_strategies(mock_psd)
    assert result is not None
    assert strategy == "composite_default"
    assert "composite_default" not in errors


def test_composite_none_fallback_to_no_icc():
    mock_psd = MagicMock()
    call_count = [0]
    def side(**kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return None
        return Image.new("RGB", (80, 60))
    mock_psd.composite.side_effect = side
    result, strategy, errors = _try_composite_strategies(mock_psd)
    assert strategy == "composite_no_icc"
    assert result is not None


def test_composite_error_fallback_to_force():
    mock_psd = MagicMock()
    call_count = [0]
    def side(**kw):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise RuntimeError("unsupported layer")
        return Image.new("RGBA", (80, 60))
    mock_psd.composite.side_effect = side
    result, strategy, errors = _try_composite_strategies(mock_psd)
    assert strategy == "composite_force"
    assert "composite_default" in errors
    assert "composite_no_icc" in errors


def test_composite_all_fail_returns_none():
    mock_psd = MagicMock()
    mock_psd.composite.side_effect = RuntimeError("fail all")
    mock_psd.topil.side_effect = RuntimeError("topil fail")
    result, strategy, errors = _try_composite_strategies(mock_psd)
    assert result is None
    assert strategy == ""
    assert len(errors) >= 3


def test_composite_topil_fallback():
    mock_psd = MagicMock()
    mock_psd.composite.side_effect = RuntimeError("fail")
    mock_psd.topil.return_value = Image.new("RGB", (80, 60))
    result, strategy, errors = _try_composite_strategies(mock_psd)
    assert strategy == "topil"
    assert result is not None


# ─── 6. _count_layers (2개) ───────────────────────────────────────────────────

def test_count_layers_success():
    mock_psd = MagicMock()
    mock_psd.__iter__ = MagicMock(return_value=iter(["l1", "l2", "l3"]))
    assert _count_layers(mock_psd) == 3


def test_count_layers_error_returns_minus_one():
    mock_psd = MagicMock()
    mock_psd.__iter__ = MagicMock(side_effect=RuntimeError("err"))
    assert _count_layers(mock_psd) == -1


# ─── 7. psd_tools_composite 경로 (3개) ────────────────────────────────────────

def test_flatten_psd_composite_success_method():
    """composite 성공 → flatten_method=psd_tools_composite."""
    psd_bytes = _fake_psd_bytes()
    cls, inst = _mock_psd_cls()
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": True, "psdToolsVersion": "1.9.31", "psdToolsImportSucceeded": True,
    }))
    # psd_tools.PSDImage 자체를 패치 — _open_psd 내 "from psd_tools import PSDImage"가 이걸 가져감
    psd_patch = patch("psd_tools.PSDImage", cls)
    with fill_patch, psd_patch:
        _, method, meta = flatten_input(psd_bytes, "test.psd")
    assert method == "psd_tools_composite"
    assert meta["psdCompositeSucceeded"] is True


def test_flatten_psd_composite_meta_width_height():
    psd_bytes = _fake_psd_bytes()
    cls, inst = _mock_psd_cls()
    inst.width = 200
    inst.height = 150
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": True, "psdToolsVersion": "1.9.31", "psdToolsImportSucceeded": True,
    }))
    psd_patch = patch("psd_tools.PSDImage", cls)
    with fill_patch, psd_patch:
        img, _, meta = flatten_input(psd_bytes, "test.psd")
    assert meta["psdWidth"] == 200
    assert meta["psdHeight"] == 150


def test_flatten_psd_composite_result_is_rgb():
    psd_bytes = _fake_psd_bytes()
    cls, inst = _mock_psd_cls(composite_result=Image.new("RGBA", (50, 50)))
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": True, "psdToolsVersion": "1.9.31", "psdToolsImportSucceeded": True,
    }))
    psd_patch = patch("psd_tools.PSDImage", cls)
    with fill_patch, psd_patch:
        img, method, _ = flatten_input(psd_bytes, "test.psd")
    assert img.mode == "RGB"
    assert method == "psd_tools_composite"


# ─── 8. psd_tools_merged 경로 (2개) ───────────────────────────────────────────

def test_flatten_psd_tools_merged_when_composite_fails():
    """composite 전부 실패 + psd-tools open 성공 → psd_tools_merged."""
    psd_bytes = _fake_psd_bytes()
    cls, inst = _mock_psd_cls(composite_error=RuntimeError("complex layers"))
    inst.topil = MagicMock(side_effect=RuntimeError("topil fail"))
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": True, "psdToolsVersion": "1.9.31", "psdToolsImportSucceeded": True,
    }))
    psd_patch = patch("psd_tools.PSDImage", cls)
    pillow_patch = patch("psd_flatten.Image.open", return_value=Image.new("RGB", (80, 60)))
    with fill_patch, psd_patch, pillow_patch:
        _, method, meta = flatten_input(psd_bytes, "test.psd")
    assert method == "psd_tools_merged"
    assert meta["psdCompositeSucceeded"] is False
    assert meta.get("flattenMethod") == "psd_tools_merged"


def test_flatten_psd_tools_merged_result_is_rgb():
    psd_bytes = _fake_psd_bytes()
    cls, inst = _mock_psd_cls(composite_error=RuntimeError("fail"))
    inst.topil = MagicMock(side_effect=RuntimeError("topil fail"))
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": True, "psdToolsVersion": "1.9.31", "psdToolsImportSucceeded": True,
    }))
    psd_patch = patch("psd_tools.PSDImage", cls)
    pillow_patch = patch("psd_flatten.Image.open", return_value=Image.new("RGB", (80, 60)))
    with fill_patch, psd_patch, pillow_patch:
        img, method, _ = flatten_input(psd_bytes, "test.psd")
    assert img.mode == "RGB"
    assert method == "psd_tools_merged"


# ─── 9. pillow_psd_fallback 경로 (2개) ────────────────────────────────────────

def test_flatten_pillow_fallback_when_not_installed():
    psd_bytes = _fake_psd_bytes()
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": False, "psdToolsVersion": None, "psdToolsImportSucceeded": False,
    }))
    pillow_patch = patch("psd_flatten.Image.open", return_value=Image.new("RGB", (10, 10)))
    with fill_patch, pillow_patch:
        _, method, meta = flatten_input(psd_bytes, "nopsd.psd")
    assert method == "pillow_psd_fallback"
    assert meta["psdCompositeSucceeded"] is False


def test_flatten_pillow_fallback_meta_flatten_method():
    psd_bytes = _fake_psd_bytes()
    fill_patch = patch("psd_flatten._fill_psd_tools_meta", side_effect=lambda m: m.update({
        "psdToolsInstalled": False, "psdToolsVersion": None, "psdToolsImportSucceeded": False,
    }))
    pillow_patch = patch("psd_flatten.Image.open", return_value=Image.new("RGB", (10, 10)))
    with fill_patch, pillow_patch:
        _, _, meta = flatten_input(psd_bytes, "nopsd.psd")
    assert meta["flattenMethod"] == "pillow_psd_fallback"
