from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import csv
import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image

from .detection import ProposalStatus
from .exceptions import CropError, ImageLoadError
from .image_tools import crop_image, load_png
from .manual_editing import compute_settings_checksum
from .manual_storage import validate_manual_sidecar
from .naming import format_frame_number, generate_asset_basename, normalize_snake, unique_filename
from .normalization import checksum_for_normalization, place_on_canvas, stale_normalized_export, transparent_bounds
from .processing import BackgroundRemovalSettings, apply_background_removal, export_png
from .project_model import AssetRecord, SourceSheet, SpriteProject, WorkflowStatus


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ExportVariant(str, Enum):
    raw = "raw"
    auto_clean = "auto_clean"
    final_edited = "final_edited"
    normalized = "normalized"


class ExportScope(str, Enum):
    selected_assets = "selected_assets"
    visible_filtered_assets = "visible_filtered_assets"
    active_character_group = "active_character_group"
    active_category = "active_category"
    active_alignment_group = "active_alignment_group"
    reviewed_assets = "reviewed_assets"
    cleaned_assets = "cleaned_assets"
    normalized_assets = "normalized_assets"
    non_exported_assets = "non_exported_assets"
    all_assets = "all_assets"


class OverwritePolicy(str, Enum):
    ask_each_time = "ask_each_time"
    skip_existing = "skip_existing"
    overwrite_existing = "overwrite_existing"
    rename_with_suffix = "rename_with_suffix"
    compare_checksum_skip_identical = "compare_checksum_skip_identical"


class ValidationSeverity(str, Enum):
    blocked = "blocked"
    warning = "warning"
    info = "info"


@dataclass(slots=True)
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    asset_uuid: str
    asset_name: str
    category: str
    message: str
    detail: str
    suggested_fix: str
    auto_fix_available: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "asset_uuid": self.asset_uuid,
            "asset_name": self.asset_name,
            "category": self.category,
            "message": self.message,
            "detail": self.detail,
            "suggested_fix": self.suggested_fix,
            "auto_fix_available": self.auto_fix_available,
        }


@dataclass(slots=True)
class AssetExportClassification:
    state: str
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExportPreviewEntry:
    asset_uuid: str
    asset_name: str
    variant: ExportVariant
    destination_path: str
    output_width: int
    output_height: int
    validation_state: str
    overwrite_action: str
    existing_file_state: str
    checksum_compare_result: str
    estimated_file_count: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_uuid": self.asset_uuid,
            "asset_name": self.asset_name,
            "variant": self.variant.value,
            "destination_path": self.destination_path,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "validation_state": self.validation_state,
            "overwrite_action": self.overwrite_action,
            "existing_file_state": self.existing_file_state,
            "checksum_compare_result": self.checksum_compare_result,
            "estimated_file_count": self.estimated_file_count,
            "reasons": self.reasons,
        }


@dataclass(slots=True)
class BatchExportSettings:
    scope: ExportScope = ExportScope.selected_assets
    selected_asset_uuids: list[str] = field(default_factory=list)
    variants: list[ExportVariant] = field(default_factory=lambda: [ExportVariant.normalized])
    include_final_backup: bool = False
    include_auto_clean_backup: bool = False
    include_raw_reference_crop: bool = False
    output_root: str = ""
    project_relative: bool = True
    flat_output: bool = False
    directory_template: str = "Characters/{character}/{category}/{direction}"
    filename_template: str = "{character}_{action}_{direction}_{frame}_{width}x{height}.png"
    frame_padding: int = 2
    overwrite_policy: OverwritePolicy = OverwritePolicy.compare_checksum_skip_identical
    generate_manifest: bool = True
    generate_csv_manifest: bool = True
    generate_html_manifest: bool = True
    generate_json_manifest: bool = True
    create_resume_state: bool = True
    validation_severity_floor: ValidationSeverity = ValidationSeverity.warning
    selected_character_group: str = ""
    selected_category: str = ""
    selected_alignment_group: str = ""
    selected_asset_mode: str = ""
    visible_asset_uuids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scope"] = self.scope.value
        payload["variants"] = [variant.value for variant in self.variants]
        payload["overwrite_policy"] = self.overwrite_policy.value
        payload["validation_severity_floor"] = self.validation_severity_floor.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "BatchExportSettings":
        payload = payload or {}
        variants = payload.get("variants", [ExportVariant.normalized.value])
        scope = payload.get("scope", ExportScope.selected_assets.value)
        overwrite_policy = payload.get("overwrite_policy", OverwritePolicy.compare_checksum_skip_identical.value)
        floor = payload.get("validation_severity_floor", ValidationSeverity.warning.value)
        return cls(
            scope=ExportScope(scope),
            selected_asset_uuids=[str(item) for item in payload.get("selected_asset_uuids", [])],
            variants=[ExportVariant(str(item)) for item in variants],
            include_final_backup=bool(payload.get("include_final_backup", False)),
            include_auto_clean_backup=bool(payload.get("include_auto_clean_backup", False)),
            include_raw_reference_crop=bool(payload.get("include_raw_reference_crop", False)),
            output_root=str(payload.get("output_root", "")),
            project_relative=bool(payload.get("project_relative", True)),
            flat_output=bool(payload.get("flat_output", False)),
            directory_template=str(payload.get("directory_template", "Characters/{character}/{category}/{direction}")),
            filename_template=str(payload.get("filename_template", "{character}_{action}_{direction}_{frame}_{width}x{height}.png")),
            frame_padding=int(payload.get("frame_padding", 2)),
            overwrite_policy=OverwritePolicy(overwrite_policy),
            generate_manifest=bool(payload.get("generate_manifest", True)),
            generate_csv_manifest=bool(payload.get("generate_csv_manifest", True)),
            generate_html_manifest=bool(payload.get("generate_html_manifest", True)),
            generate_json_manifest=bool(payload.get("generate_json_manifest", True)),
            create_resume_state=bool(payload.get("create_resume_state", True)),
            validation_severity_floor=ValidationSeverity(floor),
            selected_character_group=str(payload.get("selected_character_group", "")),
            selected_category=str(payload.get("selected_category", "")),
            selected_alignment_group=str(payload.get("selected_alignment_group", "")),
            selected_asset_mode=str(payload.get("selected_asset_mode", "")),
            visible_asset_uuids=[str(item) for item in payload.get("visible_asset_uuids", [])],
        )


