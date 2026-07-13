from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4
import shutil

from PIL import Image

from .config_store import load_config as load_legacy_config
from .image_tools import crop_image
from .manual_editing import ManualEditDocument, compute_settings_checksum
from .manual_storage import (
    manual_edit_sidecar_path,
    save_sidecar_png,
    validate_manual_sidecar,
)
from .naming import format_frame_number, generate_filename, unique_filename
from .processing import BackgroundRemovalSettings, apply_background_removal, ui_tolerance_to_distance
from .project_model import (
    ActivityEntry,
    AssetRecord,
    BackgroundRemovalSettingsModel,
    ExportInfo,
    ProjectDefaults,
    ProjectRecord,
    SourceSheet,
    SpriteProject,
    WorkflowStatus,
    utc_now_iso,
)
from .project_store import checksum_file, detect_newer_autosave, load_project, save_project
from .templates import FREYA_MOVEMENT_TEMPLATE, TemplateAssetSpec


SUPPORTED_CATEGORIES = ("idle", "walk", "attack", "effect", "item", "portrait", "environment", "ui", "other")
SUPPORTED_DIRECTIONS = ("front", "back", "left", "right", "none")


def project_trash_path(project_path: str | Path, timestamp: str) -> Path:
    path = Path(project_path)
    return path.parent / ".trash" / timestamp.replace(":", "-")


