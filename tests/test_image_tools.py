from __future__ import annotations

import numpy as np
from PIL import Image

from pixel_asset_extractor.image_tools import analyze_image, crop_image, export_crop
from pixel_asset_extractor.models import CropRect


def test_crop_export_preserves_exact_pixels(tmp_path):
    array = np.array(
        [
            [[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255]],
            [[10, 20, 30, 255], [40, 50, 60, 255], [70, 80, 90, 255]],
            [[100, 110, 120, 255], [130, 140, 150, 255], [160, 170, 180, 255]],
        ],
        dtype=np.uint8,
    )
    image = Image.fromarray(array, mode="RGBA")
    crop_rect = CropRect(1, 1, 2, 2)
    destination = tmp_path / "crop.png"

    export_crop(image, crop_rect, destination)

    with Image.open(destination) as saved_file:
        saved = saved_file.convert("RGBA")
    expected = image.crop((1, 1, 3, 3))

    assert np.array_equal(np.asarray(saved), np.asarray(expected))


def test_analyze_image_reports_basic_stats():
    array = np.zeros((4, 4, 4), dtype=np.uint8)
    array[1:3, 1:3, :3] = 255
    array[:, :, 3] = 255
    image = Image.fromarray(array, mode="RGBA")

    analysis = analyze_image(image)

    assert analysis.width == 4
    assert analysis.height == 4
    assert analysis.mean_luminance > 0
    assert analysis.edge_pixel_count > 0