@dataclass(slots=True)
class BatchExportManifest:
    created_at: str
    project_name: str
    output_root: str
    settings: dict[str, Any]
    entries: list[ExportPreviewEntry]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "project_name": self.project_name,
            "output_root": self.output_root,
            "settings": self.settings,
            "entries": [entry.to_dict() for entry in self.entries],
            "summary": self.summary,
        }


@dataclass(slots=True)
class ExportJobState:
    job_id: str
    created_at: str
    settings_checksum: str
    completed_keys: list[str] = field(default_factory=list)
    exported_files: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "created_at": self.created_at,
            "settings_checksum": self.settings_checksum,
            "completed_keys": self.completed_keys,
            "exported_files": self.exported_files,
            "failures": self.failures,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExportJobState":
        return cls(
            job_id=str(payload.get("job_id", "")),
            created_at=str(payload.get("created_at", utc_now_iso())),
            settings_checksum=str(payload.get("settings_checksum", "")),
            completed_keys=[str(item) for item in payload.get("completed_keys", [])],
            exported_files=[str(item) for item in payload.get("exported_files", [])],
            failures=[dict(item) for item in payload.get("failures", []) if isinstance(item, dict)],
        )


def _stable_checksum(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def sanitize_path_component(value: str) -> str:
    value = normalize_snake(value)
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1F]", "_", value)
    return value.strip("._") or "untitled"


def sanitize_filename(filename: str) -> str:
    filename = filename.replace("\\", "/").split("/")[-1]
    stem = Path(filename).stem
    suffix = Path(filename).suffix or ".png"
    stem = sanitize_path_component(stem)
    if not suffix.lower().endswith(".png"):
        suffix = ".png"
    return f"{stem}{suffix.lower()}"


