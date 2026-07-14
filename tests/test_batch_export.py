from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from pixel_asset_extractor.batch_export import ExportScope, ExportVariant, OverwritePolicy, export_report_csv, export_report_html, export_report_json
from pixel_asset_extractor.batch_export import BatchExportSettings
from pixel_asset_extractor.project_manager import ProjectManager
from pixel_asset_extractor.project_store import save_project
from pixel_asset_extractor.ui.batch_export_dialog import BatchExportDialog, ProjectValidationDialog


def make_png(path: Path) -> Path:
    image = Image.new("RGBA", (48, 48), (128, 128, 128, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 24, 24), fill=(220, 30, 30, 255))
    image.save(path)
    return path


def make_manager(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager()
    sheet_path = make_png(tmp_path / "sheet.png")
    sheet = manager.add_source_sheet(sheet_path)
    asset = manager.add_asset(
        "freya_idle_front_01",
        source_sheet_id=sheet.source_sheet_id,
        source_sheet_path=str(sheet_path),
        character_group="freya",
        category="idle",
        action="idle",
        direction="front",
        frame_number=1,
    )
    asset.crop_rect = __import__("pixel_asset_extractor.models", fromlist=["CropRect"]).CropRect(0, 0, 32, 32)
    manager.project.path = tmp_path / "project.json"
    save_project(manager.project, manager.project.path)
    return manager


def test_validation_report_collects_issues(tmp_path):
    manager = make_manager(tmp_path)
    asset = manager.project.assets[0]
    asset.raw_output_filename = "bad name?.png"
    asset.crop_rect = None

    issues = manager.validate_project()

    codes = {issue.code for issue in issues}
    assert "crop_missing" in codes
    assert "filename_convention" in codes


def test_batch_export_preview_and_resume_state(tmp_path):
    manager = make_manager(tmp_path)
    settings = BatchExportSettings(
        scope=ExportScope.all_assets,
        variants=[ExportVariant.raw],
        output_root=str(tmp_path / "exports"),
        overwrite_policy=OverwritePolicy.compare_checksum_skip_identical,
    )

    preview = manager.batch_export_preview(settings)
    manifest, state = manager.run_batch_export(settings)

    assert preview
    assert (tmp_path / "exports" / ".batch_export_state.json").exists()
    assert manifest.entries[0].variant == ExportVariant.raw
    assert state.completed_keys


def test_batch_export_creates_files_and_manifest(tmp_path):
    manager = make_manager(tmp_path)
    settings = BatchExportSettings(
        scope=ExportScope.all_assets,
        variants=[ExportVariant.raw, ExportVariant.normalized],
        output_root=str(tmp_path / "exports"),
        include_raw_reference_crop=True,
        generate_manifest=True,
    )

    manifest, _state = manager.run_batch_export(settings)

    assert any(Path(entry.destination_path).exists() for entry in manifest.entries)
    assert (tmp_path / "exports" / "batch_export_manifest.json").exists()
    assert (tmp_path / "exports" / "batch_export_manifest.csv").exists()
    assert (tmp_path / "exports" / "batch_export_manifest.html").exists()


def test_validation_report_exports(tmp_path):
    manager = make_manager(tmp_path)
    issues = manager.validate_project()
    csv_path = export_report_csv(issues, tmp_path / "validation.csv")
    json_path = export_report_json(issues, tmp_path / "validation.json")
    html_path = export_report_html(issues, tmp_path / "validation.html")

    assert csv_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))
    assert html_path.exists()


def test_batch_export_dialog_smoke(qapp, tmp_path):
    manager = make_manager(tmp_path)
    dialog = BatchExportDialog(manager)
    validation = ProjectValidationDialog(manager)

    assert dialog.windowTitle() == "Batch Export"
    assert validation.windowTitle() == "Validate Project"
