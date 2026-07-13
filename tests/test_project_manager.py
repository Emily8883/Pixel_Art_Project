from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from pixel_asset_extractor.autosave import autosave_path, has_newer_autosave
from pixel_asset_extractor.naming import generate_filename
from pixel_asset_extractor.project_manager import ProjectManager, project_trash_path
from pixel_asset_extractor.project_model import ActivityEntry, BackgroundRemovalSettingsModel, ProjectRecord, SourceSheet, SpriteProject, WorkflowStatus
from pixel_asset_extractor.project_store import checksum_file, detect_newer_autosave, load_project, save_project
from pixel_asset_extractor.templates import FREYA_MOVEMENT_TEMPLATE


def make_png(path: Path, color=(0, 0, 0, 255), size=(8, 8)) -> Path:
    image = Image.new("RGBA", size, color)
    image.save(path)
    return path


def make_project_with_sheet(tmp_path: Path) -> tuple[ProjectManager, Path, Path]:
    sheet_path = make_png(tmp_path / "sheet.png", (50, 60, 70, 255), (16, 16))
    manager = ProjectManager()
    sheet = manager.add_source_sheet(sheet_path)
    asset = manager.add_asset(
        display_name="freya_walk_front_01",
        source_sheet_id=sheet.source_sheet_id,
        source_sheet_path=str(sheet_path),
        character_group="freya",
        category="walk",
        action="walk",
        direction="front",
        frame_number=1,
    )
    asset.crop_rect = __import__("pixel_asset_extractor.models", fromlist=["CropRect"]).CropRect(1, 1, 4, 4)
    asset.background_removal = BackgroundRemovalSettingsModel(background_rgba=(50, 60, 70, 255), tolerance_ui=5)
    manager.project.mark_modified()
    return manager, sheet_path, tmp_path


def test_version3_project_round_trip(tmp_path):
    manager, sheet_path, _ = make_project_with_sheet(tmp_path)
    project_path = tmp_path / "freya_project.json"

    save_project(manager.project, project_path)
    loaded = load_project(project_path)

    assert loaded.project.project_name == "Untitled Project"
    assert loaded.source_sheets[0].path == str(sheet_path)
    assert loaded.assets[0].display_name == "freya_walk_front_01"
    assert loaded.assets[0].crop_rect is not None
    assert loaded.assets[0].background_removal.background_rgba == (50, 60, 70, 255)


def test_version1_migration(tmp_path):
    legacy = {
        "version": 1,
        "source_image": str(make_png(tmp_path / "legacy.png")),
        "x": 2,
        "y": 3,
        "width": 4,
        "height": 5,
        "legacy_field": "keep-me",
    }
    path = tmp_path / "legacy_v1.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    project = load_project(path)

    assert len(project.assets) == 1
    assert len(project.source_sheets) == 1
    assert project.assets[0].crop_rect.width == 4
    assert project.legacy_fields["legacy_extras"]["legacy_field"] == "keep-me"


def test_version2_migration(tmp_path):
    from pixel_asset_extractor.config_store import save_config
    from pixel_asset_extractor.models import CropConfig, CropRect

    source = make_png(tmp_path / "legacy_v2.png")
    legacy_config = CropConfig(
        source_image=str(source),
        crop_rect=CropRect(1, 2, 3, 4),
        background_rgba=(9, 8, 7, 255),
        output_raw_filename="raw.png",
        output_clean_filename="clean.png",
        export_directory=str(tmp_path / "out"),
        extras={"custom": "field"},
    )
    path = tmp_path / "legacy_v2.json"
    save_config(legacy_config, path)

    project = load_project(path)

    assert project.assets[0].background_removal.background_rgba == (9, 8, 7, 255)
    assert project.legacy_fields["legacy_extras"]["custom"] == "field"


def test_stable_uuid_creation(tmp_path):
    manager = ProjectManager()
    first = manager.add_asset("one")
    second = manager.add_asset("two")

    assert first.asset_uuid
    assert second.asset_uuid
    assert first.asset_uuid != second.asset_uuid


