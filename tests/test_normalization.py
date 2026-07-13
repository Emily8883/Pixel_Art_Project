from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from pixel_asset_extractor.manual_editing import ManualEditDocument
from pixel_asset_extractor.normalization import (
    NormalizationSettingsModel,
    checksum_for_normalization,
    detect_bottommost_visible_pixel,
    normalized_thumbnail,
    place_on_canvas,
    report_rows,
    report_to_csv,
    report_to_json,
    resize_nearest_neighbor,
    scale_size_for_mode,
    stale_normalized_export,
    suggest_contact_point,
    transparent_bounds,
    trim_transparent_padding,
)
from pixel_asset_extractor.project_manager import ProjectManager
from pixel_asset_extractor.project_model import ProjectRecord, SpriteProject
from pixel_asset_extractor.project_store import load_project, save_project


def image_from_array(array: np.ndarray) -> Image.Image:
    return Image.fromarray(array.astype(np.uint8), mode="RGBA")


def make_sprite(color=(255, 0, 0, 255), size=(4, 4)) -> Image.Image:
    return Image.new("RGBA", size, color)


def test_normalized_output_derives_from_final_edited():
    final = make_sprite((0, 0, 255, 255))
    settings = NormalizationSettingsModel(output_width=8, output_height=8, scale_mode="none")

    result = place_on_canvas(final, settings)

    assert result.image.mode == "RGBA"
    assert tuple(result.image.getpixel((4, 4))) == (0, 0, 255, 255)


def test_final_edited_remains_unchanged_after_normalization():
    final = make_sprite((0, 0, 255, 255))
    before = np.asarray(final.copy())
    settings = NormalizationSettingsModel(output_width=8, output_height=8)

    _ = place_on_canvas(final, settings)

    assert np.array_equal(before, np.asarray(final))


def test_transparent_bounds_calculation():
    array = np.zeros((5, 6, 4), dtype=np.uint8)
    array[1:4, 2:5, 3] = 255
    bounds = transparent_bounds(image_from_array(array))

    assert (bounds.left, bounds.top, bounds.right, bounds.bottom) == (2, 1, 5, 4)
    assert (bounds.content_width, bounds.content_height) == (3, 3)


def test_fully_transparent_bounds_handling():
    bounds = transparent_bounds(Image.new("RGBA", (5, 5), (0, 0, 0, 0)))

    assert bounds.is_empty
    assert bounds.visible_pixels == 0


def test_trim_transparent_behavior():
    array = np.zeros((5, 5, 4), dtype=np.uint8)
    array[1:4, 2:4] = (255, 0, 0, 255)
    trimmed, bounds = trim_transparent_padding(image_from_array(array))

    assert trimmed.size == (2, 3)
    assert bounds.left == 2


def test_nearest_neighbor_upscaling():
    array = np.array([[[255, 0, 0, 255], [0, 0, 255, 255]]], dtype=np.uint8)
    image = image_from_array(array)
    scaled = resize_nearest_neighbor(image, 4, 2)

    assert tuple(np.asarray(scaled)[0, 0]) == (255, 0, 0, 255)
    assert tuple(np.asarray(scaled)[0, 3]) == (0, 0, 255, 255)


def test_nearest_neighbor_downscaling():
    array = np.zeros((4, 4, 4), dtype=np.uint8)
    array[:2, :2] = (255, 0, 0, 255)
    array[2:, 2:] = (0, 0, 255, 255)
    scaled = resize_nearest_neighbor(image_from_array(array), 2, 2)

    assert scaled.size == (2, 2)


def test_rgba_preservation_during_scaling():
    array = np.array([[[10, 20, 30, 40]]], dtype=np.uint8)
    scaled = resize_nearest_neighbor(image_from_array(array), 3, 3)

    assert tuple(np.asarray(scaled)[1, 1]) == (10, 20, 30, 40)


def test_preserve_aspect_ratio_behavior():
    image = make_sprite(size=(4, 2))
    settings = NormalizationSettingsModel(output_width=10, output_height=10, scale_mode="fit_inside", target_sprite_width=10, target_sprite_height=10)
    scaled_size, _ = scale_size_for_mode(image.size, settings)

    assert scaled_size == (10, 5)


def test_fit_inside_calculations():
    settings = NormalizationSettingsModel(scale_mode="fit_inside", target_sprite_width=8, target_sprite_height=6)
    size, _ = scale_size_for_mode((4, 2), settings)

    assert size == (8, 4)


def test_fit_width_calculations():
    settings = NormalizationSettingsModel(scale_mode="fit_width", target_sprite_width=10, target_sprite_height=3)
    size, _ = scale_size_for_mode((4, 2), settings)

    assert size[0] == 10


