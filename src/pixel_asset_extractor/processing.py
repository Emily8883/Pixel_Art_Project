from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


MAX_RGB_DISTANCE = math.sqrt(3 * (255**2))


@dataclass(frozen=True, slots=True)
class BackgroundRemovalSettings:
    background_rgba: tuple[int, int, int, int] | None
    tolerance_ui: int = 5
    connected_background_only: bool = True
    connectivity: int = 4

    @property
    def tolerance_threshold(self) -> float:
        return ui_tolerance_to_distance(self.tolerance_ui)


@dataclass(frozen=True, slots=True)
class BackgroundRemovalResult:
    cleaned_image: Image.Image
    removed_pixels: int
    total_pixels: int
    removal_percentage: float
    fully_transparent: bool
    removed_mask: np.ndarray


def removal_warning_messages(
    crop_exists: bool,
    background_rgba: tuple[int, int, int, int] | None,
    connected_background_only: bool,
    removal_result: BackgroundRemovalResult | None,
) -> list[str]:
    warnings: list[str] = []
    if not crop_exists:
        warnings.append("No crop exists.")
    if background_rgba is None:
        warnings.append("No background color has been selected.")
    if not connected_background_only:
        warnings.append("Global removal may erase colors inside the sprite.")
    if removal_result is not None and removal_result.removal_percentage > 80.0:
        warnings.append("Removal erases more than 80% of crop pixels.")
    if removal_result is not None and removal_result.fully_transparent:
        warnings.append("The cleaned image becomes completely transparent.")
    return warnings


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def rgb_distance(color_a: tuple[int, int, int], color_b: tuple[int, int, int]) -> float:
    dr = color_a[0] - color_b[0]
    dg = color_a[1] - color_b[1]
    db = color_a[2] - color_b[2]
    return math.sqrt(dr * dr + dg * dg + db * db)


def ui_tolerance_to_distance(tolerance_ui: int) -> float:
    """Map the 0-100 UI slider to the RGB distance space.

    The maximum Euclidean distance between two RGB colors is sqrt(3 * 255^2).
    A UI value of 100 therefore maps to approximately 441.67.
    """

    ui_value = clamp_int(tolerance_ui, 0, 100)
    return MAX_RGB_DISTANCE * (ui_value / 100.0)


def hex_from_rgba(rgba: tuple[int, int, int, int]) -> str:
    return f"#{rgba[0]:02X}{rgba[1]:02X}{rgba[2]:02X}{rgba[3]:02X}"


def format_rgb(rgba: tuple[int, int, int, int] | None) -> str:
    if rgba is None:
        return "RGB: not selected"
    return f"RGB: ({rgba[0]}, {rgba[1]}, {rgba[2]})"


def format_rgba(rgba: tuple[int, int, int, int] | None) -> str:
    if rgba is None:
        return "RGBA: not selected"
    return f"RGBA: ({rgba[0]}, {rgba[1]}, {rgba[2]}, {rgba[3]})"


def format_hex(rgba: tuple[int, int, int, int] | None) -> str:
    if rgba is None:
        return "Hex: not selected"
    return f"Hex: {hex_from_rgba(rgba)}"


def rgba_to_rgb(rgba: tuple[int, int, int, int] | None) -> tuple[int, int, int] | None:
    if rgba is None:
        return None
    return rgba[0], rgba[1], rgba[2]


def load_rgba_image(path: str | Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA")


def removable_mask(
    image: Image.Image,
    background_rgb: tuple[int, int, int],
    tolerance_threshold: float,
    connected_background_only: bool,
    connectivity: int,
) -> np.ndarray:
    rgba = image.convert("RGBA")
    array = np.asarray(rgba, dtype=np.uint8)
    rgb = array[:, :, :3].astype(np.int32)
    delta = rgb - np.array(background_rgb, dtype=np.int32)
    distance = np.sqrt(np.sum(delta * delta, axis=2, dtype=np.int64), dtype=np.float64)
    removable = distance <= tolerance_threshold

    if not connected_background_only:
        return removable

    if connectivity not in (4, 8):
        raise ValueError("connectivity must be 4 or 8")

    height, width = removable.shape
    visited = np.zeros_like(removable, dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    def enqueue_if_removable(y: int, x: int) -> None:
        if removable[y, x] and not visited[y, x]:
            visited[y, x] = True
            queue.append((y, x))

    for x in range(width):
        enqueue_if_removable(0, x)
        if height > 1:
            enqueue_if_removable(height - 1, x)
    for y in range(height):
        enqueue_if_removable(y, 0)
        if width > 1:
            enqueue_if_removable(y, width - 1)

    if connectivity == 4:
        neighbors = ((-1, 0), (1, 0), (0, -1), (0, 1))
    else:
        neighbors = (
            (-1, 0),
            (1, 0),
            (0, -1),
            (0, 1),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        )

    while queue:
        y, x = queue.popleft()
        for dy, dx in neighbors:
            ny = y + dy
            nx = x + dx
            if 0 <= ny < height and 0 <= nx < width and removable[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = True
                queue.append((ny, nx))

    return visited


def apply_background_removal(
    image: Image.Image,
    settings: BackgroundRemovalSettings,
) -> BackgroundRemovalResult:
    rgba = image.convert("RGBA")
    array = np.asarray(rgba, dtype=np.uint8).copy()

    if settings.background_rgba is None:
        cleaned = Image.fromarray(array, mode="RGBA")
        total_pixels = rgba.width * rgba.height
        return BackgroundRemovalResult(
            cleaned_image=cleaned,
            removed_pixels=0,
            total_pixels=total_pixels,
            removal_percentage=0.0,
            fully_transparent=total_pixels == 0,
            removed_mask=np.zeros((rgba.height, rgba.width), dtype=bool),
        )

    mask = removable_mask(
        rgba,
        background_rgb=(settings.background_rgba[0], settings.background_rgba[1], settings.background_rgba[2]),
        tolerance_threshold=settings.tolerance_threshold,
        connected_background_only=settings.connected_background_only,
        connectivity=settings.connectivity,
    )

    array[mask] = (0, 0, 0, 0)
    cleaned = Image.fromarray(array, mode="RGBA")
    removed_pixels = int(np.count_nonzero(mask))
    total_pixels = rgba.width * rgba.height
    removal_percentage = (removed_pixels / total_pixels * 100.0) if total_pixels else 0.0
    fully_transparent = bool(total_pixels and removed_pixels == total_pixels)
    return BackgroundRemovalResult(
        cleaned_image=cleaned,
        removed_pixels=removed_pixels,
        total_pixels=total_pixels,
        removal_percentage=removal_percentage,
        fully_transparent=fully_transparent,
        removed_mask=mask,
    )


def crop_image(image: Image.Image, crop_rect) -> Image.Image:
    from .exceptions import CropError

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


def export_png(image: Image.Image, output_path: str | Path) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGBA").save(destination, format="PNG")
    return destination
