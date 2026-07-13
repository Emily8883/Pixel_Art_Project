from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import csv
import io
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image


ScaleMode = str
AnchorMode = str

SCALE_MODES = ("none", "percent", "fit_inside", "fit_width", "fit_height", "exact_dimensions")
ANCHOR_MODES = (
    "top_left",
    "top_center",
    "top_right",
    "center_left",
    "center",
    "center_right",
    "bottom_left",
    "bottom_center",
    "bottom_right",
    "custom",
)
OUTPUT_PRESETS = (16, 24, 32, 48, 64, 96, 128)


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _image_to_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()


def _array_to_image(array: np.ndarray) -> Image.Image:
    return Image.fromarray(array.astype(np.uint8, copy=False), mode="RGBA")


def _checksum_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


@dataclass(slots=True)
class NormalizationSettingsModel:
    enabled: bool = True
    output_width: int = 48
    output_height: int = 48
    scale_mode: ScaleMode = "fit_inside"
    scale_percent: int = 100
    target_sprite_width: int = 0
    target_sprite_height: int = 0
    preserve_aspect_ratio: bool = True
    offset_x: int = 0
    offset_y: int = 0
    anchor_mode: AnchorMode = "bottom_center"
    baseline_y: int = 45
    pivot_x: int = 24
    pivot_y: int = 45
    trim_transparent_before_placement: bool = False
    minimum_padding: int = 2
    allow_overflow: bool = False
    normalized_output_filename: str = ""
    include_canvas_in_thumbnail: bool = False
    confirmed: bool = True
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "enabled": self.enabled,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "scale_mode": self.scale_mode,
            "scale_percent": self.scale_percent,
            "target_sprite_width": self.target_sprite_width,
            "target_sprite_height": self.target_sprite_height,
            "preserve_aspect_ratio": self.preserve_aspect_ratio,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "anchor_mode": self.anchor_mode,
            "baseline_y": self.baseline_y,
            "pivot_x": self.pivot_x,
            "pivot_y": self.pivot_y,
            "trim_transparent_before_placement": self.trim_transparent_before_placement,
            "minimum_padding": self.minimum_padding,
            "allow_overflow": self.allow_overflow,
            "normalized_output_filename": self.normalized_output_filename,
            "include_canvas_in_thumbnail": self.include_canvas_in_thumbnail,
            "confirmed": self.confirmed,
        }
        payload.update(self.extras)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "NormalizationSettingsModel":
        payload = payload or {}
        extras = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "enabled",
                "output_width",
                "output_height",
                "scale_mode",
                "scale_percent",
                "target_sprite_width",
                "target_sprite_height",
                "preserve_aspect_ratio",
                "offset_x",
                "offset_y",
                "anchor_mode",
                "baseline_y",
                "pivot_x",
                "pivot_y",
                "trim_transparent_before_placement",
                "minimum_padding",
                "allow_overflow",
                "normalized_output_filename",
                "include_canvas_in_thumbnail",
                "confirmed",
            }
        }
        return cls(
            enabled=bool(payload.get("enabled", True)),
            output_width=_clamp_int(payload.get("output_width", 48), 1, 10000),
            output_height=_clamp_int(payload.get("output_height", 48), 1, 10000),
            scale_mode=str(payload.get("scale_mode", "fit_inside")) if str(payload.get("scale_mode", "fit_inside")) in SCALE_MODES else "fit_inside",
            scale_percent=_clamp_int(payload.get("scale_percent", 100), 1, 6400),
            target_sprite_width=_clamp_int(payload.get("target_sprite_width", 0), 0, 10000),
            target_sprite_height=_clamp_int(payload.get("target_sprite_height", 0), 0, 10000),
            preserve_aspect_ratio=bool(payload.get("preserve_aspect_ratio", True)),
            offset_x=int(payload.get("offset_x", 0)),
            offset_y=int(payload.get("offset_y", 0)),
            anchor_mode=str(payload.get("anchor_mode", "bottom_center")) if str(payload.get("anchor_mode", "bottom_center")) in ANCHOR_MODES else "bottom_center",
            baseline_y=int(payload.get("baseline_y", 45)),
            pivot_x=int(payload.get("pivot_x", 24)),
            pivot_y=int(payload.get("pivot_y", 45)),
            trim_transparent_before_placement=bool(payload.get("trim_transparent_before_placement", False)),
            minimum_padding=_clamp_int(payload.get("minimum_padding", 2), 0, 1000),
            allow_overflow=bool(payload.get("allow_overflow", False)),
            normalized_output_filename=str(payload.get("normalized_output_filename", "")),
            include_canvas_in_thumbnail=bool(payload.get("include_canvas_in_thumbnail", False)),
            confirmed=bool(payload.get("confirmed", True)),
            extras=extras,
        )

    def checksum(self) -> str:
        return _checksum_bytes(json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=True).encode("utf-8"))

    def reset_defaults(self, output_width: int = 48, output_height: int = 48) -> None:
        self.enabled = True
        self.output_width = output_width
        self.output_height = output_height
        self.scale_mode = "fit_inside"
        self.scale_percent = 100
        self.target_sprite_width = 0
        self.target_sprite_height = 0
        self.preserve_aspect_ratio = True
        self.offset_x = 0
        self.offset_y = 0
        self.anchor_mode = "bottom_center"
        self.baseline_y = 45 if output_height >= 48 else max(0, output_height - 3)
        self.pivot_x = max(0, output_width // 2)
        self.pivot_y = self.baseline_y
        self.trim_transparent_before_placement = False
        self.minimum_padding = 2
        self.allow_overflow = False
        self.confirmed = True


@dataclass(slots=True)
class TransparentBounds:
    left: int
    top: int
    right: int
    bottom: int
    content_width: int
    content_height: int
    margin_left: int
    margin_top: int
    margin_right: int
    margin_bottom: int
    visible_pixels: int

    @property
    def is_empty(self) -> bool:
        return self.visible_pixels == 0


@dataclass(slots=True)
class NormalizedOutputResult:
    image: Image.Image
    content_bounds: TransparentBounds
    scaled_content_size: tuple[int, int]
    placed_rect: tuple[int, int, int, int]
    clipped_pixels: int
    warnings: list[str]
    checksum: str


@dataclass(slots=True)
class AlignmentDiagnostics:
    horizontal_center_variance: float
    baseline_variance: float
    pivot_variance: float
    content_bounds_variance: float
    likely_frame_jump_warning: str | None = None


def transparent_bounds(image: Image.Image) -> TransparentBounds:
    rgba = image.convert("RGBA")
    array = np.asarray(rgba, dtype=np.uint8)
    alpha = array[:, :, 3] > 0
    height, width = alpha.shape
    visible = int(np.count_nonzero(alpha))
    if visible == 0:
        return TransparentBounds(0, 0, 0, 0, 0, 0, width, height, width, height, 0)

    ys, xs = np.where(alpha)
    left = int(xs.min())
    right = int(xs.max()) + 1
    top = int(ys.min())
    bottom = int(ys.max()) + 1
    return TransparentBounds(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        content_width=right - left,
        content_height=bottom - top,
        margin_left=left,
        margin_top=top,
        margin_right=width - right,
        margin_bottom=height - bottom,
        visible_pixels=visible,
    )


def trim_transparent_padding(image: Image.Image) -> tuple[Image.Image, TransparentBounds]:
    bounds = transparent_bounds(image)
    if bounds.is_empty:
        return image.convert("RGBA").copy(), bounds
    cropped = image.convert("RGBA").crop((bounds.left, bounds.top, bounds.right, bounds.bottom))
    return cropped, bounds


def resize_nearest_neighbor(image: Image.Image, width: int, height: int) -> Image.Image:
    width = max(1, int(width))
    height = max(1, int(height))
    return image.convert("RGBA").resize((width, height), resample=Image.Resampling.NEAREST)


def scale_size_for_mode(
    source_size: tuple[int, int],
    settings: NormalizationSettingsModel,
) -> tuple[int, int, list[str]]:
    source_width, source_height = source_size
    warnings: list[str] = []
    if source_width <= 0 or source_height <= 0:
        return (1, 1), warnings

    target_width = settings.target_sprite_width or settings.output_width
    target_height = settings.target_sprite_height or settings.output_height
    if settings.scale_mode == "none":
        scaled_width, scaled_height = source_width, source_height
    elif settings.scale_mode == "percent":
        scaled_width = max(1, round(source_width * settings.scale_percent / 100.0))
        scaled_height = max(1, round(source_height * settings.scale_percent / 100.0))
    elif settings.scale_mode == "fit_width":
        ratio = target_width / source_width
        scaled_width = max(1, int(round(target_width)))
        scaled_height = max(1, int(round(source_height * ratio))) if settings.preserve_aspect_ratio else max(1, int(round(target_height)))
    elif settings.scale_mode == "fit_height":
        ratio = target_height / source_height
        scaled_height = max(1, int(round(target_height)))
        scaled_width = max(1, int(round(source_width * ratio))) if settings.preserve_aspect_ratio else max(1, int(round(target_width)))
    elif settings.scale_mode == "exact_dimensions":
        scaled_width = max(1, int(target_width))
        scaled_height = max(1, int(target_height))
    else:
        ratio = min(target_width / source_width, target_height / source_height)
        scaled_width = max(1, int(round(source_width * ratio)))
        scaled_height = max(1, int(round(source_height * ratio)))

    reduction_ratio = min(scaled_width / source_width, scaled_height / source_height)
    if reduction_ratio < 0.25:
        warnings.append("Large reduction may make the sprite muddy or unreadable. Manual pixel-art redrawing may be required.")
    elif reduction_ratio < 0.5:
        warnings.append("Source reduced below 50% of native size.")
    elif reduction_ratio < 0.75:
        warnings.append("Source reduced below 75% of native size.")
    if scaled_width < 16 or scaled_height < 16:
        warnings.append("Output content is smaller than 16 pixels in either dimension.")
    if scaled_width < source_width or scaled_height < source_height:
        warnings.append("Sprite may become visually dense because many source pixels map to the same output pixel.")
    return (scaled_width, scaled_height), warnings


def anchor_offset(
    canvas_size: tuple[int, int],
    sprite_size: tuple[int, int],
    settings: NormalizationSettingsModel,
) -> tuple[int, int]:
    canvas_width, canvas_height = canvas_size
    sprite_width, sprite_height = sprite_size
    anchor = settings.anchor_mode

    if anchor == "custom":
        return settings.offset_x, settings.offset_y

    x_positions = {
        "left": 0,
        "center": max(0, (canvas_width - sprite_width) // 2),
        "right": max(0, canvas_width - sprite_width),
    }
    y_positions = {
        "top": 0,
        "center": max(0, (canvas_height - sprite_height) // 2),
        "bottom": max(0, canvas_height - sprite_height),
    }

    if anchor == "top_left":
        x, y = x_positions["left"], y_positions["top"]
    elif anchor == "top_center":
        x, y = x_positions["center"], y_positions["top"]
    elif anchor == "top_right":
        x, y = x_positions["right"], y_positions["top"]
    elif anchor == "center_left":
        x, y = x_positions["left"], y_positions["center"]
    elif anchor == "center":
        x, y = x_positions["center"], y_positions["center"]
    elif anchor == "center_right":
        x, y = x_positions["right"], y_positions["center"]
    elif anchor == "bottom_left":
        x, y = x_positions["left"], y_positions["bottom"]
    elif anchor == "bottom_center":
        x, y = x_positions["center"], y_positions["bottom"]
    elif anchor == "bottom_right":
        x, y = x_positions["right"], y_positions["bottom"]
    else:
        x, y = 0, 0

    if (
        settings.baseline_y is not None
        and 0 <= settings.baseline_y < canvas_height
        and anchor in {"bottom_left", "bottom_center", "bottom_right"}
    ):
        y = settings.baseline_y - sprite_height + 1
    return x + settings.offset_x, y + settings.offset_y


def place_on_canvas(
    sprite: Image.Image,
    settings: NormalizationSettingsModel,
) -> NormalizedOutputResult:
    final_sprite = sprite.convert("RGBA")
    bounds = transparent_bounds(final_sprite)
    source_for_scale = final_sprite
    if settings.trim_transparent_before_placement:
        source_for_scale, bounds = trim_transparent_padding(final_sprite)
    if bounds.is_empty:
        warnings = ["No visible pixels exist in the final edited image."]
        canvas = Image.new("RGBA", (settings.output_width, settings.output_height), (0, 0, 0, 0))
        return NormalizedOutputResult(
            image=canvas,
            content_bounds=bounds,
            scaled_content_size=(0, 0),
            placed_rect=(0, 0, 0, 0),
            clipped_pixels=0,
            warnings=warnings,
            checksum=_checksum_bytes(canvas.tobytes()),
        )

    scaled_size, warnings = scale_size_for_mode(source_for_scale.size, settings)
    scaled = resize_nearest_neighbor(source_for_scale, *scaled_size)
    canvas = Image.new("RGBA", (settings.output_width, settings.output_height), (0, 0, 0, 0))
    x, y = anchor_offset((settings.output_width, settings.output_height), scaled.size, settings)
    clipped_pixels = 0
    dest_left = max(0, x)
    dest_top = max(0, y)
    src_left = max(0, -x)
    src_top = max(0, -y)
    dest_right = min(settings.output_width, x + scaled.width)
    dest_bottom = min(settings.output_height, y + scaled.height)
    if dest_right <= dest_left or dest_bottom <= dest_top:
        clipped_pixels = scaled.width * scaled.height
        warnings.append("Sprite is completely outside the output canvas.")
    else:
        src_right = src_left + (dest_right - dest_left)
        src_bottom = src_top + (dest_bottom - dest_top)
        crop = scaled.crop((src_left, src_top, src_right, src_bottom))
        canvas.alpha_composite(crop, dest=(dest_left, dest_top))
        clipped_pixels = scaled.width * scaled.height - crop.width * crop.height

    if clipped_pixels > 0:
        warnings.append(f"Clipped {clipped_pixels} pixels against the output canvas.")
        if not settings.allow_overflow:
            warnings.append("Overflow is not allowed; export should be confirmed or resolved.")
    checksum = _checksum_bytes(canvas.tobytes())
    return NormalizedOutputResult(
        image=canvas,
        content_bounds=bounds,
        scaled_content_size=scaled.size,
        placed_rect=(dest_left, dest_top, scaled.width, scaled.height),
        clipped_pixels=clipped_pixels,
        warnings=warnings,
        checksum=checksum,
    )


def detect_bottommost_visible_pixel(image: Image.Image) -> tuple[int, int] | None:
    bounds = transparent_bounds(image)
    if bounds.is_empty:
        return None
    alpha = np.asarray(image.convert("RGBA"), dtype=np.uint8)[:, :, 3] > 0
    ys, xs = np.where(alpha)
    index = int(np.argmax(ys))
    return int(xs[index]), int(ys[index])


def suggest_contact_point(image: Image.Image) -> tuple[int, int] | None:
    bounds = transparent_bounds(image)
    if bounds.is_empty:
        return None
    return bounds.left + bounds.content_width // 2, bounds.bottom - 1


def set_baseline_from_current_sprite(settings: NormalizationSettingsModel, image: Image.Image) -> None:
    contact = detect_bottommost_visible_pixel(image)
    if contact is None:
        return
    settings.baseline_y = contact[1]


def normalized_thumbnail(
    image: Image.Image,
    canvas_size: tuple[int, int],
    include_canvas: bool = True,
    thumbnail_size: int = 64,
) -> Image.Image:
    normalized = image.convert("RGBA")
    if include_canvas:
        return resize_nearest_neighbor(normalized, thumbnail_size, thumbnail_size)
    bounds = transparent_bounds(normalized)
    if bounds.is_empty:
        return resize_nearest_neighbor(normalized, thumbnail_size, thumbnail_size)
    cropped = normalized.crop((bounds.left, bounds.top, bounds.right, bounds.bottom))
    ratio = min(thumbnail_size / cropped.width, thumbnail_size / cropped.height)
    return resize_nearest_neighbor(
        cropped,
        max(1, int(round(cropped.width * ratio))),
        max(1, int(round(cropped.height * ratio))),
    )


def checksum_for_normalization(settings: NormalizationSettingsModel) -> str:
    return settings.checksum()


def stale_normalized_export(normalized_checksum: str | None, current_checksum: str) -> bool:
    return bool(normalized_checksum) and normalized_checksum != current_checksum


def report_rows(
    assets: Iterable[Any],
    *,
    include_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for asset in assets:
        settings = getattr(asset, "normalization", NormalizationSettingsModel())
        row = {
            "asset_name": getattr(asset, "display_name", ""),
            "source_crop_size": f"{getattr(asset.crop_rect, 'width', 0)}x{getattr(asset.crop_rect, 'height', 0)}" if getattr(asset, "crop_rect", None) else "0x0",
            "final_edited_size": f"{getattr(asset, 'manual_edit_width', 0) or 0}x{getattr(asset, 'manual_edit_height', 0) or 0}",
            "output_canvas_size": f"{settings.output_width}x{settings.output_height}",
            "scaled_content_size": f"{settings.target_sprite_width or settings.output_width}x{settings.target_sprite_height or settings.output_height}",
            "scale_percentage": settings.scale_percent,
            "baseline": settings.baseline_y,
            "pivot": f"{settings.pivot_x},{settings.pivot_y}",
            "alignment_group": getattr(asset, "alignment_group", ""),
            "clipping_status": bool(getattr(asset, "normalized_export_path", "")) and getattr(asset, "normalized_export_timestamp", "") != "",
            "warning_count": 0,
            "normalized_export_status": bool(getattr(asset, "normalized_export_path", "")),
        }
        rows.append(row)
    return rows


def copy_normalization_from_previous_frame(
    previous: NormalizationSettingsModel,
    current: NormalizationSettingsModel | None = None,
) -> NormalizationSettingsModel:
    cloned = NormalizationSettingsModel.from_dict(previous.to_dict())
    if current is not None:
        cloned.normalized_output_filename = current.normalized_output_filename or cloned.normalized_output_filename
    return cloned


def copy_normalization_from_group_leader(leader: NormalizationSettingsModel) -> NormalizationSettingsModel:
    return NormalizationSettingsModel.from_dict(leader.to_dict())


def stabilize_animation_suggestion(assets: Iterable[Any]) -> dict[str, int]:
    x_values: list[int] = []
    y_values: list[int] = []
    pivot_x_values: list[int] = []
    pivot_y_values: list[int] = []
    for asset in assets:
        if getattr(asset, "contact_x", None) is not None:
            x_values.append(int(asset.contact_x))
        if getattr(asset, "contact_y", None) is not None:
            y_values.append(int(asset.contact_y))
        if getattr(asset, "pivot_x", None) is not None:
            pivot_x_values.append(int(asset.pivot_x))
        if getattr(asset, "pivot_y", None) is not None:
            pivot_y_values.append(int(asset.pivot_y))
    def median(values: list[int]) -> int:
        if not values:
            return 0
        values = sorted(values)
        mid = len(values) // 2
        return values[mid]
    return {
        "median_contact_x": median(x_values),
        "median_contact_y": median(y_values),
        "median_pivot_x": median(pivot_x_values),
        "median_pivot_y": median(pivot_y_values),
    }


def alignment_diagnostics(assets: Iterable[Any]) -> AlignmentDiagnostics:
    x_values: list[float] = []
    baseline_values: list[float] = []
    pivot_values: list[float] = []
    bounds_values: list[float] = []
    for asset in assets:
        if getattr(asset, "contact_x", None) is not None:
            x_values.append(float(asset.contact_x))
        if getattr(asset, "baseline_y", None) is not None:
            baseline_values.append(float(asset.baseline_y))
        if getattr(asset, "pivot_x", None) is not None and getattr(asset, "pivot_y", None) is not None:
            pivot_values.append(float(asset.pivot_x) + float(asset.pivot_y))
        if getattr(asset, "crop_rect", None) is not None:
            bounds_values.append(float(asset.crop_rect.width * asset.crop_rect.height))
    def variance(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        return float(np.var(np.asarray(values, dtype=np.float64)))
    warning = None
    if x_values and max(x_values) - min(x_values) >= 3:
        warning = f"Frame {len(x_values):02d} is positioned {int(max(x_values) - min(x_values))} pixels farther right than the group median."
    return AlignmentDiagnostics(
        horizontal_center_variance=variance(x_values),
        baseline_variance=variance(baseline_values),
        pivot_variance=variance(pivot_values),
        content_bounds_variance=variance(bounds_values),
        likely_frame_jump_warning=warning,
    )


def report_to_csv(rows: list[dict[str, Any]], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    return path


def report_to_json(rows: list[dict[str, Any]], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
