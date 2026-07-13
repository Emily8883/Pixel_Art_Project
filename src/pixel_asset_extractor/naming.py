from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from .project_model import AssetRecord


INVALID_FILENAME_CHARS = r'<>:"/\\|?*'


def normalize_snake(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("-", "_").replace(" ", "_")
    value = re.sub(rf"[{re.escape(INVALID_FILENAME_CHARS)}]", "_", value)
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def format_frame_number(frame_number: int | None) -> str:
    if frame_number is None:
        return ""
    return f"{int(frame_number):02d}"


def generate_asset_basename(
    character_group: str = "",
    category: str = "",
    action: str = "",
    direction: str = "",
    frame_number: int | None = None,
    variant: str = "",
) -> str:
    parts: list[str] = []
    normalized_character = normalize_snake(character_group)
    normalized_category = normalize_snake(category)
    normalized_action = normalize_snake(action)
    normalized_direction = normalize_snake(direction)
    normalized_variant = normalize_snake(variant)

    if normalized_character:
        parts.append(normalized_character)
    if normalized_category:
        parts.append(normalized_category)
    if normalized_action and normalized_action != normalized_category:
        parts.append(normalized_action)
    if normalized_variant:
        parts.append(normalized_variant)
    if normalized_direction and normalized_direction != "none":
        parts.append(normalized_direction)
    frame = format_frame_number(frame_number)
    if frame:
        parts.append(frame)
    basename = "_".join(parts)
    basename = re.sub(r"_+", "_", basename).strip("_")
    return basename


def generate_filename(
    character_group: str = "",
    category: str = "",
    action: str = "",
    direction: str = "",
    frame_number: int | None = None,
    variant: str = "",
) -> str:
    basename = generate_asset_basename(character_group, category, action, direction, frame_number, variant)
    return f"{basename}.png" if basename else ".png"


def unique_filename(base_name: str, used_names: set[str]) -> str:
    if base_name not in used_names:
        return base_name
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix
    index = 2
    while True:
        candidate = f"{stem}_{index:02d}{suffix}"
        if candidate not in used_names:
            return candidate
        index += 1


def asset_filename_conflicts(asset: AssetRecord, other_filenames: set[str]) -> bool:
    return asset.raw_output_filename in other_filenames or asset.clean_output_filename in other_filenames