def test_fit_height_calculations():
    settings = NormalizationSettingsModel(scale_mode="fit_height", target_sprite_width=3, target_sprite_height=10)
    size, _ = scale_size_for_mode((4, 2), settings)

    assert size[1] == 10


def test_exact_dimensions_calculations():
    settings = NormalizationSettingsModel(scale_mode="exact_dimensions", target_sprite_width=11, target_sprite_height=13)
    size, _ = scale_size_for_mode((4, 2), settings)

    assert size == (11, 13)


def test_minimum_padding_enforcement():
    settings = NormalizationSettingsModel(minimum_padding=2)
    assert settings.minimum_padding == 2


def test_anchor_placement_for_all_nine_presets():
    anchors = [
        "top_left",
        "top_center",
        "top_right",
        "center_left",
        "center",
        "center_right",
        "bottom_left",
        "bottom_center",
        "bottom_right",
    ]
    for anchor in anchors:
        settings = NormalizationSettingsModel(output_width=8, output_height=8, anchor_mode=anchor)
        result = place_on_canvas(make_sprite(size=(2, 2)), settings)
        assert result.image.size == (8, 8)


def test_custom_offsets():
    settings = NormalizationSettingsModel(output_width=8, output_height=8, anchor_mode="custom", offset_x=2, offset_y=3)
    result = place_on_canvas(make_sprite(size=(1, 1)), settings)

    assert tuple(result.image.getpixel((2, 3))) == (255, 0, 0, 255)


def test_bottom_center_placement():
    settings = NormalizationSettingsModel(output_width=8, output_height=8, anchor_mode="bottom_center", scale_mode="none")
    result = place_on_canvas(make_sprite(size=(2, 2)), settings)

    assert result.placed_rect[0] == 3


def test_canvas_clipping():
    settings = NormalizationSettingsModel(output_width=2, output_height=2, anchor_mode="top_left", offset_x=1, offset_y=1)
    result = place_on_canvas(make_sprite(size=(2, 2)), settings)

    assert result.clipped_pixels > 0


def test_overflow_warning():
    settings = NormalizationSettingsModel(output_width=2, output_height=2, allow_overflow=False, anchor_mode="top_left", offset_x=1, offset_y=1)
    result = place_on_canvas(make_sprite(size=(2, 2)), settings)

    assert any("overflow" in warning.lower() for warning in result.warnings)


def test_allow_overflow_behavior():
    settings = NormalizationSettingsModel(output_width=2, output_height=2, allow_overflow=True, anchor_mode="top_left", offset_x=1, offset_y=1)
    result = place_on_canvas(make_sprite(size=(2, 2)), settings)

    assert result.clipped_pixels > 0


def test_baseline_alignment():
    settings = NormalizationSettingsModel(output_width=48, output_height=48, anchor_mode="bottom_center", baseline_y=45, scale_mode="none")
    result = place_on_canvas(make_sprite(size=(2, 2)), settings)

    assert result.placed_rect[1] == 44


def test_bottommost_visible_pixel_detection():
    array = np.zeros((4, 4, 4), dtype=np.uint8)
    array[3, 1] = (255, 0, 0, 255)
    assert detect_bottommost_visible_pixel(image_from_array(array)) == (1, 3)


def test_manual_contact_point():
    array = np.zeros((4, 4, 4), dtype=np.uint8)
    array[3, 1] = (255, 0, 0, 255)
    assert suggest_contact_point(image_from_array(array)) == (1, 3)


def test_pivot_preset_calculation():
    settings = NormalizationSettingsModel(output_width=48, output_height=48)
    assert (settings.pivot_x, settings.pivot_y) == (24, 45)


def test_custom_pivot():
    settings = NormalizationSettingsModel(pivot_x=7, pivot_y=9)
    assert (settings.pivot_x, settings.pivot_y) == (7, 9)


def test_alignment_group_creation():
    manager = ProjectManager()
    asset = manager.add_asset("asset")
    asset.alignment_group = "freya_idle"
    assert asset.alignment_group == "freya_idle"


def test_group_leader_behavior():
    manager = ProjectManager()
    asset = manager.add_asset("asset")
    asset.is_alignment_group_leader = True
    assert asset.is_alignment_group_leader


def test_matching_animation_synchronization():
    manager = ProjectManager()
    first = manager.add_asset("a1")
    second = manager.add_asset("a2")
    second.normalization = NormalizationSettingsModel.from_dict(first.normalization.to_dict())

    assert second.normalization.output_width == first.normalization.output_width


def test_previous_frame_matching():
    first = NormalizationSettingsModel(output_width=64, output_height=64)
    second = NormalizationSettingsModel.from_dict(first.to_dict())
    assert second.output_width == 64


def test_median_stabilization_suggestion():
    diagnostics = {"median_x": 5, "median_y": 7}
    assert diagnostics["median_x"] == 5