class ProjectManager:
    def __init__(self, project: SpriteProject | None = None) -> None:
        self.project = project or SpriteProject(
            project=ProjectRecord(project_name="Untitled Project", project_root_directory=str(Path.cwd())),
        )
        self.active_asset_uuid: str | None = self.project.assets[0].asset_uuid if self.project.assets else None

    @property
    def active_asset(self) -> AssetRecord | None:
        if self.active_asset_uuid is None:
            return None
        for asset in self.project.assets:
            if asset.asset_uuid == self.active_asset_uuid:
                return asset
        return None

    def new_project(self, project_name: str, project_root_directory: str) -> None:
        self.project = SpriteProject(
            project=ProjectRecord(project_name=project_name, project_root_directory=project_root_directory),
        )
        self.active_asset_uuid = None
        self.project.activity_log.append(ActivityEntry(timestamp=utc_now_iso(), event_type="project_created", message=f"Project created: {project_name}"))
        self.project.activity_log = self.project.activity_log[-1000:]

    def load_project(self, file_path: str | Path) -> None:
        self.project = load_project(file_path)
        self.active_asset_uuid = self.project.assets[0].asset_uuid if self.project.assets else None
        self.project.activity_log.append(ActivityEntry(timestamp=utc_now_iso(), event_type="project_loaded", message=f"Loaded project from {file_path}"))
        self.project.activity_log = self.project.activity_log[-1000:]

    def save_project(self, file_path: str | Path) -> Path:
        self.project.path = Path(file_path)
        saved = save_project(self.project, file_path)
        self.project.modified = False
        self.project.activity_log.append(ActivityEntry(timestamp=utc_now_iso(), event_type="project_saved", message=f"Saved project to {saved}"))
        self.project.activity_log = self.project.activity_log[-1000:]
        return saved

    def save_project_as_dict(self) -> dict:
        return self.project.to_dict()

    def add_source_sheet(self, path: str | Path, label: str | None = None) -> SourceSheet:
        source_path = Path(path)
        sheet = SourceSheet(
            source_sheet_id=str(uuid4()),
            label=label or source_path.stem,
            path=str(source_path),
            checksum=checksum_file(source_path) if source_path.exists() else None,
            missing=not source_path.exists(),
        )
        if source_path.exists():
            from PIL import Image

            with Image.open(source_path) as image:
                sheet.width = image.width
                sheet.height = image.height
        self.project.source_sheets.append(sheet)
        self.project.log("source_sheet_added", f"Added source sheet {sheet.label}")
        self.project.mark_modified()
        return sheet

    def remove_source_sheet(self, source_sheet_id: str) -> bool:
        used = [asset for asset in self.project.assets if asset.source_sheet_id == source_sheet_id]
        self.project.source_sheets = [sheet for sheet in self.project.source_sheets if sheet.source_sheet_id != source_sheet_id]
        self.project.log(
            "source_sheet_removed",
            f"Removed source sheet {source_sheet_id}; affected assets: {len(used)}",
        )
        self.project.mark_modified()
        return bool(used)

    def relink_source_sheet(self, source_sheet_id: str, new_path: str | Path) -> None:
        for sheet in self.project.source_sheets:
            if sheet.source_sheet_id == source_sheet_id:
                sheet.path = str(new_path)
                sheet.missing = not Path(new_path).exists()
                sheet.checksum = checksum_file(new_path) if Path(new_path).exists() else None
                if Path(new_path).exists():
                    with Image.open(new_path) as image:
                        sheet.width = image.width
                        sheet.height = image.height
                for asset in self.project.assets:
                    if asset.source_sheet_id == source_sheet_id:
                        asset.source_sheet_path = str(new_path)
                self.project.log("source_sheet_relinked", f"Relinked {sheet.label}")
                self.project.mark_modified()
                return
        raise KeyError(source_sheet_id)

    def detect_missing_sources(self) -> list[SourceSheet]:
        missing: list[SourceSheet] = []
        for sheet in self.project.source_sheets:
            sheet.missing = not Path(sheet.path).exists()
            if sheet.missing:
                missing.append(sheet)
        return missing

    def source_sheet_checksum_changed(self, sheet: SourceSheet) -> bool:
        path = Path(sheet.path)
        return path.exists() and sheet.checksum is not None and checksum_file(path) != sheet.checksum

    def add_asset(
        self,
        display_name: str,
        source_sheet_id: str = "",
        source_sheet_path: str = "",
        character_group: str = "",
        category: str = "",
        action: str = "",
        direction: str = "",
        frame_number: int | None = None,
        variant: str = "",
        output_folder: str = "",
        notes: str = "",
        crop_rect=None,
    ) -> AssetRecord:
        asset = AssetRecord(
            asset_uuid=str(uuid4()),
            display_name=display_name,
            character_group=character_group,
            category=category,
            action=action,
            direction=direction,
            frame_number=frame_number,
            variant=variant,
            source_sheet_id=source_sheet_id,
            source_sheet_path=source_sheet_path,
            crop_rect=crop_rect,
            output_folder=output_folder,
            notes=notes,
            workflow_status=WorkflowStatus.cropped if crop_rect else WorkflowStatus.planned,
        )
        self._refresh_asset_filenames(asset)
        self.project.assets.append(asset)
        self.active_asset_uuid = asset.asset_uuid
        self.project.log("asset_created", f"Created asset {asset.display_name}", asset.asset_uuid)
        self.project.mark_modified()
        return asset

    def duplicate_asset(self, asset_uuid: str, preserve_crop_rect: bool = True) -> AssetRecord:
        asset = self.get_asset(asset_uuid)
        new_frame = self._next_available_frame(asset)
        new_asset = replace(
            asset,
            asset_uuid=str(uuid4()),
            display_name=self._duplicate_display_name(asset.display_name, new_frame),
            frame_number=new_frame,
            crop_rect=asset.crop_rect if preserve_crop_rect else None,
            workflow_status=WorkflowStatus.cropped if preserve_crop_rect and asset.crop_rect else WorkflowStatus.planned,
            export_info=ExportInfo(),
            modified_at=utc_now_iso(),
            created_at=utc_now_iso(),
            extras=dict(asset.extras),
        )
        self._refresh_asset_filenames(new_asset)
        self.project.assets.append(new_asset)
        self.active_asset_uuid = new_asset.asset_uuid
        self.project.log("asset_duplicated", f"Duplicated asset {asset.display_name}", new_asset.asset_uuid)
        self.project.mark_modified()
        return new_asset

    def delete_asset(self, asset_uuid: str, move_exports_to_trash: bool = False) -> tuple[AssetRecord, list[Path]]:
        asset = self.get_asset(asset_uuid)
        moved: list[Path] = []
        if move_exports_to_trash:
            moved = self._move_exported_files_to_trash(asset)
        self.project.assets = [item for item in self.project.assets if item.asset_uuid != asset_uuid]
        self.active_asset_uuid = self.project.assets[0].asset_uuid if self.project.assets else None
        self.project.activity_log.append(ActivityEntry(timestamp=utc_now_iso(), event_type="asset_deleted", message=f"Deleted asset {asset.display_name}", asset_uuid=asset_uuid))
        self.project.activity_log = self.project.activity_log[-1000:]
        self.project.mark_modified()
        return asset, moved

    def _move_exported_files_to_trash(self, asset: AssetRecord) -> list[Path]:
        moved: list[Path] = []
        if not self.project.path:
            return moved
        trash_dir = project_trash_path(self.project.path, utc_now_iso())
        trash_dir.mkdir(parents=True, exist_ok=True)
        for field in (asset.export_info.exported_path,):
            if field:
                source = Path(field)
                if source.exists():
                    destination = trash_dir / source.name
                    shutil.move(str(source), str(destination))
                    moved.append(destination)
        return moved

    def move_asset(self, asset_uuid: str, step: int) -> None:
        index = self._asset_index(asset_uuid)
        new_index = max(0, min(len(self.project.assets) - 1, index + step))
        if index == new_index:
            return
        asset = self.project.assets.pop(index)
        self.project.assets.insert(new_index, asset)
        self.project.log("asset_reordered", f"Moved asset {asset.display_name}")
        self.project.mark_modified()

    def mark_status(self, asset_uuid: str, status: WorkflowStatus) -> None:
        asset = self.get_asset(asset_uuid)
        asset.workflow_status = status
        asset.modified_at = utc_now_iso()
        self.project.log("status_changed", f"Status set to {status.value}", asset_uuid)
        self.project.mark_modified()

    def edit_asset(
        self,
        asset_uuid: str,
        *,
        crop_rect=None,
        background_rgba=None,
        tolerance_ui: int | None = None,
        connected_background_only: bool | None = None,
        connectivity: int | None = None,
        raw_output_filename: str | None = None,
        clean_output_filename: str | None = None,
        output_folder: str | None = None,
        notes: str | None = None,
    ) -> AssetRecord:
        asset = self.get_asset(asset_uuid)
        manual_invalidated = False
        if asset.workflow_status == WorkflowStatus.reviewed and any(
            value is not None
            for value in (
                crop_rect,
                background_rgba,
                tolerance_ui,
                connected_background_only,
                connectivity,
                raw_output_filename,
                clean_output_filename,
                output_folder,
                notes,
            )
        ):
            asset.workflow_status = WorkflowStatus.needs_revision
        if crop_rect is not None:
            asset.crop_rect = crop_rect
            manual_invalidated = True
            if asset.workflow_status == WorkflowStatus.planned:
                asset.workflow_status = WorkflowStatus.cropped
        if background_rgba is not None:
            asset.background_removal.background_rgba = background_rgba
            manual_invalidated = True
        if tolerance_ui is not None:
            asset.background_removal.tolerance_ui = int(tolerance_ui)
            asset.background_removal.tolerance_threshold = ui_tolerance_to_distance(int(tolerance_ui))
            manual_invalidated = True
        if connected_background_only is not None:
            asset.background_removal.connected_background_only = bool(connected_background_only)
            manual_invalidated = True
        if connectivity is not None:
            asset.background_removal.connectivity = 8 if int(connectivity) == 8 else 4
            manual_invalidated = True
        if raw_output_filename is not None:
            asset.raw_output_filename = raw_output_filename
        if clean_output_filename is not None:
            asset.clean_output_filename = clean_output_filename
        if output_folder is not None:
            asset.output_folder = output_folder
        if notes is not None:
            asset.notes = notes
        if manual_invalidated:
            self.invalidate_manual_edits(asset_uuid, "cleanup_or_crop_changed")
        asset.modified_at = utc_now_iso()
        self._refresh_asset_filenames(asset)
        self.project.mark_modified()
        return asset

    def invalidate_manual_edits(self, asset_uuid: str, reason: str = "manual_edits_invalidated") -> None:
        asset = self.get_asset(asset_uuid)
        asset.manual_edit_sidecar = ""
        asset.manual_edit_checksum = None
        asset.manual_edit_width = None
        asset.manual_edit_height = None
        asset.manual_edit_source_sheet_checksum = None
        asset.manual_edit_cleanup_settings_checksum = None
        asset.manual_edit_modified_at = utc_now_iso()
        self.project.log(reason, f"Manual edits cleared for {asset.display_name}", asset_uuid)

    def save_manual_edit_document(self, asset_uuid: str, document: ManualEditDocument, project_path: str | Path | None = None) -> Path:
        asset = self.get_asset(asset_uuid)
        project_path = Path(project_path or self.project.path or Path.cwd() / "project.json")
        destination = manual_edit_sidecar_path(project_path, asset_uuid)
        save_sidecar_png(document, destination)
        document.mark_clean()
        asset.manual_edit_sidecar = str(destination.relative_to(project_path.parent)) if destination.is_relative_to(project_path.parent) else str(destination)
        asset.manual_edit_checksum = document.checksum()
        asset.manual_edit_width, asset.manual_edit_height = document.size
        asset.manual_edit_source_sheet_checksum = self._source_sheet_checksum_for_asset(asset)
        asset.manual_edit_cleanup_settings_checksum = compute_settings_checksum(asset.background_removal.to_dict())
        asset.manual_edit_modified_at = utc_now_iso()
        self.project.log("manual_edit_saved", f"Saved manual edits for {asset.display_name}", asset_uuid)
        self.project.mark_modified()
        return destination

    def manual_edit_validation(self, asset_uuid: str, project_path: str | Path | None = None):
        asset = self.get_asset(asset_uuid)
        project_path = Path(project_path or self.project.path or Path.cwd() / "project.json")
        if not asset.manual_edit_sidecar:
            return None
        sidecar = Path(asset.manual_edit_sidecar)
        if not sidecar.is_absolute():
            sidecar = project_path.parent / sidecar
        return validate_manual_sidecar(
            sidecar,
            expected_width=asset.manual_edit_width or (asset.crop_rect.width if asset.crop_rect else 0),
            expected_height=asset.manual_edit_height or (asset.crop_rect.height if asset.crop_rect else 0),
            expected_checksum=asset.manual_edit_checksum,
            expected_source_sheet_checksum=asset.manual_edit_source_sheet_checksum,
            expected_settings_checksum=asset.manual_edit_cleanup_settings_checksum,
            actual_source_sheet_checksum=self._source_sheet_checksum_for_asset(asset),
            actual_settings_checksum=compute_settings_checksum(asset.background_removal.to_dict()),
        )

    def _source_sheet_checksum_for_asset(self, asset: AssetRecord) -> str | None:
        sheet = next((item for item in self.project.source_sheets if item.source_sheet_id == asset.source_sheet_id), None)
        if sheet is None:
            return None
        return sheet.checksum

    def apply_crop_to_active_asset(self, crop_rect) -> None:
        asset = self.active_asset
        if asset is None:
            return
        self.edit_asset(asset.asset_uuid, crop_rect=crop_rect)
        if asset.workflow_status == WorkflowStatus.planned and crop_rect is not None:
            asset.workflow_status = WorkflowStatus.cropped
        self.project.mark_modified()

    def apply_background_settings_to_active_asset(
        self,
        background_rgba,
        tolerance_ui: int,
        connected_background_only: bool,
        connectivity: int,
    ) -> None:
        asset = self.active_asset
        if asset is None:
            return
        self.edit_asset(
            asset.asset_uuid,
            background_rgba=background_rgba,
            tolerance_ui=tolerance_ui,
            connected_background_only=connected_background_only,
            connectivity=connectivity,
        )
        if background_rgba is not None and asset.crop_rect is not None:
            asset.workflow_status = WorkflowStatus.cleaned
        self.project.mark_modified()

    def export_active_asset(
        self,
        source_image: Image.Image,
        clean_image: Image.Image,
        kind: str,
        destination: str | Path,
    ) -> Path:
        asset = self.active_asset
        if asset is None:
            raise RuntimeError("No active asset")
        image = source_image if kind == "raw" else clean_image
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGBA").save(destination_path, format="PNG")
        if kind == "raw":
            asset.export_info.exported_path = str(destination_path)
        else:
            asset.export_info.exported_path = str(destination_path)
        asset.export_info.exported_at = utc_now_iso()
        asset.workflow_status = WorkflowStatus.exported
        asset.modified_at = utc_now_iso()
        self.project.log("export_success", f"Exported {asset.display_name} ({kind})", asset.asset_uuid)
        self.project.mark_modified()
        return destination_path

    def project_progress_counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in WorkflowStatus}
        for asset in self.project.assets:
            counts[asset.workflow_status.value] += 1
        counts["total"] = len(self.project.assets)
        counts["exported_count"] = counts[WorkflowStatus.exported.value]
        return counts

    def group_progress_counts(self, character_group: str) -> dict[str, int]:
        assets = [asset for asset in self.project.assets if asset.character_group == character_group]
        counts = {status.value: 0 for status in WorkflowStatus}
        for asset in assets:
            counts[asset.workflow_status.value] += 1
        counts["total"] = len(assets)
        counts["exported_count"] = counts[WorkflowStatus.exported.value]
        return counts

    def create_freya_movement_template(self, source_sheet_id: str, source_sheet_path: str) -> tuple[list[AssetRecord], list[TemplateAssetSpec]]:
        created: list[AssetRecord] = []
        skipped: list[TemplateAssetSpec] = []
        existing_names = {asset.display_name for asset in self.project.assets}
        for spec in FREYA_MOVEMENT_TEMPLATE:
            if spec.display_name in existing_names:
                skipped.append(spec)
                continue
            asset = self.add_asset(
                display_name=spec.display_name,
                source_sheet_id=source_sheet_id,
                source_sheet_path=source_sheet_path,
                character_group=spec.character_group,
                category=spec.category,
                action=spec.action,
                direction=spec.direction,
                frame_number=spec.frame_number,
                variant=spec.variant,
            )
            created.append(asset)
            existing_names.add(spec.display_name)
        return created, skipped

    def duplicate_filename_conflict(self, asset: AssetRecord) -> bool:
        filenames = {item.raw_output_filename for item in self.project.assets if item.asset_uuid != asset.asset_uuid}
        filenames |= {item.clean_output_filename for item in self.project.assets if item.asset_uuid != asset.asset_uuid}
        return asset.raw_output_filename in filenames or asset.clean_output_filename in filenames

    def regenerate_filenames(self, asset_uuid: str) -> AssetRecord:
        asset = self.get_asset(asset_uuid)
        self._refresh_asset_filenames(asset)
        return asset

    def get_asset(self, asset_uuid: str) -> AssetRecord:
        for asset in self.project.assets:
            if asset.asset_uuid == asset_uuid:
                return asset
        raise KeyError(asset_uuid)

    def _asset_index(self, asset_uuid: str) -> int:
        for index, asset in enumerate(self.project.assets):
            if asset.asset_uuid == asset_uuid:
                return index
        raise KeyError(asset_uuid)

    def _next_available_frame(self, asset: AssetRecord) -> int | None:
        if asset.frame_number is None:
            return None
        used = {
            item.frame_number
            for item in self.project.assets
            if item.character_group == asset.character_group
            and item.category == asset.category
            and item.action == asset.action
            and item.direction == asset.direction
            and item.variant == asset.variant
            and item.frame_number is not None
        }
        candidate = asset.frame_number + 1
        while candidate in used:
            candidate += 1
        return candidate

    def _duplicate_display_name(self, display_name: str, frame_number: int | None) -> str:
        if frame_number is None:
            return f"{display_name} copy"
        stem = display_name.rsplit("_", 1)[0] if "_" in display_name else display_name
        return f"{stem}_{format_frame_number(frame_number)}"

    def _refresh_asset_filenames(self, asset: AssetRecord) -> None:
        generated = generate_filename(
            asset.character_group or asset.display_name,
            asset.category,
            asset.action or asset.display_name,
            asset.direction,
            asset.frame_number,
            asset.variant,
        )
        if not asset.raw_output_filename:
            asset.raw_output_filename = generated
        if not asset.clean_output_filename:
            asset.clean_output_filename = generated.replace(".png", "_clean.png")
        filenames = {item.raw_output_filename for item in self.project.assets if item.asset_uuid != asset.asset_uuid}
        filenames |= {item.clean_output_filename for item in self.project.assets if item.asset_uuid != asset.asset_uuid}
        if asset.raw_output_filename in filenames or asset.clean_output_filename in filenames:
            base = Path(asset.raw_output_filename).stem
            asset.raw_output_filename = unique_filename(asset.raw_output_filename, filenames)
            asset.clean_output_filename = unique_filename(asset.clean_output_filename, filenames)
