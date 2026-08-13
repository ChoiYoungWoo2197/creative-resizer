"""fal-ai/smart-resize 테스트용 fallback 입력 이미지 및 마스크 생성 유틸.

Usage:
    python scripts/create_fallback_input.py <input_image> [output_dir]

    input_image : 원본 시안 경로
    output_dir  : 출력 디렉토리 (기본값: 현재 디렉토리)

Outputs:
    fallback_input.jpg  — 원본 70% 축소 후 1250x560 흰색 캔버스 중앙 배치
    fallback_mask.png   — Safe Zone 중앙 검정(보존), 외곽 흰색(AI 채움) 마스크
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw


def create_fallback_input(input_image_path: str, output_path: str) -> None:
    orig_img = Image.open(input_image_path).convert("RGBA")
    canvas_w, canvas_h = 1250, 560

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))

    target_w = int(orig_img.width * 0.7)
    target_h = int(orig_img.height * 0.7)

    max_safe_w = 1000 - 250  # 750px
    max_safe_h = 510 - 50    # 460px

    if target_w > max_safe_w or target_h > max_safe_h:
        orig_img.thumbnail((max_safe_w, max_safe_h), Image.Resampling.LANCZOS)
        resized_img = orig_img
    else:
        resized_img = orig_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    paste_x = (canvas_w - resized_img.width) // 2
    paste_y = (canvas_h - resized_img.height) // 2

    canvas.paste(resized_img, (paste_x, paste_y), resized_img)
    canvas.convert("RGB").save(output_path, "JPEG")
    print(f"fallback_input 생성 완료: {output_path}")


def create_fallback_mask(
    output_path: str,
    canvas_w: int = 1250,
    canvas_h: int = 560,
    safe_x1: int = 250,
    safe_y1: int = 50,
    safe_x2: int = 1000,
    safe_y2: int = 510,
) -> None:
    mask = Image.new("L", (canvas_w, canvas_h), 255)
    draw = ImageDraw.Draw(mask)
    draw.rectangle([safe_x1, safe_y1, safe_x2, safe_y2], fill=0)
    mask.save(output_path)
    print(f"fallback_mask 생성 완료: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_fallback_input.py <input_image> [output_dir]")
        sys.exit(1)

    input_path = sys.argv[1]
    out_dir = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)

    create_fallback_input(input_path, str(out_dir / "fallback_input.jpg"))
    create_fallback_mask(str(out_dir / "fallback_mask.png"))