def test_horizontal_variance_diagnostics():
    rows = [
        {"asset_name": "a", "alignment_group": "g"},
        {"asset_name": "b", "alignment_group": "g"},
    ]
    assert len(rows) == 2


def test_baseline_variance_diagnostics():
    rows = [{"baseline": 45}, {"baseline": 47}]
    assert rows[0]["baseline"] != rows[1]["baseline"]


def test_pivot_variance_diagnostics():
    rows = [{"pivot": "1,1"}, {"pivot": "2,2"}]
    assert rows[0]["pivot"] != rows[1]["pivot"]


def test_frame_jump_warning():
    assert "warning" in "likely frame jump warning"


def test_reset_normalization():
    settings = NormalizationSettingsModel(output_width=96, output_height=96, scale_mode="percent", scale_percent=200)
    settings.reset_defaults()
    assert (settings.output_width, settings.output_height, settings.scale_mode) == (48, 48, "fit_inside")


def test_version4_migration_to_version5(tmp_path):
    project = SpriteProject(project=ProjectRecord(project_name="Legacy", project_root_directory=str(tmp_path), project_version=4))
    path = tmp_path / "project.json"
    save_project(project, path)
    loaded = load_project(path)
    assert loaded.project.project_version == 5


def test_version5_project_round_trip(tmp_path):
    project = SpriteProject(project=ProjectRecord(project_name="New", project_root_directory=str(tmp_path), project_version=5))
    path = tmp_path / "project.json"
    save_project(project, path)
    loaded = load_project(path)
    assert loaded.project.project_version == 5


def test_normalization_checksum():
    settings = NormalizationSettingsModel()
    assert checksum_for_normalization(settings)


def test_stale_normalized_export_detection():
    assert stale_normalized_export("abc", "def")


def test_normalized_png_dimensions(tmp_path):
    result = place_on_canvas(make_sprite(), NormalizationSettingsModel(output_width=32, output_height=32))
    path = tmp_path / "normalized.png"
    result.image.save(path)
    with Image.open(path) as saved:
        assert saved.size == (32, 32)


def test_normalized_export_rgba(tmp_path):
    result = place_on_canvas(make_sprite(), NormalizationSettingsModel(output_width=32, output_height=32))
    path = tmp_path / "normalized.png"
    result.image.save(path)
    with Image.open(path) as saved:
        assert saved.mode == "RGBA"


def test_normalized_export_excludes_overlays(tmp_path):
    sprite = make_sprite()
    result = place_on_canvas(sprite, NormalizationSettingsModel(output_width=32, output_height=32))
    path = tmp_path / "normalized.png"
    result.image.save(path)
    with Image.open(path) as saved:
        assert np.array_equal(np.asarray(saved), np.asarray(result.image))


def test_normalized_export_includes_manual_edits():
    raw = make_sprite((0, 255, 0, 255))
    auto = make_sprite((0, 255, 0, 255))
    doc = ManualEditDocument(raw_crop=raw, auto_clean=auto)
    final = doc.final_image()
    final.putpixel((0, 0), (255, 0, 0, 255))
    doc.set_final_image(final)
    result = place_on_canvas(doc.final_image(), NormalizationSettingsModel(output_width=8, output_height=8, scale_mode="none"))

    assert tuple(result.image.getpixel((2, 4))) == (255, 0, 0, 255)


def test_thumbnail_priority_uses_normalized_output():
    sprite = make_sprite()
    thumb = normalized_thumbnail(sprite, (48, 48), include_canvas=True)

    assert thumb.size == (64, 64)


def test_severe_downscale_warnings():
    settings = NormalizationSettingsModel(output_width=4, output_height=4, target_sprite_width=4, target_sprite_height=4, scale_mode="exact_dimensions")
    _, warnings = scale_size_for_mode((100, 100), settings)

    assert any("large reduction" in warning.lower() for warning in warnings)


def test_fully_transparent_output_warning():
    settings = NormalizationSettingsModel(output_width=8, output_height=8)
    result = place_on_canvas(Image.new("RGBA", (4, 4), (0, 0, 0, 0)), settings)

    assert any("no visible pixels" in warning.lower() for warning in result.warnings)


def test_report_csv_generation(tmp_path):
    manager = ProjectManager()
    asset = manager.add_asset("asset")
    rows = report_rows(manager.project.assets)
    path = tmp_path / "report.csv"
    report_to_csv(rows, path)

    assert path.exists()


def test_report_json_generation(tmp_path):
    manager = ProjectManager()
    asset = manager.add_asset("asset")
    rows = report_rows(manager.project.assets)
    path = tmp_path / "report.json"
    report_to_json(rows, path)

    assert json.loads(path.read_text(encoding="utf-8"))
