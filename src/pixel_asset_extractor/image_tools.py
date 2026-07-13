from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from PySide6.QtGui import QImage, QPixmap

from .exceptions import CropError, ImageLoadError
from .models import CropRect


@dataclass(frozen=True, slots=True)
class ImageAnalysis:
    width: int
    height: int
    mean_luminance: float
    edge_pixel_count: int


def load_png(path: str | Path) -> Image.Image:
    file_path = Path(path)
    if not file_path.exists():
        raise ImageLoadError(f"Image does not exist: {file_path}")
    if file_path.suffix.lower() != ".png":
        raise ImageLoadError(f"Only PNG files are supported: {file_path}")

    try:
        with Image.open(file_path) as image:
            image.load()
            return image.convert("RGBA")
    except Exception as exc:  # pragma: no cover - Pillow raises several subclasses
        raise ImageLoadError(f"Failed to load PNG: {file_path}") from exc


def pil_image_to_qpixmap(image: Image.Image) -> QPixmap:
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(
        data,
        rgba.width,
        rgba.height,
        rgba.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    return QPixmap.fromImage(qimage.copy())


def crop_image(image: Image.Image, crop_rect: CropRect) -> Image.Image:
    if not crop_rect.is_valid():
        raise CropError("Crop rectangle must have a positive width and height")

    width, height = image.size
    left = crop_rect.x
    top = crop_rect.y
    right = left + crop_rect.width
    bottom = top + crop_rect.height

    if left < 0 or top < 0 or right > width or bottom > height:
        raise CropError("Crop rectangle is outside the image bounds")

    return image.crop((left, top, right, bottom))


def export_crop(image: Image.Image, crop_rect: CropRect, output_path: str | Path) -> Path:
    cropped = crop_image(image, crop_rect)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(destination, format="PNG")
    return destination


def analyze_image(image: Image.Image) -> ImageAnalysis:
    rgba = image.convert("RGBA")
    array = np.asarray(rgba)
    gray = cv2.cvtColor(array, cv2.COLOR_RGBA2GRAY)
    sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(sobel_x, sobel_y)
    return ImageAnalysis(
        width=rgba.width,
        height=rgba.height,
        mean_luminance=float(np.mean(gray)),
        edge_pixel_count=int(np.count_nonzero(gradient > 0)),
    )
