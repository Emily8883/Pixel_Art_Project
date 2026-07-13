from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from pixel_asset_extractor.project_manager import ProjectManager
from pixel_asset_extractor.models import CropRect
from pixel_asset_extractor.ui.normalization_panel import CompareAlignmentDialog, NormalizationInspectorWidget, NormalizationReportDialog


def make_png(path, color=(255, 0, 0, 255), size=(4, 4)):
    Image.new("RGBA", size, color).save(path)
    return path


def make_manager(tmp_path):
    manager = ProjectManager()
    sheet_path = make_png(tmp_path / "sheet.png")
    sheet = manager.add_source_sheet(sheet_path)
    asset = manager.add_asset(
        "asset",
        source_sheet_id=sheet.source_sheet_id,
        source_sheet_path=str(sheet_path),
        character_group="freya",
        category="idle",
        action="idle",
        direction="front",
    )
    asset.crop_rect = CropRect(0, 0, 4, 4)
    return manager, asset


def test_inspector_controls_load_active_asset_settings(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    asset.normalization.output_width = 64
    asset.normalization.output_height = 64
    asset.normalization.scale_mode = "exact_dimensions"
    widget = NormalizationInspectorWidget()

    widget.set_context(manager, asset, tmp_path / "project.json")

    assert widget.width_spin.value() == 64
    assert widget.height_spin.value() == 64
    assert widget.scale_mode_combo.currentText() == "exact_dimensions"


def test_inspector_changes_update_active_asset(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    widget = NormalizationInspectorWidget()
    widget.set_context(manager, asset, tmp_path / "project.json")
    widget.width_spin.setValue(96)
    widget.height_spin.setValue(96)
    widget.scale_mode_combo.setCurrentText("exact_dimensions")
    widget._apply_to_asset()

    assert asset.normalization.output_width == 96
    assert asset.normalization.output_height == 96
    assert asset.normalization.scale_mode == "exact_dimensions"


def test_preset_size_updates_dimensions(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    widget = NormalizationInspectorWidget()
    widget.set_context(manager, asset, tmp_path / "project.json")
    widget.preset_combo.setCurrentText("64x64")

    assert widget.width_spin.value() == 64
    assert widget.height_spin.value() == 64


def test_scale_mode_field_enabling(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    widget = NormalizationInspectorWidget()
    widget.set_context(manager, asset, tmp_path / "project.json")
    widget.scale_mode_combo.setCurrentText("percent")
    widget._update_field_state()

    assert widget.scale_percent_spin.isEnabled()
    assert not widget.target_width_spin.isEnabled()
    assert not widget.target_height_spin.isEnabled()


def test_live_normalized_preview_refresh(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    widget = NormalizationInspectorWidget()
    widget.set_context(manager, asset, tmp_path / "project.json")
    widget.refresh_preview()

    assert widget.current_result() is not None
    assert widget.preview.zoom_percent() == 100


def test_arrow_key_movement_updates_integer_offsets(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    widget = NormalizationInspectorWidget()
    widget.set_context(manager, asset, tmp_path / "project.json")
    widget.refresh_preview()
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    widget.preview.keyPressEvent(event)

    assert widget.offset_x_spin.value() == 1


def test_shift_arrow_movement_updates_integer_offsets(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    widget = NormalizationInspectorWidget()
    widget.set_context(manager, asset, tmp_path / "project.json")
    widget.refresh_preview()
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    widget.preview.keyPressEvent(event)

    assert widget.offset_x_spin.value() == 5


def test_contact_point_set_mode(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    widget = NormalizationInspectorWidget()
    widget.set_context(manager, asset, tmp_path / "project.json")
    widget.preview.enable_contact_mode(True)
    widget.preview.contactChanged.emit(2, 3)

    assert widget.contact_x_spin.value() == 2
    assert widget.contact_y_spin.value() == 3


def test_pivot_point_set_mode(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    widget = NormalizationInspectorWidget()
    widget.set_context(manager, asset, tmp_path / "project.json")
    widget.preview.enable_pivot_mode(True)
    widget.preview.pivotChanged.emit(4, 5)

    assert widget.pivot_x_spin.value() == 4
    assert widget.pivot_y_spin.value() == 5


def test_compare_dialog_and_report_dialog_smoke(qapp, tmp_path):
    manager, asset = make_manager(tmp_path)
    compare = CompareAlignmentDialog([asset])
    report = NormalizationReportDialog(manager.asset_normalization_report())

    assert compare.windowTitle() == "Compare Alignment"
    assert report.windowTitle() == "Normalization Report"