def resolve_template(template: str, values: dict[str, Any]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace(f"{{{key}}}", str(value or ""))
    result = re.sub(r"__+", "_", result)
    result = re.sub(r"_+", "_", result)
    result = re.sub(r"(?<=/)_+|_+(?=/)", "", result)
    result = re.sub(r"/+", "/", result)
    return result.strip("/ ")


def asset_values(asset: AssetRecord, variant: ExportVariant, width: int, height: int, frame_padding: int = 2) -> dict[str, Any]:
    frame = ""
    if asset.frame_number is not None:
        frame = f"{int(asset.frame_number):0{max(2, min(4, int(frame_padding)))}d}"
    return {
        "group": sanitize_path_component(asset.character_group or asset.display_name),
        "character": sanitize_path_component(asset.character_group or asset.display_name),
        "category": sanitize_path_component(asset.category),
        "action": sanitize_path_component(asset.action or asset.category or asset.display_name),
        "direction": sanitize_path_component(asset.direction or "none"),
        "alignment_group": sanitize_path_component(asset.alignment_group or "ungrouped"),
        "variant": variant.value,
        "width": int(width),
        "height": int(height),
        "frame": frame,
    }


def resolve_output_directory(output_root: Path, asset: AssetRecord, variant: ExportVariant, settings: BatchExportSettings) -> Path:
    if settings.flat_output:
        return output_root
    values = asset_values(asset, variant, asset.normalization.output_width, asset.normalization.output_height, settings.frame_padding)
    directory = resolve_template(settings.directory_template, values)
    components = [sanitize_path_component(part) for part in Path(directory).parts if part not in (".", "..")]
    safe_path = output_root
    for component in components:
        safe_path = safe_path / component
    if len(settings.variants) > 1:
        safe_path = safe_path / sanitize_path_component(variant.value)
    return safe_path


def resolve_output_filename(asset: AssetRecord, variant: ExportVariant, settings: BatchExportSettings, width: int, height: int) -> str:
    values = asset_values(asset, variant, width, height, settings.frame_padding)
    filename = resolve_template(settings.filename_template, values)
    return sanitize_filename(filename or f"{values['character']}_{variant.value}_{width}x{height}.png")


def checksum_for_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_checksum(image: Image.Image) -> str:
    return sha256(image.convert("RGBA").tobytes()).hexdigest()


def select_assets(project: SpriteProject, settings: BatchExportSettings) -> list[AssetRecord]:
    assets = list(project.assets)
    if settings.scope == ExportScope.selected_assets and settings.selected_asset_uuids:
        selected = set(settings.selected_asset_uuids)
        assets = [asset for asset in assets if asset.asset_uuid in selected]
    elif settings.scope == ExportScope.active_character_group and settings.selected_character_group:
        assets = [asset for asset in assets if asset.character_group == settings.selected_character_group]
    elif settings.scope == ExportScope.active_category and settings.selected_category:
        assets = [asset for asset in assets if asset.category == settings.selected_category]
    elif settings.scope == ExportScope.active_alignment_group and settings.selected_alignment_group:
        assets = [asset for asset in assets if asset.alignment_group == settings.selected_alignment_group]
    elif settings.scope == ExportScope.reviewed_assets:
        assets = [asset for asset in assets if asset.workflow_status == WorkflowStatus.reviewed]
    elif settings.scope == ExportScope.cleaned_assets:
        assets = [asset for asset in assets if asset.workflow_status in {WorkflowStatus.cleaned, WorkflowStatus.reviewed, WorkflowStatus.exported}]
    elif settings.scope == ExportScope.normalized_assets:
        assets = [asset for asset in assets if asset.normalization.enabled or asset.normalized_export_path]
    elif settings.scope == ExportScope.non_exported_assets:
        assets = [asset for asset in assets if not asset.export_info.exported_path and not asset.normalized_export_path]
    elif settings.scope == ExportScope.visible_filtered_assets and settings.visible_asset_uuids:
        visible = set(settings.visible_asset_uuids)
        assets = [asset for asset in assets if asset.asset_uuid in visible]
    return assets


def _asset_source_sheet(project: SpriteProject, asset: AssetRecord) -> SourceSheet | None:
    return next((sheet for sheet in project.source_sheets if sheet.source_sheet_id == asset.source_sheet_id), None)


def _raw_crop_for_asset(project: SpriteProject, asset: AssetRecord) -> Image.Image | None:
    sheet = _asset_source_sheet(project, asset)
    if sheet is None or not sheet.path or asset.crop_rect is None:
        return None
    path = Path(sheet.path)
    if project.path is not None and not path.is_absolute():
        path = project.path.parent / path
    if not path.exists():
        return None
    try:
        image = load_png(path)
    except ImageLoadError:
        return None
    try:
        return crop_image(image, asset.crop_rect)
    except CropError:
        return None


def _auto_clean_for_asset(project: SpriteProject, asset: AssetRecord) -> Image.Image | None:
    raw = _raw_crop_for_asset(project, asset)
    if raw is None:
        return None
    cleaned = apply_background_removal(
        raw,
        BackgroundRemovalSettings(
            background_rgba=asset.background_removal.background_rgba,
            tolerance_ui=asset.background_removal.tolerance_ui,
            connected_background_only=asset.background_removal.connected_background_only,
            connectivity=asset.background_removal.connectivity,
        ),
    )
    return cleaned.cleaned_image


def _final_edited_for_asset(project: SpriteProject, asset: AssetRecord) -> Image.Image | None:
    raw = _raw_crop_for_asset(project, asset)
    if raw is None:
        return None
    cleaned = _auto_clean_for_asset(project, asset)
    if cleaned is None:
        return None
    sidecar_path = Path(asset.manual_edit_sidecar) if asset.manual_edit_sidecar else None
    if sidecar_path is not None and project.path is not None and not sidecar_path.is_absolute():
        sidecar_path = project.path.parent / sidecar_path
    if sidecar_path is not None and sidecar_path.exists():
        validation = validate_manual_sidecar(
            sidecar_path,
            expected_width=raw.width,
            expected_height=raw.height,
            expected_checksum=asset.manual_edit_checksum,
            expected_source_sheet_checksum=asset.manual_edit_source_sheet_checksum,
            expected_settings_checksum=asset.manual_edit_cleanup_settings_checksum,
            actual_source_sheet_checksum=_asset_source_sheet(project, asset).checksum if _asset_source_sheet(project, asset) else None,
            actual_settings_checksum=compute_settings_checksum(asset.background_removal.to_dict()),
        )
        if validation.valid:
            return load_png(sidecar_path)
    return cleaned


def _normalized_for_asset(project: SpriteProject, asset: AssetRecord) -> Image.Image | None:
    from .project_manager import ProjectManager

    manager = ProjectManager(project)
    try:
        return manager._normalized_output_for_asset(asset).image
    except Exception:
        return None


def variant_image_for_asset(project: SpriteProject, asset: AssetRecord, variant: ExportVariant) -> Image.Image | None:
    if variant == ExportVariant.raw:
        return _raw_crop_for_asset(project, asset)
    if variant == ExportVariant.auto_clean:
        return _auto_clean_for_asset(project, asset)
    if variant == ExportVariant.final_edited:
        return _final_edited_for_asset(project, asset)
    if variant == ExportVariant.normalized:
        return _normalized_for_asset(project, asset)
    return None


def classify_asset_for_export(project: SpriteProject, asset: AssetRecord, requested_variants: Iterable[ExportVariant]) -> AssetExportClassification:
    reasons: list[str] = []
    sheet = _asset_source_sheet(project, asset)
    raw = _raw_crop_for_asset(project, asset)
    final_image = _final_edited_for_asset(project, asset)
    normalized_image = _normalized_for_asset(project, asset)

    if sheet is None or sheet.missing:
        reasons.append("missing source sheet")
    if asset.crop_rect is None:
        reasons.append("no crop rectangle")
    elif not asset.crop_rect.is_valid():
        reasons.append("empty crop")
    if raw is None:
        reasons.append("crop unavailable")
    if asset.normalization.output_width <= 0 or asset.normalization.output_height <= 0:
        reasons.append("invalid output dimensions")
    if asset.normalization.enabled and normalized_image is None and ExportVariant.normalized in requested_variants:
        reasons.append("normalized output unavailable")
    if asset.manual_edit_sidecar and final_image is None:
        reasons.append("missing required manual-edit sidecar")
    if asset.workflow_status == WorkflowStatus.reviewed and any(
        value is not None
        for value in (asset.crop_rect, asset.background_removal.background_rgba, asset.normalization.enabled)
    ):
        reasons.append("reviewed asset may be stale after edits")

    if reasons:
        return AssetExportClassification(state="blocked", reasons=reasons)

    warnings: list[str] = []
    if asset.export_info.exported_path:
        warnings.append("stale export")
    if asset.normalization.output_width < 16 or asset.normalization.output_height < 16:
        warnings.append("output smaller than recommended size")
    if asset.normalization.scale_mode == "percent" and asset.normalization.scale_percent != 100:
        warnings.append("severe downscale" if asset.normalization.scale_percent < 60 else "scaled export")
    if asset.normalization.allow_overflow:
        warnings.append("clipping allowed intentionally")
    if asset.background_removal.background_rgba is None:
        warnings.append("no background color when cleanup expected")
    if asset.normalized_export_path and asset.normalization_checksum and normalized_image is not None:
        current_checksum = image_checksum(normalized_image)
        if stale_normalized_export(asset.normalization_checksum, current_checksum):
            warnings.append("stale normalized export")

    if warnings:
        return AssetExportClassification(state="warning", reasons=warnings)
    return AssetExportClassification(state="ready", reasons=[])


def validation_issues_for_project(project: SpriteProject) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_paths: set[str] = set()
    seen_filenames: set[str] = set()
    for asset in project.assets:
        sheet = _asset_source_sheet(project, asset)
        raw = _raw_crop_for_asset(project, asset)
        cleaned = _auto_clean_for_asset(project, asset)
        final_image = _final_edited_for_asset(project, asset)
        normalized_image = _normalized_for_asset(project, asset)

        def add_issue(severity: ValidationSeverity, code: str, message: str, detail: str, suggested_fix: str, auto_fix: bool) -> None:
            issues.append(
                ValidationIssue(
                    severity=severity,
                    code=code,
                    asset_uuid=asset.asset_uuid,
                    asset_name=asset.display_name,
                    category=asset.category or "asset",
                    message=message,
                    detail=detail,
                    suggested_fix=suggested_fix,
                    auto_fix_available=auto_fix,
                )
            )

        if sheet is None or sheet.missing:
            add_issue(ValidationSeverity.blocked, "source_missing", "Missing source sheet", "The asset points to a source sheet that could not be found.", "Relink or remove the asset source sheet.", False)
        if sheet is not None and sheet.checksum and project.path is not None:
            source_path = Path(sheet.path)
            if not source_path.is_absolute():
                source_path = project.path.parent / source_path
            if source_path.exists() and checksum_for_file(source_path) != sheet.checksum:
                add_issue(ValidationSeverity.warning, "source_checksum_changed", "Source checksum changed", "The on-disk source sheet differs from the stored checksum.", "Review the crop and revalidate the project.", False)
        if asset.crop_rect is None:
            add_issue(ValidationSeverity.blocked, "crop_missing", "Missing crop rectangle", "The asset has no crop rectangle.", "Draw or assign a crop rectangle.", True)
        elif not asset.crop_rect.is_valid():
            add_issue(ValidationSeverity.blocked, "crop_empty", "Empty crop rectangle", "The crop rectangle has a zero or negative size.", "Clear or redraw the crop.", True)
        elif sheet is not None and sheet.path:
            source_path = Path(sheet.path)
            if project.path is not None and not source_path.is_absolute():
                source_path = project.path.parent / source_path
            if source_path.exists():
                try:
                    source_image = load_png(source_path)
                    if (
                        asset.crop_rect.x < 0
                        or asset.crop_rect.y < 0
                        or asset.crop_rect.x + asset.crop_rect.width > source_image.width
                        or asset.crop_rect.y + asset.crop_rect.height > source_image.height
                    ):
                        add_issue(ValidationSeverity.blocked, "crop_outside_bounds", "Crop outside source bounds", "The crop rectangle extends outside the source sheet.", "Resize or move the crop rectangle back inside the sheet.", True)
                except Exception:
                    pass

        if raw is None:
            add_issue(ValidationSeverity.blocked, "crop_unavailable", "Crop unavailable", "The crop could not be loaded from the source sheet.", "Check the crop rectangle and source sheet path.", False)
        else:
            if raw.width == 0 or raw.height == 0:
                add_issue(ValidationSeverity.blocked, "empty_crop", "Empty crop", "The crop loads but has no dimensions.", "Clear or redraw the crop.", True)
            bounds = transparent_bounds(raw)
            if bounds.is_empty:
                add_issue(ValidationSeverity.blocked, "transparent_result", "Fully transparent crop", "The cropped image has no visible pixels.", "Choose a different crop or check the source sheet.", False)
            elif bounds.content_width < raw.width or bounds.content_height < raw.height:
                add_issue(ValidationSeverity.warning, "clipping", "Possible clipping", "Visible pixels do not fill the full crop box.", "Review the crop and alignment settings.", False)
            if raw.width * raw.height <= 4:
                add_issue(ValidationSeverity.warning, "small_crop", "Very small crop", "The crop is smaller than recommended for export.", "Confirm the crop is intentional.", False)

        if cleaned is not None and cleaned.mode == "RGBA":
            alpha_values = cleaned.getchannel("A").getextrema()
            if alpha_values[0] < 255 < alpha_values[1]:
                add_issue(ValidationSeverity.warning, "semi_transparent", "Semi-transparent pixels present", "The cleaned image contains alpha values between 0 and 255.", "Inspect the cleanup and manual edits.", False)

        if final_image is not None and final_image.mode == "RGBA":
            alpha_extrema = final_image.getchannel("A").getextrema()
            if alpha_extrema == (0, 0):
                add_issue(ValidationSeverity.blocked, "final_transparent", "Final output transparent", "The final edited output is completely transparent.", "Check manual edits and cleanup settings.", False)

        if normalized_image is not None:
            if asset.normalization.output_width <= 0 or asset.normalization.output_height <= 0:
                add_issue(ValidationSeverity.blocked, "invalid_canvas_size", "Invalid normalized canvas size", "The normalized canvas has invalid dimensions.", "Set a positive output width and height.", True)
            if asset.normalization.baseline_y is not None and not (0 <= asset.normalization.baseline_y < asset.normalization.output_height):
                add_issue(ValidationSeverity.warning, "baseline_out_of_range", "Baseline out of range", "The baseline lies outside the output canvas.", "Align the baseline to the canvas.", True)
            if asset.normalization.pivot_x is not None and asset.normalization.pivot_y is not None:
                if not (0 <= asset.normalization.pivot_x < asset.normalization.output_width and 0 <= asset.normalization.pivot_y < asset.normalization.output_height):
                    add_issue(ValidationSeverity.warning, "pivot_out_of_range", "Pivot out of range", "The pivot point lies outside the output canvas.", "Bring the pivot inside the canvas.", True)
        elif asset.normalization.enabled:
            add_issue(ValidationSeverity.blocked, "normalized_unavailable", "Normalized output unavailable", "The normalized output could not be produced.", "Check crop, cleanup, and normalization settings.", False)

        if asset.alignment_group:
            group_members = [item for item in project.assets if item.alignment_group == asset.alignment_group]
            widths = {item.normalization.output_width for item in group_members}
            heights = {item.normalization.output_height for item in group_members}
            baseline_set = {item.baseline_y or item.normalization.baseline_y for item in group_members}
            pivot_set = {(item.pivot_x or item.normalization.pivot_x, item.pivot_y or item.normalization.pivot_y) for item in group_members}
            if len(widths) > 1 or len(heights) > 1:
                add_issue(ValidationSeverity.warning, "alignment_canvas_mismatch", "Inconsistent canvas size", "Assets in the same alignment group use different normalized canvas sizes.", "Align canvas size from the group leader.", True)
            if len(baseline_set) > 1:
                add_issue(ValidationSeverity.warning, "alignment_baseline_mismatch", "Inconsistent baseline", "Assets in the same alignment group use different baselines.", "Align baseline from the group leader.", True)
            if len(pivot_set) > 1:
                add_issue(ValidationSeverity.warning, "alignment_pivot_mismatch", "Inconsistent pivot", "Assets in the same alignment group use different pivots.", "Align pivot from the group leader.", True)

        if asset.raw_output_filename:
            if asset.raw_output_filename.lower() != sanitize_filename(asset.raw_output_filename).lower():
                add_issue(ValidationSeverity.warning, "filename_convention", "Filename convention violation", "The raw filename uses unsupported characters or casing.", "Normalize the filename.", True)
            if asset.raw_output_filename in seen_filenames:
                add_issue(ValidationSeverity.blocked, "duplicate_filename", "Duplicate filename", "Another asset already uses this filename.", "Rename the file to make it unique.", True)
            seen_filenames.add(asset.raw_output_filename)
        if asset.export_info.exported_path:
            if asset.export_info.exported_path in seen_paths:
                add_issue(ValidationSeverity.blocked, "duplicate_output_path", "Duplicate output path", "Two assets resolve to the same export destination.", "Change the output folder or filename template.", True)
            seen_paths.add(asset.export_info.exported_path)
            if Path(asset.export_info.exported_path).suffix.lower() != ".png":
                add_issue(ValidationSeverity.warning, "unexpected_extension", "Unexpected extension", "The exported file is not a PNG.", "Export as PNG.", True)
        else:
            add_issue(ValidationSeverity.warning, "missing_export", "Missing export", "The asset has not been exported yet.", "Export the asset or mark it intentionally skipped.", True)

        if asset.workflow_status == WorkflowStatus.reviewed and asset.modified_at and asset.export_info.exported_at and asset.modified_at > asset.export_info.exported_at:
            add_issue(ValidationSeverity.warning, "reviewed_after_edits", "Reviewed asset edited later", "The asset was reviewed and then modified afterwards.", "Re-review or re-export the asset.", False)

        if asset.manual_edit_sidecar and asset.manual_edit_width and asset.manual_edit_height and raw is not None:
            manual = validate_manual_sidecar(
                Path(asset.manual_edit_sidecar),
                expected_width=asset.manual_edit_width,
                expected_height=asset.manual_edit_height,
                expected_checksum=asset.manual_edit_checksum,
                expected_source_sheet_checksum=asset.manual_edit_source_sheet_checksum,
                expected_settings_checksum=asset.manual_edit_cleanup_settings_checksum,
                actual_source_sheet_checksum=sheet.checksum if sheet else None,
                actual_settings_checksum=None,
            )
            if not manual.valid:
                add_issue(ValidationSeverity.warning, "manual_edit_checksum_warning", "Manual edit checksum warning", "The stored manual edit sidecar no longer matches its checksums.", "Inspect the manual edit file.", False)

        if asset.normalized_export_path and asset.normalized_exported_at and asset.normalization_checksum and normalized_image is not None:
            current = image_checksum(normalized_image)
            if stale_normalized_export(asset.normalization_checksum, current):
                add_issue(ValidationSeverity.warning, "stale_normalized_export", "Stale normalized export", "The stored normalized checksum does not match the current output.", "Refresh the normalized export metadata.", True)

        if asset.crop_rect and asset.workflow_status == WorkflowStatus.reviewed and any(
            item.proposal_uuid == asset.asset_uuid for sheet_item in project.source_sheets for item in sheet_item.crop_proposals if item.assigned_asset_uuid == asset.asset_uuid
        ):
            add_issue(ValidationSeverity.warning, "proposal_assignment_mismatch", "Proposal assignment mismatch", "A proposal points to this asset but the crop may have changed.", "Reassign the proposal or re-export the asset.", False)

    return issues


def _export_variant_image(project: SpriteProject, asset: AssetRecord, variant: ExportVariant) -> Image.Image | None:
    from .project_manager import ProjectManager

    manager = ProjectManager(project)
    if variant == ExportVariant.raw:
        return _raw_crop_for_asset(project, asset)
    if variant == ExportVariant.auto_clean:
        return _auto_clean_for_asset(project, asset)
    if variant == ExportVariant.final_edited:
        return _final_edited_for_asset(project, asset)
    if variant == ExportVariant.normalized:
        return _normalized_for_asset(project, asset)
    return None


def _file_state(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "missing", "missing"
    return "exists", checksum_for_file(path)


def _existing_checksum_matches(path: Path, image: Image.Image) -> bool:
    return path.exists() and checksum_for_file(path) == image_checksum(image)


def build_export_preview(project: SpriteProject, settings: BatchExportSettings) -> list[ExportPreviewEntry]:
    entries: list[ExportPreviewEntry] = []
    for asset in select_assets(project, settings):
        classification = classify_asset_for_export(project, asset, settings.variants)
        for variant in settings.variants:
            image = variant_image_for_asset(project, asset, variant)
            if image is None:
                validation_state = "blocked" if classification.state == "blocked" else "warning"
            else:
                validation_state = classification.state
            if variant == ExportVariant.normalized:
                width, height = asset.normalization.output_width, asset.normalization.output_height
            else:
                width, height = image.size if image is not None else (0, 0)
            root = Path(settings.output_root or project.project.defaults.output_folder or (project.path.parent / "output" if project.path else Path.cwd() / "output"))
            directory = resolve_output_directory(root, asset, variant, settings)
            filename = resolve_output_filename(asset, variant, settings, width, height)
            destination = directory / filename
            existing_state, checksum = _file_state(destination)
            if settings.overwrite_policy == OverwritePolicy.skip_existing and existing_state == "exists":
                overwrite_action = "skip"
            elif settings.overwrite_policy == OverwritePolicy.overwrite_existing:
                overwrite_action = "overwrite"
            elif settings.overwrite_policy == OverwritePolicy.rename_with_suffix and existing_state == "exists":
                overwrite_action = "rename"
            elif settings.overwrite_policy == OverwritePolicy.compare_checksum_skip_identical and existing_state == "exists" and image is not None and _existing_checksum_matches(destination, image):
                overwrite_action = "skip_identical"
            else:
                overwrite_action = "create" if existing_state == "missing" else "ask"
            entries.append(
                ExportPreviewEntry(
                    asset_uuid=asset.asset_uuid,
                    asset_name=asset.display_name,
                    variant=variant,
                    destination_path=str(destination),
                    output_width=width,
                    output_height=height,
                    validation_state=validation_state,
                    overwrite_action=overwrite_action,
                    existing_file_state=existing_state,
                    checksum_compare_result="identical" if existing_state == "exists" and image is not None and _existing_checksum_matches(destination, image) else "different" if existing_state == "exists" else "missing",
                    estimated_file_count=1,
                    reasons=classification.reasons,
                )
            )
    return entries


def _manifest_summary(entries: list[ExportPreviewEntry]) -> dict[str, Any]:
    return {
        "assets_selected": len({entry.asset_uuid for entry in entries}),
        "files_to_create": sum(1 for entry in entries if entry.overwrite_action in {"create", "ask", "rename"}),
        "files_to_overwrite": sum(1 for entry in entries if entry.overwrite_action == "overwrite"),
        "files_to_skip": sum(1 for entry in entries if entry.overwrite_action.startswith("skip")),
        "blocked": sum(1 for entry in entries if entry.validation_state == "blocked"),
        "warnings": sum(1 for entry in entries if entry.validation_state == "warning"),
    }


def export_report_csv(issues: list[ValidationIssue], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(issues[0].to_dict().keys()) if issues else ["severity", "code"])
        writer.writeheader()
        for issue in issues:
            writer.writerow(issue.to_dict())
    return destination


def export_report_json(issues: list[ValidationIssue], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps([issue.to_dict() for issue in issues], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def export_report_html(issues: list[ValidationIssue], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f"<tr><td>{issue.severity.value}</td><td>{issue.asset_name}</td><td>{issue.category}</td><td>{issue.code}</td><td>{issue.message}</td><td>{issue.suggested_fix}</td></tr>"
        for issue in issues
    )
    destination.write_text(
        f"<html><body><table><thead><tr><th>Severity</th><th>Asset</th><th>Category</th><th>Code</th><th>Message</th><th>Suggested Fix</th></tr></thead><tbody>{rows}</tbody></table></body></html>",
        encoding="utf-8",
    )
    return destination


def export_manifest_json(manifest: BatchExportManifest, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def export_manifest_csv(entries: list[ExportPreviewEntry], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(entries[0].to_dict().keys()) if entries else [])
        if entries:
            writer.writeheader()
            for entry in entries:
                writer.writerow(entry.to_dict())
    return destination


def export_manifest_html(manifest: BatchExportManifest, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f"<tr><td>{entry.asset_name}</td><td>{entry.variant.value}</td><td>{entry.destination_path}</td><td>{entry.validation_state}</td><td>{entry.overwrite_action}</td></tr>"
        for entry in manifest.entries
    )
    destination.write_text(
        f"<html><body><h1>{manifest.project_name}</h1><p>{manifest.created_at}</p><table><thead><tr><th>Asset</th><th>Variant</th><th>Path</th><th>Validation</th><th>Overwrite</th></tr></thead><tbody>{rows}</tbody></table></body></html>",
        encoding="utf-8",
    )
    return destination


def batch_export_state_path(output_root: str | Path) -> Path:
    return Path(output_root) / ".batch_export_state.json"


def load_export_job_state(output_root: str | Path) -> ExportJobState | None:
    path = batch_export_state_path(output_root)
    if not path.exists():
        return None
    return ExportJobState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_export_job_state(output_root: str | Path, state: ExportJobState) -> Path:
    path = batch_export_state_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_batch_export(
    project: SpriteProject,
    settings: BatchExportSettings,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[BatchExportManifest, ExportJobState]:
    root = Path(settings.output_root or project.project.defaults.output_folder or (project.path.parent / "output" if project.path else Path.cwd() / "output"))
    root.mkdir(parents=True, exist_ok=True)
    entries = build_export_preview(project, settings)
    summary = _manifest_summary(entries)
    job_state = load_export_job_state(root) if settings.create_resume_state else None
    if job_state is None or job_state.settings_checksum != _stable_checksum(settings.to_dict()):
        job_state = ExportJobState(job_id=sha256(utc_now_iso().encode("utf-8")).hexdigest()[:12], created_at=utc_now_iso(), settings_checksum=_stable_checksum(settings.to_dict()))

    for entry in entries:
        if cancel_requested is not None and cancel_requested():
            break
        job_key = f"{entry.asset_uuid}:{entry.variant.value}:{entry.destination_path}"
        if job_key in job_state.completed_keys:
            continue
        asset = next(asset for asset in project.assets if asset.asset_uuid == entry.asset_uuid)
        image = variant_image_for_asset(project, asset, entry.variant)
        if image is None:
            job_state.failures.append({"asset_uuid": asset.asset_uuid, "variant": entry.variant.value, "reason": "unavailable"})
            continue
        destination = Path(entry.destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing = destination.exists()
        if existing and settings.overwrite_policy == OverwritePolicy.skip_existing:
            job_state.completed_keys.append(job_key)
            continue
        if existing and settings.overwrite_policy == OverwritePolicy.compare_checksum_skip_identical and _existing_checksum_matches(destination, image):
            job_state.completed_keys.append(job_key)
            continue
        if existing and settings.overwrite_policy == OverwritePolicy.rename_with_suffix:
            stem = destination.stem
            suffix = destination.suffix or ".png"
            index = 2
            while True:
                candidate = destination.parent / f"{stem}_{index:02d}{suffix}"
                if not candidate.exists():
                    destination = candidate
                    break
                index += 1
        export_png(image, destination)
        job_state.completed_keys.append(job_key)
        job_state.exported_files.append(str(destination))
        save_export_job_state(root, job_state)

    manifest = BatchExportManifest(
        created_at=utc_now_iso(),
        project_name=project.project.project_name,
        output_root=str(root),
        settings=settings.to_dict(),
        entries=entries,
        summary=summary,
    )
    if settings.generate_manifest:
        export_manifest_json(manifest, root / "batch_export_manifest.json")
        if settings.generate_csv_manifest:
            export_manifest_csv(entries, root / "batch_export_manifest.csv")
        if settings.generate_html_manifest:
            export_manifest_html(manifest, root / "batch_export_manifest.html")
    return manifest, job_state
