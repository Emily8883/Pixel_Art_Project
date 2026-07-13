from __future__ import annotations

import numpy as np
from PIL import Image

from pixel_asset_extractor.processing import (
    BackgroundRemovalSettings,
    apply_background_removal,
    export_png,
    removal_warning_messages,
    rgb_distance,
    removable_mask,
    ui_tolerance_to_distance,
)


def make_image(array: np.ndarray) -> Image.Image:
    return Image.fromarray(array.astype(np.uint8), mode="RGBA")


def test_euclidean_rgb_distance():
    assert rgb_distance((0, 0, 0), (3, 4, 0)) == 5.0
    assert rgb_distance((10, 20, 30), (10, 20, 30)) == 0.0


def test_tolerance_mapping():
    assert ui_tolerance_to_distance(0) == 0.0
    assert round(ui_tolerance_to_distance(100), 2) == round((3 ** 0.5) * 255, 2)


def test_exact_threshold_boundary_behavior():
    array = np.array(
        [
            [[10, 10, 10, 255], [13, 10, 10, 255]],
            [[20, 20, 20, 255], [21, 20, 20, 255]],
        ],
        dtype=np.uint8,
    )
    image = make_image(array)
    mask = removable_mask(image, (10, 10, 10), 3.0, False, 4)

    assert mask[0, 0]
    assert mask[0, 1]
    assert not mask[1, 0]


def test_four_way_connected_flood_fill():
    array = np.array(
        [
            [[10, 10, 10, 255], [100, 100, 100, 255], [100, 100, 100, 255]],
            [[100, 100, 100, 255], [10, 10, 10, 255], [100, 100, 100, 255]],
            [[100, 100, 100, 255], [100, 100, 100, 255], [100, 100, 100, 255]],
        ],
        dtype=np.uint8,
    )
    image = make_image(array)
    mask = removable_mask(image, (10, 10, 10), 0.0, True, 4)

    assert mask[0, 0]
    assert not mask[1, 1]


def test_eight_way_connected_flood_fill():
    array = np.array(
        [
            [[10, 10, 10, 255], [100, 100, 100, 255], [100, 100, 100, 255]],
            [[100, 100, 100, 255], [10, 10, 10, 255], [100, 100, 100, 255]],
            [[100, 100, 100, 255], [100, 100, 100, 255], [100, 100, 100, 255]],
        ],
        dtype=np.uint8,
    )
    image = make_image(array)
    mask = removable_mask(image, (10, 10, 10), 0.0, True, 8)

    assert mask[0, 0]
    assert mask[1, 1]


def test_enclosed_matching_color_pixels_are_retained():
    array = np.full((5, 5, 4), (200, 200, 200, 255), dtype=np.uint8)
    array[1:4, 1:4, :3] = (30, 30, 30)
    array[2, 2, :3] = (200, 200, 200)
    image = make_image(array)

    result = apply_background_removal(
        image,
        BackgroundRemovalSettings(
            background_rgba=(200, 200, 200, 255),
            tolerance_ui=0,
            connected_background_only=True,
            connectivity=4,
        ),
    )

    assert result.removed_pixels == 16
    assert tuple(result.cleaned_image.getpixel((2, 2))) == (200, 200, 200, 255)


def test_global_removal_removes_all_matching_pixels():
    array = np.full((4, 4, 4), (50, 60, 70, 255), dtype=np.uint8)
    image = make_image(array)

    result = apply_background_removal(
        image,
        BackgroundRemovalSettings(
            background_rgba=(50, 60, 70, 255),
            tolerance_ui=100,
            connected_background_only=False,
            connectivity=4,
        ),
    )

    assert result.removed_pixels == 16
    assert np.all(np.asarray(result.cleaned_image)[:, :, 3] == 0)


def test_retained_pixels_remain_pixel_identical():
    array = np.array(
        [
            [[100, 100, 100, 255], [5, 6, 7, 255]],
            [[100, 100, 100, 255], [8, 9, 10, 255]],
        ],
        dtype=np.uint8,
    )
    image = make_image(array)

    result = apply_background_removal(
        image,
        BackgroundRemovalSettings(
            background_rgba=(100, 100, 100, 255),
            tolerance_ui=0,
            connected_background_only=False,
            connectivity=4,
        ),
    )

    cleaned = np.asarray(result.cleaned_image)
    assert tuple(cleaned[0, 1]) == (5, 6, 7, 255)
    assert tuple(cleaned[1, 1]) == (8, 9, 10, 255)


def test_removed_pixels_become_exactly_transparent():
    array = np.full((2, 2, 4), (40, 50, 60, 255), dtype=np.uint8)
    image = make_image(array)

    result = apply_background_removal(
        image,
        BackgroundRemovalSettings(
            background_rgba=(40, 50, 60, 255),
            tolerance_ui=100,
            connected_background_only=False,
            connectivity=4,
        ),
    )

    assert np.array_equal(np.asarray(result.cleaned_image), np.zeros((2, 2, 4), dtype=np.uint8))


def test_exported_clean_png_is_rgba(tmp_path):
    array = np.array(
        [
            [[10, 10, 10, 255], [20, 20, 20, 255]],
            [[30, 30, 30, 255], [40, 40, 40, 255]],
        ],
        dtype=np.uint8,
    )
    image = make_image(array)
    destination = tmp_path / "clean.png"

    export_png(image, destination)

    with Image.open(destination) as saved:
        assert saved.mode == "RGBA"


def test_transparent_output_warning_condition():
    array = np.full((3, 3, 4), (1, 2, 3, 255), dtype=np.uint8)
    image = make_image(array)
    result = apply_background_removal(
        image,
        BackgroundRemovalSettings(
            background_rgba=(1, 2, 3, 255),
            tolerance_ui=100,
            connected_background_only=False,
            connectivity=4,
        ),
    )
    warnings = removal_warning_messages(True, (1, 2, 3, 255), True, result)

    assert result.fully_transparent is True
    assert "The cleaned image becomes completely transparent." in warnings


def test_more_than_80_percent_removal_warning_condition():
    array = np.full((10, 10, 4), (9, 9, 9, 255), dtype=np.uint8)
    array[0, 0, :3] = (200, 200, 200)
    image = make_image(array)
    result = apply_background_removal(
        image,
        BackgroundRemovalSettings(
            background_rgba=(9, 9, 9, 255),
            tolerance_ui=100,
            connected_background_only=False,
            connectivity=4,
        ),
    )
    warnings = removal_warning_messages(True, (9, 9, 9, 255), False, result)

    assert result.removal_percentage > 80.0
    assert "Removal erases more than 80% of crop pixels." in warnings