def test_asset_creation_defaults(tmp_path):
    manager = ProjectManager()
    asset = manager.add_asset(
        display_name="Freya Idle Front",
        character_group="Freya",
        category="idle",
        action="idle",
        direction="front",
    )

    assert asset.workflow_status == WorkflowStatus.planned
    assert asset.raw_output_filename == "freya_idle_front.png"
    assert asset.clean_output_filename == "freya_idle_front_clean.png"


def test_duplicate_asset_increments_frame(tmp_path):
    manager = ProjectManager()
    a1 = manager.add_asset("freya_walk_front_01", character_group="freya", category="walk", action="walk", direction="front", frame_number=1)
    manager.add_asset("freya_walk_front_02", character_group="freya", category="walk", action="walk", direction="front", frame_number=2)

    dup = manager.duplicate_asset(a1.asset_uuid)

    assert dup.frame_number == 3
    assert dup.display_name == "freya_walk_front_03"


def test_duplicate_asset_skips_existing_frames(tmp_path):
    manager = ProjectManager()
    a1 = manager.add_asset("freya_walk_front_01", character_group="freya", category="walk", action="walk", direction="front", frame_number=1)
    manager.add_asset("freya_walk_front_02", character_group="freya", category="walk", action="walk", direction="front", frame_number=2)
    manager.add_asset("freya_walk_front_03", character_group="freya", category="walk", action="walk", direction="front", frame_number=3)

    dup = manager.duplicate_asset(a1.asset_uuid)

    assert dup.frame_number == 4


def test_delete_asset_does_not_delete_exported_files_by_default(tmp_path):
    manager = ProjectManager()
    asset = manager.add_asset("freya_idle_front")
    exported = make_png(tmp_path / "exported.png")
    asset.export_info.exported_path = str(exported)

    manager.delete_asset(asset.asset_uuid, move_exports_to_trash=False)

    assert exported.exists()


def test_project_trash_path_generation(tmp_path):
    trash = project_trash_path(tmp_path / "freya_project.json", "2026-07-13T12:34:56")

    assert str(trash).endswith(r".trash\2026-07-13T12-34-56")


def test_status_transitions(tmp_path):
    manager = ProjectManager()
    sheet = manager.add_source_sheet(make_png(tmp_path / "sheet.png"))
    asset = manager.add_asset("freya_walk_front_01", sheet.source_sheet_id, str(sheet.path), "freya", "walk", "walk", "front", 1)

    assert asset.workflow_status == WorkflowStatus.planned
    asset.crop_rect = __import__("pixel_asset_extractor.models", fromlist=["CropRect"]).CropRect(0, 0, 4, 4)
    manager.apply_crop_to_active_asset(asset.crop_rect)
    assert manager.active_asset.workflow_status == WorkflowStatus.cropped
    manager.apply_background_settings_to_active_asset((50, 60, 70, 255), 5, True, 4)
    assert manager.active_asset.workflow_status == WorkflowStatus.cleaned
    manager.mark_status(asset.asset_uuid, WorkflowStatus.reviewed)
    assert manager.active_asset.workflow_status == WorkflowStatus.reviewed


def test_reviewed_asset_edit_behavior(tmp_path):
    manager = ProjectManager()
    asset = manager.add_asset("freya_idle_front")
    manager.mark_status(asset.asset_uuid, WorkflowStatus.reviewed)
    manager.edit_asset(asset.asset_uuid, notes="edited")

    assert manager.get_asset(asset.asset_uuid).workflow_status == WorkflowStatus.needs_revision


def test_project_progress_counts(tmp_path):
    manager = ProjectManager()
    a1 = manager.add_asset("a1")
    a2 = manager.add_asset("a2")
    manager.mark_status(a1.asset_uuid, WorkflowStatus.exported)
    manager.mark_status(a2.asset_uuid, WorkflowStatus.cleaned)

    counts = manager.project_progress_counts()
    assert counts["total"] == 2
    assert counts["exported_count"] == 1
    assert counts["cleaned"] == 1


def test_freya_template_creates_correct_records(tmp_path):
    manager = ProjectManager()
    sheet = manager.add_source_sheet(make_png(tmp_path / "freya.png"))
    created, skipped = manager.create_freya_movement_template(sheet.source_sheet_id, sheet.path)

    assert len(created) == len(FREYA_MOVEMENT_TEMPLATE)
    assert not skipped
    assert any(asset.display_name == "freya_walk_front_04" for asset in manager.project.assets)


