from __future__ import annotations

from typing import Tuple

from PIL import Image, ImageOps, ImageStat


DEFAULT_MIN_WIDTH = 1800


def adaptive_scale(image: Image.Image, min_width: int = DEFAULT_MIN_WIDTH) -> Image.Image:
    """Amplia imagem de forma adaptativa para melhorar legibilidade do OCR."""
    if image.width >= min_width:
        return image

    scale_factor = min(3.0, max(1.0, min_width / float(image.width)))
    new_size = (int(image.width * scale_factor), int(image.height * scale_factor))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def to_grayscale(image: Image.Image) -> Image.Image:
    return image.convert("L")


def binarize(image: Image.Image) -> Image.Image:
    """Aplica limiarização simples baseada na média para destacar texto."""
    contrasted = ImageOps.autocontrast(image)
    threshold = max(110, min(190, int(ImageStat.Stat(contrasted).mean[0])))
    return contrasted.point(lambda p: 255 if p > threshold else 0, mode="1").convert("L")


def deskew(image: Image.Image) -> Tuple[Image.Image, float]:
    """Corrige rotação usando orientação detectada pelo Tesseract (OSD)."""
    try:
        import pytesseract

        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        rotation = float(osd.get("rotate", 0) or 0)
    except Exception:
        rotation = 0.0

    if rotation:
        corrected = image.rotate(-rotation, expand=True, fillcolor=255)
        return corrected, rotation
    return image, 0.0


def preprocess_for_ocr(image: Image.Image) -> Tuple[Image.Image, float]:
    processed = adaptive_scale(image)
    processed = to_grayscale(processed)
    processed = binarize(processed)
    processed, rotation = deskew(processed)
    return processed, rotation
