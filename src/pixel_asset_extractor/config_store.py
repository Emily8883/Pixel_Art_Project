from __future__ import annotations

import json
from pathlib import Path

from .exceptions import ConfigError
from .models import CropConfig, CropRect
from .processing import ui_tolerance_to_distance


def save_config(config: CropConfig, output_path: str | Path) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return destination


def _extract_crop_rect(payload: dict[str, object]) -> dict[str, object]:
    if "crop_rect" in payload and isinstance(payload["crop_rect"], dict):
        return payload["crop_rect"]
    return payload


def _read_background_rgba(payload: dict[str, object]) -> tuple[int, int, int, int] | None:
    raw_value = payload.get("background_rgba")
    if raw_value is None:
        return None
    if not isinstance(raw_value, (list, tuple)) or len(raw_value) != 4:
        raise ConfigError("background_rgba must be a 4-item list or tuple")
    return tuple(int(component) for component in raw_value)  # type: ignore[return-value]


def load_config(path: str | Path) -> CropConfig:
    source = Path(path)
    if not source.exists():
        raise ConfigError(f"Configuration file does not exist: {source}")

    try:
        with source.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:  # pragma: no cover - json module errors vary
        raise ConfigError(f"Failed to read configuration file: {source}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"Invalid configuration structure: {source}")

    try:
        crop_data = _extract_crop_rect(payload)
        crop_rect = CropRect.from_dict(crop_data)
        tolerance_ui = int(payload.get("tolerance_ui", 5))
        tolerance_threshold = float(payload.get("tolerance_threshold", ui_tolerance_to_distance(tolerance_ui)))
        extras = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "config_version",
                "version",
                "source_image",
                "crop_rect",
                "x",
                "y",
                "width",
                "height",
                "background_rgba",
                "tolerance_ui",
                "tolerance_threshold",
                "connected_background_only",
                "connectivity",
                "output_raw_filename",
                "output_clean_filename",
                "export_directory",
            }
        }
        return CropConfig(
            source_image=str(payload.get("source_image", "")),
            crop_rect=crop_rect,
            background_rgba=_read_background_rgba(payload),
            tolerance_ui=tolerance_ui,
            tolerance_threshold=tolerance_threshold,
            connected_background_only=bool(payload.get("connected_background_only", True)),
            connectivity=int(payload.get("connectivity", 4)),
            output_raw_filename=str(payload.get("output_raw_filename", "")),
            output_clean_filename=str(payload.get("output_clean_filename", "")),
            export_directory=payload.get("export_directory"),
            config_version=int(payload.get("config_version", payload.get("version", 1))),
            extras=extras,
        )
    except Exception as exc:  # pragma: no cover - validation branch
        raise ConfigError(f"Invalid configuration structure: {source}") from exc
