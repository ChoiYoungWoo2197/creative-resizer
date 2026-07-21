"""Stage 20 data classes."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TextRun:
    text: str = ""
    font_family: str = ""
    font_size: float = 0.0
    font_weight: str = "normal"
    font_style: str = "normal"
    color: tuple = (0, 0, 0, 255)
    tracking: float = 0.0


@dataclass
class TypographyLayer:
    layer_id: str
    name: str
    role: str
    priority: str = "optional"
    text_content: str = ""
    text_runs: list[TextRun] = field(default_factory=list)
    font_family: str = ""
    font_size: float = 0.0
    font_weight: str = "normal"
    is_korean: bool = False
    is_text: bool = False
    bbox: dict = field(default_factory=dict)
    canvas_width: int = 0
    canvas_height: int = 0
    layer_order: int = 0
    group_name: str = ""
    preview_path: str = ""
    layer_type: str = "pixel"
    role_source: str = "name"  # name / position / group / heuristic / user


@dataclass
class CTAGroup:
    group_id: str
    text_layer_id: str
    text_content: str = ""
    bg_layer_id: str = ""
    icon_layer_id: str = ""
    bbox: dict = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class LayoutSlot:
    role: str
    x: int
    y: int
    w: int
    h: int
    mode: str = "contain"
    safe: bool = True
    z_order: int = 0


@dataclass
class TypographyResult:
    success: bool = False
    error: str = ""
    template_name: str = ""
    detected_roles: list[str] = field(default_factory=list)
    missing_roles: list[str] = field(default_factory=list)
    duplicate_text_removed: int = 0
    korean_layers: int = 0
    total_text_layers: int = 0
    cta_group_detected: bool = False
    safe_zone_pass: bool = True
    safe_zone_violations: list[str] = field(default_factory=list)
    quality_score: float = 0.0
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    output_path: str = ""
    used_layer_roles: list[str] = field(default_factory=list)
    extracted_layer_count: int = 0
    layout_score: float = 0.0
