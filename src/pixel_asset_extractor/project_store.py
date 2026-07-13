from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from PIL import Image

from .config_store import load_config as load_legacy_config
from .exceptions import ConfigError
from .models import CropConfig, CropRect
from .project_model import ActivityEntry, AssetRecord, BackgroundRemovalSettingsModel, ProjectRecord, SourceSheet, SpriteProject, WorkflowStatus, utc_now_iso


def checksum_file(path: str | Path) -> str:
    hasher = sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_project_from_legacy_config(config: CropConfig | dict[str, Any], project_path: Path | None = None) -> SpriteProject:
    if isinstance(config, CropConfig):
        crop_rect = config.crop_rect
        source_image = config.source_image
        background = config.background_rgba
        tolerance_ui = config.tolerance_ui
        tolerance_threshold = config.tolerance_threshold
        connected_background_only = config.connected_background_only
        connectivity = config.connectivity
        raw_filename = config.output_raw_filename
        clean_filename = config.output_clean_filename
        export_directory = config.export_directory or ""
        project_version = getattr(config, "config_version", 2)
        extras = dict(config.extras)
    else:
        crop_rect = CropRect.from_dict(config.get("crop_rect", config))
        source_image = str(config.get("source_image", ""))
        background = _read_background(config.get("background_rgba"))
        tolerance_ui = int(config.get("tolerance_ui", 5))
        tolerance_threshold = float(config.get("tolerance_threshold", 0.0))
        connected_background_only = bool(config.get("connected_background_only", True))
        connectivity = int(config.get("connectivity", 4))
        raw_filename = str(config.get("output_raw_filename", ""))
        clean_filename = str(config.get("output_clean_filename", ""))
        export_directory = str(config.get("export_directory", ""))
        project_version = int(config.get("config_version", config.get("version", 1)))
        extras = {
            key: value
            for key, value in config.items()
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

    project_root = str(project_path.parent if project_path else Path.cwd())
    source_sheet = SourceSheet(
        source_sheet_id=str(uuid4()),
        label=Path(source_image).stem or "Source Sheet",
        path=source_image,
        checksum=checksum_file(source_image) if source_image and Path(source_image).exists() else None,
        missing=not Path(source_image).exists() if source_image else True,
    )
    if source_image and Path(source_image).exists():
        with Image.open(source_image) as image:
            source_sheet.width = image.width
            source_sheet.height = image.height
    asset = AssetRecord(
        asset_uuid="legacy",
        display_name=Path(source_image).stem or "legacy_asset",
        source_sheet_path=source_image,
        source_sheet_id=source_sheet.source_sheet_id,
        crop_rect=crop_rect,
        raw_output_filename=raw_filename,
        clean_output_filename=clean_filename,
        output_folder=export_directory,
        background_removal=BackgroundRemovalSettingsModel(
            background_rgba=background,
            tolerance_ui=tolerance_ui,
            tolerance_threshold=tolerance_threshold,
            connected_background_only=connected_background_only,
            connectivity=connectivity,
        ),
        workflow_status=WorkflowStatus.cropped if crop_rect.is_valid() else WorkflowStatus.planned,
    )
    project = SpriteProject(
        project=ProjectRecord(
            project_name="Migrated Project",
            project_root_directory=project_root,
            project_version=5,
            created_at=utc_now_iso(),
            modified_at=utc_now_iso(),
        ),
        source_sheets=[source_sheet],
        assets=[asset],
    )
    project.legacy_fields["legacy_version"] = project_version
    project.legacy_fields["legacy_extras"] = extras
    return project


def save_project(project: SpriteProject, file_path: str | Path) -> Path:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    project.path = path
    payload = json.dumps(project.to_dict(), indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), suffix=".tmp", encoding="utf-8") as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = Path(tmp.name)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_bytes(path.read_bytes())
    os.replace(temp_path, path)
    return path


def load_project(
    file_path: str | Path,
    recover_backup: Callable[[Path, Path], bool] | None = None,
) -> SpriteProject:
    path = Path(file_path)
    try:
        return _load_project_file(path)
    except Exception as primary_exc:
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists() and recover_backup is not None and recover_backup(path, backup):
            return _load_project_file(backup)
        raise ConfigError(f"Could not load project file: {path}") from primary_exc


def _load_project_file(path: Path) -> SpriteProject:
    if not path.exists():
        raise ConfigError(f"Project file does not exist: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"Malformed project file: {path}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"Malformed project file: {path}")

    config_version = int(payload.get("config_version", payload.get("version", 1)))
    if config_version in (3, 4, 5) and "project" in payload:
        return _load_v3_project(payload, path)
    if config_version in (1, 2) or {"crop_rect", "x", "y", "width", "height"} & payload.keys():
        legacy = load_legacy_config(path)
        return build_project_from_legacy_config(legacy, path)
    return _load_v3_project(payload, path)


def _load_v3_project(payload: dict[str, Any], path: Path) -> SpriteProject:
    project_dir = path.parent
    project_payload = payload.get("project", {})
    source_sheets = payload.get("source_sheets", [])
    assets = payload.get("assets", [])
    activity_log = payload.get("activity_log", [])
    project = ProjectRecord.from_dict(project_payload if isinstance(project_payload, dict) else {}, project_dir)
    source_sheet_records = [SourceSheet.from_dict(item, project_dir) for item in source_sheets if isinstance(item, dict)]
    asset_records = [AssetRecord.from_dict(item, project_dir) for item in assets if isinstance(item, dict)]
    from .project_model import ActivityEntry

    activity_entries = [ActivityEntry.from_dict(item) for item in activity_log if isinstance(item, dict)]
    legacy_fields = {
        key: value
        for key, value in payload.items()
        if key not in {"config_version", "project", "source_sheets", "assets", "activity_log"}
    }
    project.project_version = 5
    return SpriteProject(
        project=project,
        source_sheets=source_sheet_records,
        assets=asset_records,
        activity_log=activity_entries,
        legacy_fields=legacy_fields,
        path=path,
    )


def _read_background(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(int(v) for v in value)  # type: ignore[return-value]
    return None


def detect_newer_autosave(project_path: str | Path) -> Path | None:
    path = Path(project_path)
    autosave = path.with_suffix(path.suffix + ".autosave")
    if autosave.exists() and (not path.exists() or autosave.stat().st_mtime > path.stat().st_mtime):
        return autosave
    return None
