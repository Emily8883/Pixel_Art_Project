from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4
import shutil
from copy import deepcopy

from PIL import Image

from .config_store import load_config as load_legacy_config
from .image_tools import crop_image, load_png
from .manual_editing import ManualEditDocument, compute_settings_checksum
from .manual_storage import load_sidecar_png, validate_manual_sidecar
from .detection import (
    BackgroundSample,
    CropProposal,
    DetectionResult,
    DetectionSettingsModel,
    ExclusionZone,
    ProposalStatus,
    apply_detection_preset,
    common_color_suggestions,
    detect_crop_proposals,
    merge_proposals as merge_crop_proposals,
    split_proposal_horizontal as split_crop_proposal_horizontal,
    split_proposal_vertical as split_crop_proposal_vertical,
    proposal_cache_key,
)
from .batch_export import (
    BatchExportManifest,
    BatchExportSettings,
    ExportPreviewEntry,
    ExportVariant,
    ValidationIssue,
    build_export_preview,
    classify_asset_for_export,
    run_batch_export,
    validation_issues_for_project,
)
from .models import CropRect
from .normalization import (
    AlignmentDiagnostics,
    NormalizedOutputResult,
    NormalizationSettingsModel,
    checksum_for_normalization,
    detect_bottommost_visible_pixel,
    normalized_thumbnail,
    place_on_canvas,
    report_rows,
    report_to_csv,
    report_to_json,
    set_baseline_from_current_sprite,
    stale_normalized_export,
    suggest_contact_point,
    transparent_bounds,
)
from .manual_storage import (
    manual_edit_sidecar_path,
    save_sidecar_png,
    validate_manual_sidecar,
)
from .naming import format_frame_number, generate_filename, generate_normalized_filename, unique_filename
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
        self._proposal_undo_stacks: dict[str, list[tuple[list[CropProposal], list[ExclusionZone], list[BackgroundSample], DetectionSettingsModel]]] = {}
        self._proposal_redo_stacks: dict[str, list[tuple[list[CropProposal], list[ExclusionZone], list[BackgroundSample], DetectionSettingsModel]]] = {}
        self._detection_cache: dict[str, DetectionResult] = {}

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
        asset.normalization = self._default_normalization_for_asset(asset)
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
            normalization=NormalizationSettingsModel.from_dict(asset.normalization.to_dict()),
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
        normalization: NormalizationSettingsModel | None = None,
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
                normalization,
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
        if normalization is not None:
            asset.normalization = normalization
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

    def source_sheet(self, source_sheet_id: str) -> SourceSheet:
        for sheet in self.project.source_sheets:
            if sheet.source_sheet_id == source_sheet_id:
                return sheet
        raise KeyError(source_sheet_id)

    def _resolve_sheet_path(self, sheet: SourceSheet) -> Path | None:
        if not sheet.path:
            return None
        sheet_path = Path(sheet.path)
        if self.project.path is not None and not sheet_path.is_absolute():
            sheet_path = self.project.path.parent / sheet_path
        return sheet_path

    def _load_source_sheet_image(self, sheet: SourceSheet) -> Image.Image | None:
        sheet_path = self._resolve_sheet_path(sheet)
        if sheet_path is None or not sheet_path.exists():
            return None
        return load_png(sheet_path)

    def _proposal_state_snapshot(self, source_sheet_id: str) -> tuple[list[CropProposal], list[ExclusionZone], list[BackgroundSample], DetectionSettingsModel]:
        sheet = self.source_sheet(source_sheet_id)
        return (
            deepcopy(sheet.crop_proposals),
            deepcopy(sheet.exclusion_zones),
            deepcopy(sheet.background_samples),
            DetectionSettingsModel.from_dict(sheet.detection_settings.to_dict()),
        )

    def _push_proposal_history(self, source_sheet_id: str) -> None:
        self._proposal_undo_stacks.setdefault(source_sheet_id, []).append(self._proposal_state_snapshot(source_sheet_id))
        self._proposal_undo_stacks[source_sheet_id] = self._proposal_undo_stacks[source_sheet_id][-100:]
        self._proposal_redo_stacks.setdefault(source_sheet_id, []).clear()

    def _restore_proposal_state(self, source_sheet_id: str, snapshot: tuple[list[CropProposal], list[ExclusionZone], list[BackgroundSample], DetectionSettingsModel]) -> None:
        sheet = self.source_sheet(source_sheet_id)
        proposals, zones, samples, settings = snapshot
        sheet.crop_proposals = deepcopy(proposals)
        sheet.exclusion_zones = deepcopy(zones)
        sheet.background_samples = deepcopy(samples)
        sheet.detection_settings = DetectionSettingsModel.from_dict(settings.to_dict())
        self.project.mark_modified()

    def save_detection_preset(self, name: str, settings: DetectionSettingsModel) -> None:
        self.project.detection_presets[name] = settings.to_dict()
        self.project.mark_modified()

    def load_detection_preset(self, name: str) -> DetectionSettingsModel:
        payload = self.project.detection_presets.get(name)
        if payload is None:
            return apply_detection_preset(DetectionSettingsModel(), name)
        return DetectionSettingsModel.from_dict(payload)

    def add_background_sample(self, source_sheet_id: str, rgba: tuple[int, int, int, int], label: str = "", source: str = "manual") -> BackgroundSample:
        sheet = self.source_sheet(source_sheet_id)
        self._push_proposal_history(source_sheet_id)
        sample = BackgroundSample(rgba=rgba, source=source, label=label)
        sheet.background_samples.append(sample)
        sheet.detection_settings.background_samples = deepcopy(sheet.background_samples)
        self.project.mark_modified()
        return sample

    def clear_background_samples(self, source_sheet_id: str) -> None:
        sheet = self.source_sheet(source_sheet_id)
        self._push_proposal_history(source_sheet_id)
        sheet.background_samples.clear()
        sheet.detection_settings.background_samples = []
        self.project.mark_modified()

    def add_exclusion_zone(self, source_sheet_id: str, rect: CropRect, name: str = "Exclusion Zone", zone_type: str = "manual_rectangle") -> ExclusionZone:
        sheet = self.source_sheet(source_sheet_id)
        self._push_proposal_history(source_sheet_id)
        zone = ExclusionZone(source_sheet_uuid=source_sheet_id, rect=rect, name=name, zone_type=zone_type)
        sheet.exclusion_zones.append(zone)
        self.project.mark_modified()
        return zone

    def update_detection_settings(self, source_sheet_id: str, settings: DetectionSettingsModel) -> None:
        sheet = self.source_sheet(source_sheet_id)
        self._push_proposal_history(source_sheet_id)
        sheet.detection_settings = settings
        self.project.mark_modified()

    def detect_sprite_regions(self, source_sheet_id: str, settings: DetectionSettingsModel | None = None) -> DetectionResult:
        sheet = self.source_sheet(source_sheet_id)
        result, active_settings = self.preview_sprite_regions(source_sheet_id, settings)
        self.apply_detection_result(source_sheet_id, result, active_settings)
        return result

    def preview_sprite_regions(
        self,
        source_sheet_id: str,
        settings: DetectionSettingsModel | None = None,
        cancel_requested=None,
    ) -> tuple[DetectionResult, DetectionSettingsModel]:
        sheet = self.source_sheet(source_sheet_id)
        image = self._load_source_sheet_image(sheet)
        if image is None:
            raise RuntimeError("Source sheet image could not be loaded")
        active_settings = DetectionSettingsModel.from_dict((settings or sheet.detection_settings).to_dict())
        if not active_settings.background_samples:
            active_settings.background_samples = deepcopy(sheet.background_samples) or common_color_suggestions(image, limit=3)
        cache_key = proposal_cache_key(sheet.checksum, active_settings)
        cached = self._detection_cache.get(cache_key)
        if cached is not None:
            return cached, active_settings
        result = detect_crop_proposals(image, source_sheet_id, active_settings, exclusion_zones=sheet.exclusion_zones, cancel_requested=cancel_requested)
        self._detection_cache[cache_key] = result
        return result, active_settings

    def apply_detection_result(self, source_sheet_id: str, result: DetectionResult, settings: DetectionSettingsModel) -> None:
        sheet = self.source_sheet(source_sheet_id)
        self._push_proposal_history(source_sheet_id)
        sheet.detection_settings = settings
        sheet.crop_proposals = result.proposals
        self.project.mark_modified()

    def undo_proposal_edit(self, source_sheet_id: str) -> bool:
        undo_stack = self._proposal_undo_stacks.get(source_sheet_id)
        if not undo_stack:
            return False
        current = self._proposal_state_snapshot(source_sheet_id)
        snapshot = undo_stack.pop()
        self._proposal_redo_stacks.setdefault(source_sheet_id, []).append(current)
        self._restore_proposal_state(source_sheet_id, snapshot)
        return True

    def redo_proposal_edit(self, source_sheet_id: str) -> bool:
        redo_stack = self._proposal_redo_stacks.get(source_sheet_id)
        if not redo_stack:
            return False
        current = self._proposal_state_snapshot(source_sheet_id)
        snapshot = redo_stack.pop()
        self._proposal_undo_stacks.setdefault(source_sheet_id, []).append(current)
        self._restore_proposal_state(source_sheet_id, snapshot)
        return True

    def proposal_by_uuid(self, source_sheet_id: str, proposal_uuid: str) -> CropProposal:
        sheet = self.source_sheet(source_sheet_id)
        for proposal in sheet.crop_proposals:
            if proposal.proposal_uuid == proposal_uuid:
                return proposal
        raise KeyError(proposal_uuid)

    def move_proposal(self, source_sheet_id: str, proposal_uuid: str, dx: int, dy: int) -> CropProposal:
        proposal = self.proposal_by_uuid(source_sheet_id, proposal_uuid)
        self._push_proposal_history(source_sheet_id)
        proposal.rect = CropRect(proposal.rect.x + int(dx), proposal.rect.y + int(dy), proposal.rect.width, proposal.rect.height)
        proposal.padded_rect = CropRect(proposal.padded_rect.x + int(dx), proposal.padded_rect.y + int(dy), proposal.padded_rect.width, proposal.padded_rect.height)
        proposal.status = ProposalStatus.modified
        proposal.user_modified = True
        proposal.modified_at = utc_now_iso()
        self.project.mark_modified()
        return proposal

    def resize_proposal(self, source_sheet_id: str, proposal_uuid: str, width: int, height: int) -> CropProposal:
        proposal = self.proposal_by_uuid(source_sheet_id, proposal_uuid)
        self._push_proposal_history(source_sheet_id)
        proposal.rect = CropRect(proposal.rect.x, proposal.rect.y, max(1, int(width)), max(1, int(height)))
        proposal.padded_rect = CropRect(proposal.padded_rect.x, proposal.padded_rect.y, max(1, int(width)), max(1, int(height)))
        proposal.width = proposal.rect.width
        proposal.height = proposal.rect.height
        proposal.status = ProposalStatus.modified
        proposal.user_modified = True
        proposal.modified_at = utc_now_iso()
        self.project.mark_modified()
        return proposal

    def merge_proposals(self, source_sheet_id: str, proposal_uuids: Iterable[str], padding: int = 0) -> CropProposal:
        sheet = self.source_sheet(source_sheet_id)
        selected = [proposal for proposal in sheet.crop_proposals if proposal.proposal_uuid in set(proposal_uuids)]
        if len(selected) < 2:
            raise ValueError("At least two proposals are required to merge")
        self._push_proposal_history(source_sheet_id)
        merged = merge_crop_proposals(selected, source_sheet_uuid=source_sheet_id, padding=padding)
        sheet.crop_proposals = [proposal for proposal in sheet.crop_proposals if proposal.proposal_uuid not in {item.proposal_uuid for item in selected}]
        sheet.crop_proposals.append(merged)
        self.project.mark_modified()
        return merged

    def split_proposal_vertical(self, source_sheet_id: str, proposal_uuid: str, split_x: int) -> tuple[CropProposal, CropProposal]:
        sheet = self.source_sheet(source_sheet_id)
        proposal = self.proposal_by_uuid(source_sheet_id, proposal_uuid)
        self._push_proposal_history(source_sheet_id)
        left, right = split_crop_proposal_vertical(proposal, split_x)
        sheet.crop_proposals = [item for item in sheet.crop_proposals if item.proposal_uuid != proposal_uuid] + [left, right]
        self.project.mark_modified()
        return left, right

    def split_proposal_horizontal(self, source_sheet_id: str, proposal_uuid: str, split_y: int) -> tuple[CropProposal, CropProposal]:
        sheet = self.source_sheet(source_sheet_id)
        proposal = self.proposal_by_uuid(source_sheet_id, proposal_uuid)
        self._push_proposal_history(source_sheet_id)
        top, bottom = split_crop_proposal_horizontal(proposal, split_y)
        sheet.crop_proposals = [item for item in sheet.crop_proposals if item.proposal_uuid != proposal_uuid] + [top, bottom]
        self.project.mark_modified()
        return top, bottom

    def assign_proposal_to_asset(self, source_sheet_id: str, proposal_uuid: str, asset_uuid: str, warn_before_replace: bool = True) -> AssetRecord:
        proposal = self.proposal_by_uuid(source_sheet_id, proposal_uuid)
        asset = self.get_asset(asset_uuid)
        if warn_before_replace and asset.crop_rect is not None:
            pass
        self._push_proposal_history(source_sheet_id)
        asset.crop_rect = proposal.rect
        asset.workflow_status = WorkflowStatus.cropped
        asset.modified_at = utc_now_iso()
        proposal.status = ProposalStatus.assigned
        proposal.assigned_asset_uuid = asset.asset_uuid
        proposal.modified_at = utc_now_iso()
        self.invalidate_manual_edits(asset.asset_uuid, "proposal_assigned")
        self.project.log("proposal_assigned", f"Assigned proposal {proposal.proposal_uuid} to {asset.display_name}", asset.asset_uuid)
        self.project.mark_modified()
        return asset

    def create_asset_from_proposal(self, source_sheet_id: str, proposal_uuid: str, **overrides) -> AssetRecord:
        proposal = self.proposal_by_uuid(source_sheet_id, proposal_uuid)
        sheet = self.source_sheet(source_sheet_id)
        base_name = overrides.get("display_name") or f"detected_region_{len(self.project.assets) + 1:02d}"
        asset = self.add_asset(
            display_name=base_name,
            source_sheet_id=sheet.source_sheet_id,
            source_sheet_path=sheet.path,
            character_group=overrides.get("character_group", ""),
            category=overrides.get("category", "other"),
            action=overrides.get("action", "detected"),
            direction=overrides.get("direction", "none"),
            frame_number=overrides.get("frame_number"),
            variant=overrides.get("variant", ""),
            output_folder=overrides.get("output_folder", ""),
            notes=overrides.get("notes", proposal.notes),
            crop_rect=proposal.rect,
        )
        proposal.status = ProposalStatus.assigned
        proposal.assigned_asset_uuid = asset.asset_uuid
        proposal.modified_at = utc_now_iso()
        self.project.mark_modified()
        return asset

    def _raw_crop_for_asset(self, asset: AssetRecord) -> Image.Image | None:
        sheet = next((item for item in self.project.source_sheets if item.source_sheet_id == asset.source_sheet_id), None)
        if sheet is None or not sheet.path or asset.crop_rect is None:
            return None
        sheet_path = Path(sheet.path)
        if self.project.path is not None and not sheet_path.is_absolute():
            sheet_path = self.project.path.parent / sheet_path
        if not sheet_path.exists():
            return None
        image = load_png(sheet_path)
        return crop_image(image, asset.crop_rect)

    def _apply_cleaning(self, raw_image: Image.Image, settings: BackgroundRemovalSettingsModel):
        config = BackgroundRemovalSettings(
            background_rgba=settings.background_rgba,
            tolerance_ui=settings.tolerance_ui,
            connected_background_only=settings.connected_background_only,
            connectivity=settings.connectivity,
        )
        return apply_background_removal(raw_image, config)

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

    def normalize_active_asset(self, asset_uuid: str | None = None) -> NormalizedOutputResult:
        asset = self.get_asset(asset_uuid) if asset_uuid is not None else self.active_asset
        if asset is None:
            raise RuntimeError("No active asset")
        result = self._normalized_output_for_asset(asset)
        asset.normalization_checksum = checksum_for_normalization(asset.normalization)
        asset.normalization_confirmed = True
        asset.normalized_export_path = asset.normalized_export_path or ""
        asset.baseline_y = asset.normalization.baseline_y
        asset.pivot_x = asset.normalization.pivot_x
        asset.pivot_y = asset.normalization.pivot_y
        asset.modified_at = utc_now_iso()
        self.project.mark_modified()
        return result

    def export_normalized_asset(self, destination: str | Path, asset_uuid: str | None = None) -> Path:
        asset = self.get_asset(asset_uuid) if asset_uuid is not None else self.active_asset
        if asset is None:
            raise RuntimeError("No active asset")
        result = self.normalize_active_asset(asset.asset_uuid)
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        result.image.save(destination_path, format="PNG")
        asset.normalized_export_path = str(destination_path)
        asset.normalized_exported_at = utc_now_iso()
        asset.normalization_checksum = result.checksum
        asset.workflow_status = WorkflowStatus.exported
        asset.modified_at = utc_now_iso()
        self.project.log("normalized_export_success", f"Exported normalized {asset.display_name}", asset.asset_uuid)
        self.project.mark_modified()
        return destination_path

    def normalized_thumbnail_for_asset(self, asset_uuid: str, size: int = 64) -> Image.Image:
        asset = self.get_asset(asset_uuid)
        result = self._normalized_output_for_asset(asset)
        return normalized_thumbnail(
            result.image,
            (asset.normalization.output_width, asset.normalization.output_height),
            include_canvas=asset.normalization.include_canvas_in_thumbnail,
            thumbnail_size=size,
        )

    def thumbnail_for_asset(self, asset_uuid: str, size: int = 64) -> Image.Image:
        asset = self.get_asset(asset_uuid)
        if asset.normalized_export_path or asset.normalization.enabled:
            return self.normalized_thumbnail_for_asset(asset_uuid, size=size)
        raw_image = self._raw_crop_for_asset(asset)
        if raw_image is None:
            raise RuntimeError("Asset has no crop")
        clean = self._apply_cleaning(raw_image, asset.background_removal)
        return normalized_thumbnail(clean.cleaned_image, (size, size), include_canvas=False, thumbnail_size=size)

    def _normalized_output_for_asset(self, asset: AssetRecord) -> NormalizedOutputResult:
        final_image = self._final_image_for_asset(asset)
        return place_on_canvas(final_image, asset.normalization)

    def asset_normalization_report(self) -> list[dict[str, object]]:
        return report_rows(self.project.assets)

    def save_normalization_report_csv(self, path: str | Path) -> Path:
        return report_to_csv(self.asset_normalization_report(), path)

    def save_normalization_report_json(self, path: str | Path) -> Path:
        return report_to_json(self.asset_normalization_report(), path)

    def validate_project(self) -> list[ValidationIssue]:
        return validation_issues_for_project(self.project)

    def batch_export_preview(self, settings: BatchExportSettings) -> list[ExportPreviewEntry]:
        return build_export_preview(self.project, settings)

    def classify_asset_for_export(self, asset_uuid: str, requested_variants: Iterable[ExportVariant]) -> str:
        asset = self.get_asset(asset_uuid)
        return classify_asset_for_export(self.project, asset, requested_variants).state

    def run_batch_export(self, settings: BatchExportSettings, cancel_requested=None) -> tuple[BatchExportManifest, object]:
        return run_batch_export(self.project, settings, cancel_requested=cancel_requested)

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
        if not asset.normalization.normalized_output_filename:
            asset.normalization.normalized_output_filename = generate_normalized_filename(
                asset.character_group or asset.display_name,
                asset.category,
                asset.action or asset.display_name,
                asset.direction,
                asset.frame_number,
                asset.variant,
                canvas_size=(asset.normalization.output_width, asset.normalization.output_height),
            )

    def _default_normalization_for_asset(self, asset: AssetRecord) -> NormalizationSettingsModel:
        settings = NormalizationSettingsModel()
        if asset.category in {"character", "idle", "walk", "attack", "boss", "enemy", "enemy_small"}:
            settings.output_width = 48
            settings.output_height = 48
            if asset.category in {"enemy_small"}:
                settings.output_width = 32
                settings.output_height = 32
        if asset.category in {"projectile", "effect", "item"}:
            settings.output_width = 48
            settings.output_height = 48
            settings.anchor_mode = "center"
        if asset.category == "boss":
            settings.output_width = 64
            settings.output_height = 64
        settings.baseline_y = 45 if settings.output_height >= 48 else max(0, settings.output_height - 3)
        settings.pivot_x = settings.output_width // 2
        settings.pivot_y = settings.baseline_y
        settings.normalized_output_filename = generate_normalized_filename(
            asset.character_group or asset.display_name,
            asset.category,
            asset.action or asset.display_name,
            asset.direction,
            asset.frame_number,
            asset.variant,
            canvas_size=(settings.output_width, settings.output_height),
        )
        return settings

    def _final_image_for_asset(self, asset: AssetRecord) -> Image.Image:
        raw_image = self._raw_crop_for_asset(asset)
        if raw_image is None:
            raise RuntimeError("Asset has no raw crop")
        clean = self._apply_cleaning(raw_image, asset.background_removal)
        if asset.manual_edit_sidecar:
            sidecar = Path(asset.manual_edit_sidecar)
            if self.project.path is not None and not sidecar.is_absolute():
                sidecar = self.project.path.parent / sidecar
            validation = validate_manual_sidecar(
                sidecar,
                expected_width=raw_image.width,
                expected_height=raw_image.height,
                expected_checksum=asset.manual_edit_checksum,
                expected_source_sheet_checksum=asset.manual_edit_source_sheet_checksum,
                expected_settings_checksum=asset.manual_edit_cleanup_settings_checksum,
                actual_source_sheet_checksum=self._source_sheet_checksum_for_asset(asset),
                actual_settings_checksum=compute_settings_checksum(asset.background_removal.to_dict()),
            )
            if validation.valid:
                return load_sidecar_png(sidecar)
        return clean.cleaned_image