def test_freya_template_is_idempotent(tmp_path):
    manager = ProjectManager()
    sheet = manager.add_source_sheet(make_png(tmp_path / "freya.png"))
    created1, skipped1 = manager.create_freya_movement_template(sheet.source_sheet_id, sheet.path)
    created2, skipped2 = manager.create_freya_movement_template(sheet.source_sheet_id, sheet.path)

    assert len(created1) > 0
    assert len(created2) == 0
    assert len(skipped2) == len(FREYA_MOVEMENT_TEMPLATE)


def test_relative_path_serialization(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    sheet = make_png(project_dir / "sheet.png")
    manager = ProjectManager()
    source = manager.add_source_sheet(sheet)
    manager.add_asset("asset", source.source_sheet_id, str(sheet), "freya", "idle", "idle", "front")
    project_path = project_dir / "freya_project.json"
    save_project(manager.project, project_path)
    payload = json.loads(project_path.read_text(encoding="utf-8"))

    assert payload["source_sheets"][0]["path"] == "sheet.png"


def test_missing_source_sheet_handling(tmp_path):
    manager = ProjectManager()
    sheet = manager.add_source_sheet(tmp_path / "missing.png")
    missing = manager.detect_missing_sources()

    assert sheet in missing
    assert sheet.missing is True


def test_source_sheet_checksum_calculation(tmp_path):
    sheet = make_png(tmp_path / "checksum.png", (1, 2, 3, 255))
    checksum = checksum_file(sheet)

    assert len(checksum) == 64


def test_changed_source_warning_condition(tmp_path):
    sheet_path = make_png(tmp_path / "sheet.png", (1, 2, 3, 255))
    manager = ProjectManager()
    sheet = manager.add_source_sheet(sheet_path)
    sheet.checksum = "0" * 64

    assert manager.source_sheet_checksum_changed(sheet) is True


def test_atomic_project_save_and_backup_creation(tmp_path):
    manager = ProjectManager()
    manager.add_asset("asset")
    path = tmp_path / "project.json"
    save_project(manager.project, path)
    path.write_text(path.read_text(encoding="utf-8").replace("asset", "asset2"), encoding="utf-8")
    save_project(manager.project, path)

    assert path.exists()
    assert path.with_suffix(".json.bak").exists()


def test_autosave_newer_detection(tmp_path):
    manager = ProjectManager()
    manager.add_asset("asset")
    path = tmp_path / "project.json"
    save_project(manager.project, path)
    time.sleep(1.1)
    autosave = autosave_path(path)
    save_project(manager.project, autosave)

    assert has_newer_autosave(path) is True
    assert detect_newer_autosave(path) == autosave


def test_activity_log_maximum_length(tmp_path):
    manager = ProjectManager()
    for index in range(1105):
        manager.project.log("event", f"message {index}")

    assert len(manager.project.activity_log) == 1000


def test_legacy_config_fields_not_silently_discarded(tmp_path):
    from pixel_asset_extractor.config_store import load_config

    legacy = {
        "version": 1,
        "source_image": str(make_png(tmp_path / "legacy.png")),
        "x": 1,
        "y": 2,
        "width": 3,
        "height": 4,
        "unknown_field": "preserve-me",
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    config = load_config(path)

    assert config.extras["unknown_field"] == "preserve-me"


def test_export_updates_asset_metadata_correctly(tmp_path):
    manager = ProjectManager()
    sheet = manager.add_source_sheet(make_png(tmp_path / "sheet.png"))
    asset = manager.add_asset("asset", sheet.source_sheet_id, str(sheet.path), "freya", "idle", "idle", "front", 1)
    asset.crop_rect = __import__("pixel_asset_extractor.models", fromlist=["CropRect"]).CropRect(0, 0, 4, 4)
    raw = Image.new("RGBA", (4, 4), (1, 2, 3, 255))
    clean = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    manager.export_active_asset(raw, clean, "clean", tmp_path / "clean.png")

    assert asset.export_info.exported_path.endswith("clean.png")
    assert asset.workflow_status == WorkflowStatus.exported
    assert asset.export_info.exported_at is not None
