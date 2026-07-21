"""Stage 20B: Korean font resolution with web-safe fallback mapping."""
from __future__ import annotations

# PSD font name → web-safe / system font name
_FONT_MAP: dict[str, str] = {
    # Korean fonts
    "NanumGothic": "NanumGothic, 나눔고딕, sans-serif",
    "NanumGothicBold": "NanumGothic, 나눔고딕, sans-serif",
    "NanumMyeongjo": "NanumMyeongjo, 나눔명조, serif",
    "NanumBarunGothic": "NanumBarunGothic, 나눔바른고딕, sans-serif",
    "AppleSDGothicNeo-Regular": "Apple SD Gothic Neo, sans-serif",
    "AppleSDGothicNeo-Bold": "Apple SD Gothic Neo, sans-serif",
    "KoPubWorldBatang": "KoPubWorld Batang, serif",
    "KoPubWorldDotum": "KoPubWorld Dotum, sans-serif",
    "SpoqaHanSansNeo": "Spoqa Han Sans Neo, sans-serif",
    "Noto Sans KR": "Noto Sans KR, sans-serif",
    "Noto Serif KR": "Noto Serif KR, serif",
    "SamsungOne": "Samsung One, sans-serif",
    "SpoqaHanSans": "Spoqa Han Sans, sans-serif",
    "GmarketSans": "GmarketSans, sans-serif",
    "SCDream": "SCDream, sans-serif",
    "Pretendard": "Pretendard, sans-serif",
    "PretendardVariable": "Pretendard, sans-serif",
    "YiSunShin": "YiSunShin, sans-serif",
    "Jalnan": "Jalnan, sans-serif",
    "Cafe24Ssurround": "Cafe24Ssurround, sans-serif",
    "RixYeolim": "RixYeolim, sans-serif",
    # Western fonts
    "Helvetica": "Helvetica, Arial, sans-serif",
    "HelveticaNeue": "Helvetica Neue, Helvetica, sans-serif",
    "Arial": "Arial, sans-serif",
    "Roboto": "Roboto, sans-serif",
    "Montserrat": "Montserrat, sans-serif",
    "GothamRounded": "Gotham Rounded, sans-serif",
}

_KOREAN_FALLBACK = "NanumGothic, sans-serif"
_DEFAULT_FALLBACK = "sans-serif"


def resolve_font(psd_font_name: str, is_korean: bool = False) -> str:
    """Map PSD font name to CSS-safe font stack.

    Returns the exact string if the PSD font has a known mapping,
    otherwise returns a Korean or generic fallback.
    """
    if not psd_font_name:
        return _KOREAN_FALLBACK if is_korean else _DEFAULT_FALLBACK
    # Exact match
    if psd_font_name in _FONT_MAP:
        return _FONT_MAP[psd_font_name]
    # Prefix match (handles Bold/Regular variants)
    for k, v in _FONT_MAP.items():
        if psd_font_name.lower().startswith(k.lower()):
            return v
    # Contains Korean family name
    if any(kw in psd_font_name for kw in ["Gothic", "고딕", "Myeongjo", "명조", "KR", "Korean"]):
        return f"{psd_font_name}, {_KOREAN_FALLBACK}"
    return psd_font_name if psd_font_name else (_KOREAN_FALLBACK if is_korean else _DEFAULT_FALLBACK)
