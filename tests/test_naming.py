from __future__ import annotations

from pixel_asset_extractor.naming import format_frame_number, generate_filename, normalize_snake, unique_filename


def test_filename_normalization():
    assert normalize_snake("Freya Attack!") == "freya_attack"
    assert normalize_snake("  WALK-FRONT  ") == "walk_front"


def test_filename_generation_with_optional_fields():
    assert generate_filename("Freya", "idle", "idle", "front") == "freya_idle_front.png"
    assert generate_filename("Freya", "walk", "walk", "front", 4) == "freya_walk_front_04.png"
    assert generate_filename("Freya", "attack", "shears", "right", 1) == "freya_attack_shears_right_01.png"
    assert generate_filename("Freya", "effect", "", "none", None, "pollen_cloud") == "freya_effect_pollen_cloud.png"


def test_duplicate_filename_detection():
    assert unique_filename("freya_walk_front_01.png", {"freya_walk_front_01.png"}) == "freya_walk_front_01_02.png"


def test_frame_number_formatting():
    assert format_frame_number(None) == ""
    assert format_frame_number(1) == "01"
    assert format_frame_number(12) == "12"
