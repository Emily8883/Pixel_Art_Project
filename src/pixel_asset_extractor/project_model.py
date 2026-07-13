from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import CropRect
from .detection import BackgroundSample, CropProposal, DetectionSettingsModel, ExclusionZone
from .processing import ui_tolerance_to_distance
from .normalization import NormalizationSettingsModel


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WorkflowStatus(str, Enum):
    planned = "planned"
    cropped = "cropped"
    cleaned = "cleaned"
    reviewed = "reviewed"
    exported = "exported"
    needs_revision = "needs_revision"


@dataclass(slots=True)
class ActivityEntry:
    timestamp: str
    event_type: str
    message: str
    asset_uuid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "asset_uuid": self.asset_uuid,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ActivityEntry":
        return cls(
            timestamp=str(payload.get("timestamp", utc_now_iso())),
            event_type=str(payload.get("event_type", "unknown")),
            message=str(payload.get("message", "")),
            asset_uuid=payload.get("asset_uuid"),
        )


@dataclass(slots=True)
class SourceSheet:
    source_sheet_id: str
    label: str
    path: str
    checksum: str | None = None
    width: int | None = None
    height: int | None = None
    missing: bool = False
    detection_settings: DetectionSettingsModel = field(default_factory=DetectionSettingsModel)
    background_samples: list[BackgroundSample] = field(default_factory=list)
    exclusion_zones: list[ExclusionZone] = field(default_factory=list)
    crop_proposals: list[CropProposal] = field(default_factory=list)
    analysis_region: CropRect | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, project_dir: Path | None = None) -> dict[str, Any]:
        data = {
            "source_sheet_id": self.source_sheet_id,
            "label": self.label,
            "path": self.path,
            "checksum": self.checksum,
            "width": self.width,
            "height": self.height,
            "missing": self.missing,
            "detection_settings": self.detection_settings.to_dict(),
            "background_samples": [sample.to_dict() for sample in self.background_samples],
            "exclusion_zones": [zone.to_dict() for zone in self.exclusion_zones],
            "crop_proposals": [proposal.to_dict() for proposal in self.crop_proposals],
            "analysis_region": self.analysis_region.to_dict() if self.analysis_region else None,
        }
        if project_dir is not None:
            data["path"] = _serialize_path(self.path, project_dir)
        data.update(self.extras)
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any], project_dir: Path | None = None) -> "SourceSheet":
        extras = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "source_sheet_id",
                "label",
                "path",
                "checksum",
                "width",
                "height",
                "missing",
                "detection_settings",
                "background_samples",
                "exclusion_zones",
                "crop_proposals",
                "analysis_region",
            }
        }
        path = str(payload.get("path", ""))
        if project_dir is not None and path:
            path = str(_deserialize_path(path, project_dir))
        return cls(
            source_sheet_id=str(payload.get("source_sheet_id", str(uuid4()))),
            label=str(payload.get("label", Path(path).stem or "Source Sheet")),
            path=path,
            checksum=payload.get("checksum"),
            width=_maybe_int(payload.get("width")),
            height=_maybe_int(payload.get("height")),
            missing=bool(payload.get("missing", False)),
            detection_settings=DetectionSettingsModel.from_dict(payload.get("detection_settings", {})),
            background_samples=[BackgroundSample.from_dict(item) for item in payload.get("background_samples", []) if isinstance(item, dict)],
            exclusion_zones=[ExclusionZone.from_dict(item) for item in payload.get("exclusion_zones", []) if isinstance(item, dict)],
            crop_proposals=[CropProposal.from_dict(item) for item in payload.get("crop_proposals", []) if isinstance(item, dict)],
            analysis_region=CropRect.from_dict(payload["analysis_region"]) if isinstance(payload.get("analysis_region"), dict) else None,
            extras=extras,
        )


@dataclass(slots=True)
class ExportInfo:
    exported_path: str | None = None
    exported_at: str | None = None

    def to_dict(self, project_dir: Path | None = None) -> dict[str, Any]:
        exported_path = self.exported_path
        if exported_path and project_dir is not None:
            exported_path = _serialize_path(exported_path, project_dir)
        return {"exported_path": exported_path, "exported_at": self.exported_at}

    @classmethod
    def from_dict(cls, payload: dict[str, Any], project_dir: Path | None = None) -> "ExportInfo":
        exported_path = payload.get("exported_path")
        if exported_path and project_dir is not None:
            exported_path = str(_deserialize_path(str(exported_path), project_dir))
        return cls(
            exported_path=exported_path,
            exported_at=payload.get("exported_at"),
        )


