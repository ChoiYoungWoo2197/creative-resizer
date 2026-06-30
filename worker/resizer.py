from psd_tools import PSDImage
from PIL import Image
import os


def load_psd_as_image(psd_path: str) -> Image.Image:
    ext = os.path.splitext(psd_path)[1].lower()

    if ext in ('.psd', '.psb'):
        try:
            psd = PSDImage.open(psd_path)
        except Exception as e:
            msg = str(e)
            if "version" in msg.lower():
                raise ValueError(
                    f"지원하지 않는 PSD 포맷입니다. Photoshop에서 '다른 이름으로 저장 > Photoshop (*.psd)' 로 재저장 후 시도하세요. (원인: {msg})"
                )
            raise ValueError(f"PSD 파일을 열 수 없습니다: {msg}")

        img = psd.composite()
        if img is None:
            raise ValueError("PSD 합성 이미지를 생성할 수 없습니다. 레이어가 비어있거나 잠겨 있을 수 있습니다.")
    else:
        try:
            img = Image.open(psd_path)
            img.load()
        except Exception as e:
            raise ValueError(f"이미지 파일을 열 수 없습니다: {e}")

    if img.mode in ("CMYK", "P", "LAB"):
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    return img


def resize_cover(img: Image.Image, width: int, height: int) -> Image.Image:
    src_ratio = img.width / img.height
    dst_ratio = width / height

    if src_ratio > dst_ratio:
        new_h = height
        new_w = int(img.width * height / img.height)
    else:
        new_w = width
        new_h = int(img.height * width / img.width)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


def resize_contain(img: Image.Image, width: int, height: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    offset = ((width - img.width) // 2, (height - img.height) // 2)
    canvas.paste(img, offset)
    return canvas


def resize_blur_bg(img: Image.Image, width: int, height: int) -> Image.Image:
    from PIL import ImageFilter

    bg = img.copy().resize((width, height), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=20))

    img.thumbnail((width, height), Image.LANCZOS)
    offset = ((width - img.width) // 2, (height - img.height) // 2)

    if bg.mode != "RGBA":
        bg = bg.convert("RGBA")
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    bg.paste(img, offset, img)
    return bg


RESIZE_FUNCS = {
    "cover": resize_cover,
    "contain": resize_contain,
    "blur-bg": resize_blur_bg,
}


def generate(psd_path: str, specs: list[dict], resize_mode: str,
             output_format: str, output_dir: str) -> list[dict]:
    img = load_psd_as_image(psd_path)
    resize_fn = RESIZE_FUNCS.get(resize_mode, resize_cover)
    results = []

    for spec in specs:
        media = spec["media"]
        w = spec["width"]
        h = spec["height"]
        slug = spec.get("slug", "")
        name = spec.get("name", "")

        resized = resize_fn(img, w, h)

        if output_format in ("jpg", "jpeg"):
            resized = resized.convert("RGB")
            ext = "jpg"
        else:
            ext = output_format

        slug_part = f"_{slug}" if slug else ""
        filename = f"{media}{slug_part}_{w}x{h}.{ext}"
        out_path = os.path.join(output_dir, filename)
        os.makedirs(output_dir, exist_ok=True)
        resized.save(out_path)

        results.append({
            "media": media,
            "name": name,
            "slug": slug,
            "width": w,
            "height": h,
            "fileName": filename,
            "filePath": out_path,
        })

    return results
