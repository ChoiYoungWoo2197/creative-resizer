"""Stage 20A: PSD text layer extraction.

Extracts textContent, font metadata, TextRuns, and Korean status from PSD type layers.
Preserves original Korean text without modification (NFC normalization only).
"""
from __future__ import annotations
import unicodedata
import re

from .schemas import TextRun, TypographyLayer


_KOREAN_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏ꥠ-꥿ힰ-퟿]")
_COORD_SUFFIX_RE = re.compile(r"_-?\d+_-?\d+$")


def _is_korean(text: str) -> bool:
    return bool(_KOREAN_RE.search(text))


def _nfc(text: str) -> str:
    """NFC-normalize Korean text. No other modification."""
    return unicodedata.normalize("NFC", text) if text else ""


def _normalize_layer_name_text(name: str) -> str:
    """Strip PSD coordinate suffix (_x_y) and normalize underscores to spaces."""
    s = _COORD_SUFFIX_RE.sub("", name or "")
    s = re.sub(r"_+", " ", s).strip()
    return _nfc(s)


def _extract_engine_data(layer) -> dict:
    """Safely extract engine_data from psd-tools layer object."""
    try:
        return layer.engine_data or {}
    except Exception:
        return {}


def _extract_text_content(layer) -> str:
    """Extract and NFC-normalize text from psd-tools TYPE layer."""
    try:
        ed = _extract_engine_data(layer)
        txt = (
            ed.get("EngineDict", {})
              .get("Editor", {})
              .get("Text", {})
              .get("Txt ", "")
        )
        if txt:
            txt = txt.replace("\r", " ").replace("\n", " ").strip()
            return _nfc(txt)[:300]
    except Exception:
        pass
    # Fallback: psd-tools text property
    try:
        if hasattr(layer, "text"):
            t = layer.text
            if t:
                return _nfc(str(t).replace("\r", " ").replace("\n", " ").strip())[:300]
    except Exception:
        pass
    return ""


def _extract_font_metadata(layer) -> dict:
    """Extract font family, size, weight from PSD engine_data."""
    result = {"fontFamily": "", "fontSize": 0.0, "fontWeight": "normal", "fontStyle": "normal"}
    try:
        ed = _extract_engine_data(layer)
        engine_dict = ed.get("EngineDict", {})
        resources = engine_dict.get("ResourceDict", {})
        font_set = resources.get("FontSet", [])
        if font_set:
            first_font = font_set[0] if isinstance(font_set, list) else {}
            result["fontFamily"] = _nfc(str(first_font.get("Name", "") or ""))
        # StyleRun for size/weight
        style_runs = engine_dict.get("StyleRun", {})
        run_array = style_runs.get("RunArray", [])
        if run_array:
            first_run = run_array[0]
            sheet = first_run.get("StyleSheet", {}).get("StyleSheetData", {})
            size = sheet.get("FontSize", 0)
            if size:
                result["fontSize"] = round(float(size), 2)
            synth_bold = sheet.get("SyntheticBold", False)
            synth_italic = sheet.get("SyntheticItalic", False)
            result["fontWeight"] = "bold" if synth_bold else "normal"
            result["fontStyle"] = "italic" if synth_italic else "normal"
    except Exception:
        pass
    return result


def _extract_text_runs(layer) -> list[TextRun]:
    """Extract multiple TextRun items (multi-font text layers)."""
    runs: list[TextRun] = []
    try:
        ed = _extract_engine_data(layer)
        engine_dict = ed.get("EngineDict", {})
        style_runs = engine_dict.get("StyleRun", {})
        run_array = style_runs.get("RunArray", [])
        full_text = _extract_text_content(layer)
        pos = 0
        for run_item in run_array:
            run_len = run_item.get("RunLengthArray", 1)
            if isinstance(run_len, list):
                run_len = run_len[0] if run_len else 1
            run_text = full_text[pos:pos + run_len] if full_text else ""
            pos += run_len
            sheet = run_item.get("StyleSheet", {}).get("StyleSheetData", {})
            size = sheet.get("FontSize", 0.0)
            color_data = sheet.get("FillColor", {}).get("Values", [1, 0, 0, 0])
            if isinstance(color_data, (list, tuple)) and len(color_data) >= 4:
                # RGBA 0–1 → 0–255
                r, g, b, a = [int(v * 255) for v in color_data[:4]]
                color = (r, g, b, a)
            else:
                color = (0, 0, 0, 255)
            synth_bold = sheet.get("SyntheticBold", False)
            runs.append(TextRun(
                text=run_text,
                font_size=round(float(size), 2),
                font_weight="bold" if synth_bold else "normal",
                color=color,
            ))
    except Exception:
        pass
    if not runs and _extract_text_content(layer):
        runs.append(TextRun(text=_extract_text_content(layer)))
    return runs


def extract_text_layers(layers: list[dict]) -> list[dict]:
    """Annotate layers list with text metadata.

    For each text (type) layer, adds:
      - textContent (NFC-normalized)
      - fontFamily / fontSize / fontWeight / fontStyle
      - textRuns (list of TextRun-like dicts)
      - isKorean (bool)

    Non-text layers are returned unchanged.
    Returns new list (layers not mutated).
    """
    result = []
    for layer in layers:
        if not layer.get("isTextLayer") and layer.get("type") not in ("type", "text"):
            # Korean raster text layers: annotate via layer name fallback
            normalized = _normalize_layer_name_text(layer.get("name", ""))
            if normalized and _is_korean(normalized):
                result.append({
                    **layer,
                    "textContent": normalized,
                    "textContentSource": "layer_name_fallback",
                    "isKorean": True,
                    "fontFamily": "",
                    "fontSize": 0.0,
                    "fontWeight": "normal",
                    "fontStyle": "normal",
                    "textRuns": [],
                })
            else:
                result.append(layer)
            continue
        lo = layer.get("_layer_obj")
        text_content = layer.get("textContent", "")
        font_meta = {"fontFamily": "", "fontSize": 0.0, "fontWeight": "normal", "fontStyle": "normal"}
        text_runs: list[dict] = []
        if lo is not None:
            try:
                tc = _extract_text_content(lo)
                if tc:
                    text_content = tc
                fm = _extract_font_metadata(lo)
                font_meta.update(fm)
                runs = _extract_text_runs(lo)
                text_runs = [{"text": r.text, "fontSize": r.font_size,
                              "fontWeight": r.font_weight, "color": list(r.color)} for r in runs]
            except Exception:
                pass
        is_kor = _is_korean(text_content)
        result.append({
            **layer,
            "textContent": _nfc(text_content),
            "fontFamily": font_meta["fontFamily"],
            "fontSize": font_meta["fontSize"],
            "fontWeight": font_meta["fontWeight"],
            "fontStyle": font_meta["fontStyle"],
            "textRuns": text_runs,
            "isKorean": is_kor,
        })
    return result


def count_korean_layers(layers: list[dict]) -> int:
    return sum(1 for l in layers if l.get("isKorean"))


def get_text_summary(layers: list[dict]) -> dict:
    """Summary of text layer statistics."""
    text_layers = [l for l in layers if l.get("isTextLayer") or l.get("type") in ("type", "text")]
    return {
        "totalTextLayers": len(text_layers),
        "koreanLayers": count_korean_layers(text_layers),
        "textContents": [l.get("textContent", "")[:50] for l in text_layers],
    }
