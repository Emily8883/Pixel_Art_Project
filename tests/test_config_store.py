from __future__ import annotations

import json

from pixel_asset_extractor.config_store import load_config, save_config
from pixel_asset_extractor.models import CropConfig, CropRect
from pixel_asset_extractor.processing import ui_tolerance_to_distance


def test_config_round_trip(tmp_path):
    config = CropConfig(
        source_image=r"C:\\temp\\sprites\\sheet.png",
        crop_rect=CropRect(12, 24, 48, 64),
        background_rgba=(100, 110, 120, 130),
        tolerance_ui=12,
        tolerance_threshold=ui_tolerance_to_distance(12),
        connected_background_only=False,
        connectivity=8,
        output_raw_filename="sheet_raw.png",
        output_clean_filename="sheet_clean.png",
        export_directory=r"C:\\temp\\sprites\\output",
    )
    file_path = tmp_path / "crop_config.json"

    save_config(config, file_path)
    loaded = load_config(file_path)

    assert loaded == config


def test_version1_config_loads_from_legacy_shape(tmp_path):
    payload = {
        "version": 1,
        "source_image": r"C:\\legacy\\sheet.png",
        "x": 4,
        "y": 8,
        "width": 16,
        "height": 32,
    }
    file_path = tmp_path / "legacy.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_config(file_path)

    assert loaded.source_image == payload["source_image"]
    assert loaded.crop_rect == CropRect(4, 8, 16, 32)
    assert loaded.config_version == 1
    assert loaded.background_rgba is None
    assert loaded.tolerance_ui == 5
    assert loaded.connected_background_only is True
    assert loaded.connectivity == 4


def test_legacy_config_with_only_crop_coordinates_loads(tmp_path):
    payload = {
        "x": 7,
        "y": 9,
        "width": 11,
        "height": 13,
    }
    file_path = tmp_path / "coords_only.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_config(file_path)

    assert loaded.crop_rect == CropRect(7, 9, 11, 13)
    assert loaded.source_image == ""
    assert loaded.config_version == 1


def test_version2_config_round_trip(tmp_path):
    config = CropConfig(
        source_image=r"C:\\assets\\sheet.png",
        crop_rect=CropRect(1, 2, 3, 4),
        background_rgba=(12, 34, 56, 78),
        tolerance_ui=30,
        tolerance_threshold=ui_tolerance_to_distance(30),
        connected_background_only=True,
        connectivity=8,
        output_raw_filename="raw.png",
        output_clean_filename="clean.png",
        export_directory=r"C:\\assets\\exports",
    )
    file_path = tmp_path / "project.json"

    save_config(config, file_path)
    loaded = load_config(file_path)

    assert loaded == config