@dataclass(slots=True)
class BackgroundRemovalSettingsModel:
    background_rgba: tuple[int, int, int, int] | None = None
    tolerance_ui: int = 5
    tolerance_threshold: float = field(default_factory=lambda: ui_tolerance_to_distance(5))
    connected_background_only: bool = True
    connectivity: int = 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "background_rgba": list(self.background_rgba) if self.background_rgba is not None else None,
            "tolerance_ui": self.tolerance_ui,
            "tolerance_threshold": self.tolerance_threshold,
            "connected_background_only": self.connected_background_only,
            "connectivity": self.connectivity,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BackgroundRemovalSettingsModel":
        background = payload.get("background_rgba")
        background_rgba = tuple(background) if isinstance(background, (list, tuple)) and len(background) == 4 else None
        tolerance_ui = _maybe_int(payload.get("tolerance_ui", 5)) or 5
        threshold_value = payload.get("tolerance_threshold")
        return cls(
            background_rgba=background_rgba,  # type: ignore[arg-type]
            tolerance_ui=tolerance_ui,
            tolerance_threshold=float(threshold_value) if threshold_value is not None else ui_tolerance_to_distance(tolerance_ui),
            connected_background_only=bool(payload.get("connected_background_only", True)),
            connectivity=8 if int(payload.get("connectivity", 4)) == 8 else 4,
        )


