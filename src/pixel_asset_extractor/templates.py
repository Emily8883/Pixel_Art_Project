from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemplateAssetSpec:
    display_name: str
    character_group: str
    category: str
    action: str
    direction: str
    frame_number: int | None = None
    variant: str = ""


FREYA_MOVEMENT_TEMPLATE: tuple[TemplateAssetSpec, ...] = (
    TemplateAssetSpec("freya_idle_front", "freya", "idle", "idle", "front"),
    TemplateAssetSpec("freya_idle_right", "freya", "idle", "idle", "right"),
    TemplateAssetSpec("freya_idle_left", "freya", "idle", "idle", "left"),
    TemplateAssetSpec("freya_idle_back", "freya", "idle", "idle", "back"),
    TemplateAssetSpec("freya_walk_front_01", "freya", "walk", "walk", "front", 1),
    TemplateAssetSpec("freya_walk_front_02", "freya", "walk", "walk", "front", 2),
    TemplateAssetSpec("freya_walk_front_03", "freya", "walk", "walk", "front", 3),
    TemplateAssetSpec("freya_walk_front_04", "freya", "walk", "walk", "front", 4),
    TemplateAssetSpec("freya_walk_right_01", "freya", "walk", "walk", "right", 1),
    TemplateAssetSpec("freya_walk_right_02", "freya", "walk", "walk", "right", 2),
    TemplateAssetSpec("freya_walk_right_03", "freya", "walk", "walk", "right", 3),
    TemplateAssetSpec("freya_walk_right_04", "freya", "walk", "walk", "right", 4),
    TemplateAssetSpec("freya_walk_left_01", "freya", "walk", "walk", "left", 1),
    TemplateAssetSpec("freya_walk_left_02", "freya", "walk", "walk", "left", 2),
    TemplateAssetSpec("freya_walk_left_03", "freya", "walk", "walk", "left", 3),
    TemplateAssetSpec("freya_walk_left_04", "freya", "walk", "walk", "left", 4),
    TemplateAssetSpec("freya_walk_back_01", "freya", "walk", "walk", "back", 1),
    TemplateAssetSpec("freya_walk_back_02", "freya", "walk", "walk", "back", 2),
    TemplateAssetSpec("freya_walk_back_03", "freya", "walk", "walk", "back", 3),
    TemplateAssetSpec("freya_walk_back_04", "freya", "walk", "walk", "back", 4),
)

