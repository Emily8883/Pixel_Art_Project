from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from PIL import Image

from .manual_editing import ManualEditDocument, manual_edit_checksum


@dataclass(slots=True)
class ManualEditValidation:
    valid: bool
    messages: list[str]
    sidecar_path: Path | None = None
    dimensions_match: bool = False
    checksum_match: bool = False
    source_checksum_match: bool = False
    settings_checksum_match: bool = False


def project_data_dir(project_path: str | Path) -> Path:
    return Path(project_path).parent / ".project_data"


def edits_dir(project_path: str | Path) -> Path:
    return project_data_dir(project_path) / "edits"


def thumbnails_dir(project_path: str | Path) -> Path:
    return project_data_dir(project_path) / "thumbnails"


def recovery_dir(project_path: str | Path) -> Path:
    return project_data_dir(project_path) / "recovery"


def manual_edit_sidecar_path(project_path: str | Path, asset_uuid: str) -> Path:
    return edits_dir(project_path) / f"{asset_uuid}.png"


def manual_thumbnail_path(project_path: str | Path, asset_uuid: str) -> Path:
    return thumbnails_dir(project_path) / f"{asset_uuid}.png"


def save_sidecar_png(document: ManualEditDocument, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.stem}.{os.getpid()}.{path.suffix.lstrip('.')}.tmp")
    if temp_path.exists():
        temp_path.unlink()
    document.final_image().save(temp_path, format="PNG")
    os.replace(temp_path, path)
    return path


def load_sidecar_png(path: str | Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA")


def validate_manual_sidecar(
    sidecar_path: str | Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_checksum: str | None = None,
    expected_source_sheet_checksum: str | None = None,
    expected_settings_checksum: str | None = None,
    actual_source_sheet_checksum: str | None = None,
    actual_settings_checksum: str | None = None,
) -> ManualEditValidation:
    path = Path(sidecar_path)
    messages: list[str] = []
    if not path.exists():
        return ManualEditValidation(valid=False, messages=["Manual edit sidecar is missing."], sidecar_path=path)

    try:
        image = load_sidecar_png(path)
    except Exception as exc:  # pragma: no cover - PIL error variety
        return ManualEditValidation(valid=False, messages=[f"Failed to read manual edit sidecar: {exc}"], sidecar_path=path)

    dimensions_match = image.width == expected_width and image.height == expected_height
    checksum = manual_edit_checksum(image)
    checksum_match = expected_checksum is None or checksum == expected_checksum
    source_match = expected_source_sheet_checksum is None or expected_source_sheet_checksum == actual_source_sheet_checksum
    settings_match = expected_settings_checksum is None or expected_settings_checksum == actual_settings_checksum

    if not dimensions_match:
        messages.append("Manual edit sidecar dimensions do not match the crop.")
    if expected_checksum is not None and not checksum_match:
        messages.append("Manual edit sidecar checksum does not match.")
    if expected_source_sheet_checksum is not None and not source_match:
        messages.append("Source sheet checksum has changed since the manual edit was saved.")
    if expected_settings_checksum is not None and not settings_match:
        messages.append("Cleanup settings checksum has changed since the manual edit was saved.")

    valid = dimensions_match and checksum_match and source_match and settings_match
    return ManualEditValidation(
        valid=valid,
        messages=messages,
        sidecar_path=path,
        dimensions_match=dimensions_match,
        checksum_match=checksum_match,
        source_checksum_match=source_match,
        settings_checksum_match=settings_match,
    )


def locate_replacement_sidecar(project_path: str | Path, asset_uuid: str, candidates: list[str | Path]) -> Path | None:
    target_name = f"{asset_uuid}.png"
    for candidate in candidates:
        path = Path(candidate)
        if path.is_dir():
            direct = path / target_name
            if direct.exists():
                return direct
        elif path.name == target_name and path.exists():
            return path
    fallback = manual_edit_sidecar_path(project_path, asset_uuid)
    if fallback.exists():
        return fallback
    return None