@dataclass(slots=True)
class AssetRecord:
    asset_uuid: str
    display_name: str
    character_group: str = ""
    category: str = ""
    action: str = ""
    direction: str = ""
    frame_number: int | None = None
    variant: str = ""
    source_sheet_id: str = ""
    source_sheet_path: str = ""
    crop_rect: CropRect | None = None
    background_removal: BackgroundRemovalSettingsModel = field(default_factory=BackgroundRemovalSettingsModel)
    raw_output_filename: str = ""
    clean_output_filename: str = ""
    output_folder: str = ""
    workflow_status: WorkflowStatus = WorkflowStatus.planned
    notes: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)
    export_info: ExportInfo = field(default_factory=ExportInfo)
    normalization: NormalizationSettingsModel = field(default_factory=NormalizationSettingsModel)
    alignment_group: str = ""
    is_alignment_group_leader: bool = False
    contact_x: int | None = None
    contact_y: int | None = None
    baseline_y: int | None = None
    pivot_x: int | None = None
    pivot_y: int | None = None
    includes_ground_shadow: bool = False
    shadow_should_be_separate: bool = False
    normalized_export_path: str = ""
    normalized_exported_at: str | None = None
    normalization_checksum: str | None = None
    normalization_confirmed: bool = False
    manual_edit_sidecar: str = ""
    manual_edit_checksum: str | None = None
    manual_edit_width: int | None = None
    manual_edit_height: int | None = None
    manual_edit_source_sheet_checksum: str | None = None
    manual_edit_cleanup_settings_checksum: str | None = None
    manual_edit_modified_at: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, project_dir: Path | None = None) -> dict[str, Any]:
        data = {
            "asset_uuid": self.asset_uuid,
            "display_name": self.display_name,
            "character_group": self.character_group,
            "category": self.category,
            "action": self.action,
            "direction": self.direction,
            "frame_number": self.frame_number,
            "variant": self.variant,
            "source_sheet_id": self.source_sheet_id,
            "source_sheet_path": self.source_sheet_path,
            "crop_rect": self.crop_rect.to_dict() if self.crop_rect else None,
            "background_removal": self.background_removal.to_dict(),
            "raw_output_filename": self.raw_output_filename,
            "clean_output_filename": self.clean_output_filename,
            "output_folder": self.output_folder,
            "workflow_status": self.workflow_status.value,
            "notes": self.notes,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "export_info": self.export_info.to_dict(project_dir),
            "normalization": self.normalization.to_dict(),
            "alignment_group": self.alignment_group,
            "is_alignment_group_leader": self.is_alignment_group_leader,
            "contact_x": self.contact_x,
            "contact_y": self.contact_y,
            "baseline_y": self.baseline_y,
            "pivot_x": self.pivot_x,
            "pivot_y": self.pivot_y,
            "includes_ground_shadow": self.includes_ground_shadow,
            "shadow_should_be_separate": self.shadow_should_be_separate,
            "normalized_export_path": self.normalized_export_path,
            "normalized_exported_at": self.normalized_exported_at,
            "normalization_checksum": self.normalization_checksum,
            "normalization_confirmed": self.normalization_confirmed,
            "manual_edit_sidecar": self.manual_edit_sidecar,
            "manual_edit_checksum": self.manual_edit_checksum,
            "manual_edit_width": self.manual_edit_width,
            "manual_edit_height": self.manual_edit_height,
            "manual_edit_source_sheet_checksum": self.manual_edit_source_sheet_checksum,
            "manual_edit_cleanup_settings_checksum": self.manual_edit_cleanup_settings_checksum,
            "manual_edit_modified_at": self.manual_edit_modified_at,
        }
        if project_dir is not None:
            data["source_sheet_path"] = _serialize_path(self.source_sheet_path, project_dir)
            data["output_folder"] = _serialize_path(self.output_folder, project_dir)
        data.update(self.extras)
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any], project_dir: Path | None = None) -> "AssetRecord":
        extras = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "asset_uuid",
                "display_name",
                "character_group",
                "category",
                "action",
                "direction",
                "frame_number",
                "variant",
                "source_sheet_id",
                "source_sheet_path",
                "crop_rect",
                "background_removal",
                "raw_output_filename",
                "clean_output_filename",
                "output_folder",
                "workflow_status",
                "notes",
                "created_at",
                "modified_at",
                "export_info",
                "normalization",
                "alignment_group",
                "is_alignment_group_leader",
                "contact_x",
                "contact_y",
                "baseline_y",
                "pivot_x",
                "pivot_y",
                "includes_ground_shadow",
                "shadow_should_be_separate",
                "normalized_export_path",
                "normalized_exported_at",
                "normalization_checksum",
                "normalization_confirmed",
                "manual_edit_sidecar",
                "manual_edit_checksum",
                "manual_edit_width",
                "manual_edit_height",
                "manual_edit_source_sheet_checksum",
                "manual_edit_cleanup_settings_checksum",
                "manual_edit_modified_at",
            }
        }
        source_sheet_path = str(payload.get("source_sheet_path", ""))
        output_folder = str(payload.get("output_folder", ""))
        if project_dir is not None:
            if source_sheet_path:
                source_sheet_path = str(_deserialize_path(source_sheet_path, project_dir))
            if output_folder:
                output_folder = str(_deserialize_path(output_folder, project_dir))
        crop_rect_value = payload.get("crop_rect")
        crop_rect = CropRect.from_dict(crop_rect_value) if isinstance(crop_rect_value, dict) else None
        return cls(
            asset_uuid=str(payload.get("asset_uuid", str(uuid4()))),
            display_name=str(payload.get("display_name", "")),
            character_group=str(payload.get("character_group", "")),
            category=str(payload.get("category", "")),
            action=str(payload.get("action", "")),
            direction=str(payload.get("direction", "")),
            frame_number=_maybe_int(payload.get("frame_number")),
            variant=str(payload.get("variant", "")),
            source_sheet_id=str(payload.get("source_sheet_id", "")),
            source_sheet_path=source_sheet_path,
            crop_rect=crop_rect,
            background_removal=BackgroundRemovalSettingsModel.from_dict(payload.get("background_removal", {})),
            raw_output_filename=str(payload.get("raw_output_filename", "")),
            clean_output_filename=str(payload.get("clean_output_filename", "")),
            output_folder=output_folder,
            workflow_status=WorkflowStatus(str(payload.get("workflow_status", WorkflowStatus.planned.value))),
            notes=str(payload.get("notes", "")),
            created_at=str(payload.get("created_at", utc_now_iso())),
            modified_at=str(payload.get("modified_at", utc_now_iso())),
            export_info=ExportInfo.from_dict(payload.get("export_info", {}), project_dir),
            normalization=NormalizationSettingsModel.from_dict(payload.get("normalization", {})),
            alignment_group=str(payload.get("alignment_group", "")),
            is_alignment_group_leader=bool(payload.get("is_alignment_group_leader", False)),
            contact_x=_maybe_int(payload.get("contact_x")),
            contact_y=_maybe_int(payload.get("contact_y")),
            baseline_y=_maybe_int(payload.get("baseline_y")),
            pivot_x=_maybe_int(payload.get("pivot_x")),
            pivot_y=_maybe_int(payload.get("pivot_y")),
            includes_ground_shadow=bool(payload.get("includes_ground_shadow", False)),
            shadow_should_be_separate=bool(payload.get("shadow_should_be_separate", False)),
            normalized_export_path=str(payload.get("normalized_export_path", "")),
            normalized_exported_at=payload.get("normalized_exported_at"),
            normalization_checksum=payload.get("normalization_checksum"),
            normalization_confirmed=bool(payload.get("normalization_confirmed", False)),
            manual_edit_sidecar=str(payload.get("manual_edit_sidecar", "")),
            manual_edit_checksum=payload.get("manual_edit_checksum"),
            manual_edit_width=_maybe_int(payload.get("manual_edit_width")),
            manual_edit_height=_maybe_int(payload.get("manual_edit_height")),
            manual_edit_source_sheet_checksum=payload.get("manual_edit_source_sheet_checksum"),
            manual_edit_cleanup_settings_checksum=payload.get("manual_edit_cleanup_settings_checksum"),
            manual_edit_modified_at=payload.get("manual_edit_modified_at"),
            extras=extras,
        )


@dataclass(slots=True)
class ProjectDefaults:
    output_folder: str = ""

    def to_dict(self, project_dir: Path | None = None) -> dict[str, Any]:
        output_folder = self.output_folder
        if output_folder and project_dir is not None:
            output_folder = _serialize_path(output_folder, project_dir)
        return {"output_folder": output_folder}

    @classmethod
    def from_dict(cls, payload: dict[str, Any], project_dir: Path | None = None) -> "ProjectDefaults":
        output_folder = str(payload.get("output_folder", ""))
        if output_folder and project_dir is not None:
            output_folder = str(_deserialize_path(output_folder, project_dir))
        return cls(output_folder=output_folder)


@dataclass(slots=True)
class ProjectRecord:
    project_name: str
    project_root_directory: str
    project_version: int = 6
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)
    notes: str = ""
    defaults: ProjectDefaults = field(default_factory=ProjectDefaults)
    autosave_enabled: bool = True
    autosave_interval_seconds: int = 60
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, project_dir: Path | None = None) -> dict[str, Any]:
        data = {
            "project_name": self.project_name,
            "project_root_directory": self.project_root_directory,
            "project_version": self.project_version,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "notes": self.notes,
            "defaults": self.defaults.to_dict(project_dir),
            "autosave_enabled": self.autosave_enabled,
            "autosave_interval_seconds": self.autosave_interval_seconds,
        }
        if project_dir is not None:
            data["project_root_directory"] = _serialize_path(self.project_root_directory, project_dir)
        data.update(self.extras)
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any], project_dir: Path | None = None) -> "ProjectRecord":
        extras = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "project_name",
                "project_root_directory",
                "project_version",
                "created_at",
                "modified_at",
                "notes",
                "defaults",
                "autosave_enabled",
                "autosave_interval_seconds",
            }
        }
        project_root_directory = str(payload.get("project_root_directory", ""))
        if project_dir is not None and project_root_directory:
            project_root_directory = str(_deserialize_path(project_root_directory, project_dir))
        return cls(
            project_name=str(payload.get("project_name", "Untitled Project")),
            project_root_directory=project_root_directory,
            project_version=int(payload.get("project_version", 6)),
            created_at=str(payload.get("created_at", utc_now_iso())),
            modified_at=str(payload.get("modified_at", utc_now_iso())),
            notes=str(payload.get("notes", "")),
            defaults=ProjectDefaults.from_dict(payload.get("defaults", {}), project_dir),
            autosave_enabled=bool(payload.get("autosave_enabled", True)),
            autosave_interval_seconds=int(payload.get("autosave_interval_seconds", 60)),
            extras=extras,
        )


@dataclass(slots=True)
class SpriteProject:
    project: ProjectRecord
    source_sheets: list[SourceSheet] = field(default_factory=list)
    assets: list[AssetRecord] = field(default_factory=list)
    activity_log: list[ActivityEntry] = field(default_factory=list)
    detection_presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    legacy_fields: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None
    modified: bool = False

    def to_dict(self) -> dict[str, Any]:
        project_dir = self.path.parent if self.path else None
        payload = {
            "config_version": 6,
            "project": self.project.to_dict(project_dir),
            "source_sheets": [sheet.to_dict(project_dir) for sheet in self.source_sheets],
            "assets": [asset.to_dict(project_dir) for asset in self.assets],
            "activity_log": [entry.to_dict() for entry in self.activity_log],
            "detection_presets": self.detection_presets,
        }
        payload.update(self.legacy_fields)
        return payload

    def mark_modified(self) -> None:
        self.modified = True
        self.project.modified_at = utc_now_iso()

    def log(self, event_type: str, message: str, asset_uuid: str | None = None) -> None:
        self.activity_log.append(ActivityEntry(timestamp=utc_now_iso(), event_type=event_type, message=message, asset_uuid=asset_uuid))
        self.activity_log = self.activity_log[-1000:]
        self.mark_modified()


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _serialize_path(path_value: str, project_dir: Path) -> str:
    if not path_value:
        return path_value
    path = Path(path_value)
    try:
        relative = path.resolve().relative_to(project_dir.resolve())
        return str(relative).replace("\\", "/")
    except Exception:
        try:
            relative = path.relative_to(project_dir)
            return str(relative).replace("\\", "/")
        except Exception:
            return str(path)


def _deserialize_path(path_value: str, project_dir: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (project_dir / path).resolve()
