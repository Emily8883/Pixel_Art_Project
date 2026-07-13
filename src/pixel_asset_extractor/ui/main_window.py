from __future__ import annotations

import os
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QSlider,
    QSpinBox,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..autosave import autosave_path, has_newer_autosave, recover_autosave, save_autosave
from ..config_store import load_config as load_legacy_config
from ..exceptions import ConfigError, CropError, ImageLoadError
from ..image_tools import analyze_image, crop_image, load_png, pil_image_to_qpixmap
from ..manual_editing import ManualEditDocument, compute_settings_checksum
from ..manual_storage import load_sidecar_png, manual_edit_sidecar_path, validate_manual_sidecar
from ..naming import format_frame_number, generate_filename, generate_normalized_filename
from ..processing import (
    BackgroundRemovalResult,
    BackgroundRemovalSettings,
    apply_background_removal,
    clamp_int,
    export_png,
    format_hex,
    format_rgb,
    format_rgba,
    removal_warning_messages,
    ui_tolerance_to_distance,
)
from ..project_manager import ProjectManager
from ..project_model import ActivityEntry, AssetRecord, BackgroundRemovalSettingsModel, SourceSheet, WorkflowStatus, utc_now_iso
from ..project_store import build_project_from_legacy_config, load_project as load_sprite_project, save_project as save_sprite_project
from .canvas_view import ImageCanvasView
from .dialogs import ActivityLogDialog, AssetDialog
from .manual_cleanup_widget import ManualCleanupWidget
from .normalization_panel import CompareAlignmentDialog, NormalizationInspectorWidget, NormalizationReportDialog
from .preview_view import CropPreviewView, PickedColor


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pixel Asset Extractor")
        self.resize(1900, 1100)

        self.project_manager = ProjectManager()
        self.project_path: Path | None = None
        self._loaded_images: dict[str, Image.Image] = {}
        self._source_sheet_lookup: dict[str, SourceSheet] = {}
        self._active_sheet_id: str | None = None
        self._manual_documents: dict[str, ManualEditDocument] = {}
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._refresh_preview)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._maybe_autosave)

        self.canvas = ImageCanvasView()
        self.preview_view = CropPreviewView()
        self.preview_view.set_zoom_percent(100)
        self.manual_cleanup_widget = ManualCleanupWidget()
        self.normalization_widget = NormalizationInspectorWidget()
        self.normalization_widget.changed.connect(self._schedule_preview_refresh)
        self.normalization_widget.compareRequested.connect(self._open_compare_alignment)
        self.normalization_widget.exportRequested.connect(self.export_normalized)

        self.project_name_label = QLabel("Untitled Project")
        self.source_sheet_combo = QComboBox()
        self.source_sheet_combo.currentIndexChanged.connect(self._on_source_sheet_selected)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search assets")
        self.search_edit.textChanged.connect(self._refresh_asset_list)
        self.group_filter = QComboBox()
        self.group_filter.addItem("All groups")
        self.group_filter.currentTextChanged.connect(self._refresh_asset_list)
        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories")
        self.category_filter.currentTextChanged.connect(self._refresh_asset_list)
        self.direction_filter = QComboBox()
        self.direction_filter.addItem("All directions")
        self.direction_filter.currentTextChanged.connect(self._refresh_asset_list)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses")
        self.status_filter.currentTextChanged.connect(self._refresh_asset_list)

        self.asset_list = QListWidget()
        self.asset_list.itemSelectionChanged.connect(self._on_asset_selection_changed)
        self.asset_list.installEventFilter(self)

        self.progress_label = QLabel("Project Progress: 0 / 0 exported")
        self.group_progress_label = QLabel("Group Progress: 0 / 0 exported")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)

        self.preview_mode_combo = QComboBox()
        self.preview_mode_combo.addItems(["Before", "After", "Split"])
        self.preview_mode_combo.currentIndexChanged.connect(self._on_preview_mode_changed)
        self.preview_zoom_spin = QSpinBox()
        self.preview_zoom_spin.setRange(25, 3200)
        self.preview_zoom_spin.setSuffix("%")
        self.preview_zoom_spin.setValue(100)
        self.preview_zoom_spin.valueChanged.connect(self.preview_view.set_zoom_percent)
        self.preview_view.zoomChanged.connect(self._sync_preview_zoom)

        self.background_style_combo = QComboBox()
        self.background_style_combo.addItems(["checkerboard", "white", "black", "bright red", "bright green"])
        self.background_style_combo.currentTextChanged.connect(self._schedule_preview_refresh)
        self.checkerboard_combo = QComboBox()
        self.checkerboard_combo.addItems(["small", "medium", "large"])
        self.checkerboard_combo.currentTextChanged.connect(self._schedule_preview_refresh)

        self.pick_background_button = QPushButton("Pick Background Color")
        self.pick_background_button.clicked.connect(self._start_eyedropper)
        self.reset_background_button = QPushButton("Reset Background Removal")
        self.reset_background_button.clicked.connect(self._reset_background_removal)
        self.regenerate_filename_button = QPushButton("Regenerate Filename")
        self.regenerate_filename_button.clicked.connect(self._regenerate_filename)
        self.export_normalized_button = QPushButton("Export Normalized")
        self.export_normalized_button.clicked.connect(self.export_normalized)

        self.tolerance_slider = QSlider(Qt.Orientation.Horizontal)
        self.tolerance_slider.setRange(0, 100)
        self.tolerance_slider.setValue(5)
        self.tolerance_slider.valueChanged.connect(self._on_tolerance_changed)
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(0, 100)
        self.tolerance_spin.setValue(5)
        self.tolerance_spin.valueChanged.connect(self._on_tolerance_changed)
        self.connected_only_checkbox = QCheckBox("Remove connected background only")
        self.connected_only_checkbox.setChecked(True)
        self.connected_only_checkbox.toggled.connect(self._schedule_preview_refresh)
        self.connectivity_checkbox = QCheckBox("Use 8-way connectivity")
        self.connectivity_checkbox.setChecked(False)
        self.connectivity_checkbox.toggled.connect(self._schedule_preview_refresh)

        self.background_swatch = QLabel()
        self.background_swatch.setFixedSize(48, 24)
        self.background_swatch.setStyleSheet("background: transparent; border: 1px solid #666;")
        self.rgb_label = QLabel("RGB: not selected")
        self.rgba_label = QLabel("RGBA: not selected")
        self.hex_label = QLabel("Hex: not selected")
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #c0392b; font-weight: 600;")

        self.crop_status_label = QLabel("Crop rectangle: none")
        self.dimensions_label = QLabel("Raw crop dimensions: none")
        self.removed_pixels_label = QLabel("Removed pixels: 0")
        self.removed_percentage_label = QLabel("Removed: 0.0%")
        self.export_mode_label = QLabel("Export mode: raw + clean")
        self.zoom_status_label = QLabel("Current zoom: 100%")
        self.source_sheet_label = QLabel("No source sheets loaded")
        self.source_image_info_label = QLabel("Source sheet info unavailable.")
        self.source_image_info_label.setWordWrap(True)
        self.activity_summary_label = QLabel("")
        self.activity_summary_label.setWordWrap(True)
        self.project_notes_edit = QTextEdit()
        self.project_notes_edit.setPlaceholderText("Project notes")
        self.project_notes_edit.textChanged.connect(self._on_notes_changed)

        self.raw_filename_edit = QLineEdit()
        self.raw_filename_edit.textChanged.connect(self._on_filename_edited)
        self.clean_filename_edit = QLineEdit()
        self.clean_filename_edit.textChanged.connect(self._on_filename_edited)
        self.output_folder_edit = QLineEdit()
        self.output_folder_edit.textChanged.connect(self._on_output_folder_changed)

        self._active_asset_id: str | None = None
        self._eyedropper_active = False
        self._editing_programmatically = False

        self._build_layout()
        self._build_toolbar()
        self._build_menus()
        self._build_shortcuts()

        self.canvas.cropChanged.connect(self._on_canvas_crop_changed)
        self.preview_view.colorPicked.connect(self._on_color_picked)

        self._update_ui_from_project()
        self._autosave_timer.start()

    def _build_layout(self) -> None:
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(self._section("Project", self._build_project_panel()))
        left_layout.addWidget(self._section("Assets", self._build_asset_panel()), 1)
        left_layout.addWidget(self._section("Progress", self._build_progress_panel()))

        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.addWidget(self.canvas)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(self._section("Crop Preview", self._build_preview_section()), 1)
        right_layout.addWidget(self._section("Manual Cleanup", self.manual_cleanup_widget), 2)
        right_layout.addWidget(self._section("Normalization", self.normalization_widget), 3)
        right_layout.addWidget(self._section("Background Removal", self._build_cleanup_section()))
        right_layout.addWidget(self._section("Comparison Mode", self._build_comparison_section()))
        right_layout.addWidget(self._section("Export Information", self._build_export_section()))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready")

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        actions = [
            ("New Project", self.new_project),
            ("Open Project", self.open_project),
            ("Save Project", self.save_project),
            ("Save Project As", self.save_project_as),
            ("Add Asset", self.add_asset),
            ("Duplicate Asset", self.duplicate_asset),
            ("Delete Asset", self.delete_asset),
            ("Export Raw", self.export_raw),
            ("Export Clean", self.export_clean),
            ("Export Final", self.export_final),
            ("Export Normalized", self.export_normalized),
            ("Open Output Folder", self.open_output_folder),
            ("Create Freya Movement Template", self.create_freya_template),
            ("Activity Log", self.show_activity_log),
        ]
        for text, slot in actions:
            action = QAction(text, self)
            action.triggered.connect(slot)
            toolbar.addAction(action)

    def _build_menus(self) -> None:
        project_menu = self.menuBar().addMenu("Project")
        report_action = QAction("Normalization Report", self)
        report_action.triggered.connect(self._open_normalization_report)
        project_menu.addAction(report_action)
        compare_action = QAction("Compare Alignment", self)
        compare_action.triggered.connect(self._open_compare_alignment)
        project_menu.addAction(compare_action)

    def _build_shortcuts(self) -> None:
        shortcuts = [
            (QKeySequence("Ctrl+N"), self.new_project),
            (QKeySequence("Ctrl+O"), self.open_project),
            (QKeySequence("Ctrl+S"), self.save_project),
            (QKeySequence("Ctrl+Shift+S"), self.save_project_as),
            (QKeySequence("Ctrl+Alt+A"), self.add_asset),
            (QKeySequence("Ctrl+D"), self.duplicate_asset),
            (QKeySequence("Ctrl+F"), self.search_edit.setFocus),
            (QKeySequence("Ctrl+E"), self.export_clean),
            (QKeySequence("Ctrl+Shift+E"), self.export_raw),
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
        ]
        self._shortcuts = [QShortcut(sequence, self, slot) for sequence, slot in shortcuts]
        self._delete_shortcut = QShortcut(QKeySequence("Delete"), self)
        self._delete_shortcut.activated.connect(self._on_delete_shortcut)

    def _section(self, title: str, widget: QWidget, stretch: int = 0) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.addWidget(widget, stretch)
        return box

    def _build_project_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel("Project Name"))
        layout.addWidget(self.project_name_label)

        buttons = QHBoxLayout()
        for text, slot in [
            ("Add Source Sheet", self.add_source_sheet),
            ("Remove Source Sheet", self.remove_source_sheet),
            ("Rename Label", self.rename_source_sheet_label),
            ("Relink Source", self.relink_source_sheet),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        layout.addWidget(QLabel("Source Sheet"))
        layout.addWidget(self.source_sheet_combo)
        layout.addWidget(self.source_sheet_label)
        layout.addWidget(self.source_image_info_label)

        layout.addWidget(QLabel("Search"))
        layout.addWidget(self.search_edit)

        filter_row = QHBoxLayout()
        filter_row.addWidget(self.group_filter)
        filter_row.addWidget(self.category_filter)
        filter_row.addWidget(self.direction_filter)
        filter_row.addWidget(self.status_filter)
        layout.addLayout(filter_row)

        layout.addWidget(self.asset_list, 1)
        layout.addWidget(self.activity_summary_label)
        return widget

    def _build_asset_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        row = QHBoxLayout()
        for text, slot in [
            ("Add Asset", self.add_asset),
            ("Duplicate", self.duplicate_asset),
            ("Delete", self.delete_asset),
            ("Move Up", lambda: self.move_asset(-1)),
            ("Move Down", lambda: self.move_asset(1)),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        for text, slot in [
            ("Mark Reviewed", lambda: self._mark_status(WorkflowStatus.reviewed)),
            ("Mark Needs Revision", lambda: self._mark_status(WorkflowStatus.needs_revision)),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            layout.addWidget(button)
        return widget

    def _build_progress_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.group_progress_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.project_notes_edit)
        return widget

    def _build_preview_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("Mode"))
        control_row.addWidget(self.preview_mode_combo)
        control_row.addWidget(QLabel("Zoom"))
        control_row.addWidget(self.preview_zoom_spin)
        layout.addLayout(control_row)
        layout.addWidget(self.preview_view, 1)
        return widget

    def _build_cleanup_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        buttons = QHBoxLayout()
        buttons.addWidget(self.pick_background_button)
        buttons.addWidget(self.reset_background_button)
        buttons.addWidget(self.regenerate_filename_button)
        layout.addLayout(buttons)
        layout.addWidget(self.background_swatch)
        layout.addWidget(self.rgb_label)
        layout.addWidget(self.rgba_label)
        layout.addWidget(self.hex_label)
        layout.addWidget(self.tolerance_slider)
        layout.addWidget(self.tolerance_spin)
        layout.addWidget(self.connected_only_checkbox)
        layout.addWidget(self.connectivity_checkbox)
        layout.addWidget(QLabel("Warnings"))
        layout.addWidget(self.warning_label)
        return widget

    def _build_comparison_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Preview backgrounds"))
        layout.addWidget(self.background_style_combo)
        layout.addWidget(QLabel("Checkerboard size"))
        layout.addWidget(self.checkerboard_combo)
        return widget

    def _build_export_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        form.addRow("Raw filename", self.raw_filename_edit)
        form.addRow("Clean filename", self.clean_filename_edit)
        form.addRow("Output folder", self.output_folder_edit)
        layout.addLayout(form)
        layout.addWidget(self.crop_status_label)
        layout.addWidget(self.dimensions_label)
        layout.addWidget(self.removed_pixels_label)
        layout.addWidget(self.removed_percentage_label)
        layout.addWidget(self.export_mode_label)
        layout.addWidget(self.zoom_status_label)
        return widget

    def _source_sheet_map(self) -> dict[str, SourceSheet]:
        return {sheet.source_sheet_id: sheet for sheet in self.project_manager.project.source_sheets}

    def _current_asset(self) -> AssetRecord | None:
        return self.project_manager.active_asset

    def _on_notes_changed(self, *_args) -> None:
        if self._editing_programmatically:
            return
        asset = self._current_asset()
        if asset is None:
            self.project_manager.project.project.notes = self.project_notes_edit.toPlainText()
            self.project_manager.project.mark_modified()
            return
        self.project_manager.edit_asset(asset.asset_uuid, notes=self.project_notes_edit.toPlainText())
        self._schedule_preview_refresh()

    def _on_filename_edited(self, *_args) -> None:
        if self._editing_programmatically:
            return
        asset = self._current_asset()
        if asset is None:
            return
        self.project_manager.edit_asset(
            asset.asset_uuid,
            raw_output_filename=self.raw_filename_edit.text().strip(),
            clean_output_filename=self.clean_filename_edit.text().strip(),
        )
        self._refresh_asset_list()

    def _on_output_folder_changed(self, *_args) -> None:
        if self._editing_programmatically:
            return
        asset = self._current_asset()
        if asset is None:
            self.project_manager.project.defaults.output_folder = self.output_folder_edit.text().strip()
            self.project_manager.project.mark_modified()
            return
        self.project_manager.edit_asset(asset.asset_uuid, output_folder=self.output_folder_edit.text().strip())

    def _on_tolerance_changed(self, *_args) -> None:
        if self._editing_programmatically:
            return
        value = self.tolerance_slider.value() if self.sender() is self.tolerance_slider else self.tolerance_spin.value()
        self.tolerance_slider.blockSignals(True)
        self.tolerance_spin.blockSignals(True)
        self.tolerance_slider.setValue(value)
        self.tolerance_spin.setValue(value)
        self.tolerance_slider.blockSignals(False)
        self.tolerance_spin.blockSignals(False)
        asset = self._current_asset()
        if asset is not None:
            self.project_manager.apply_background_settings_to_active_asset(
                self._background_rgba(),
                value,
                self.connected_only_checkbox.isChecked(),
                8 if self.connectivity_checkbox.isChecked() else 4,
            )
            if asset.workflow_status == WorkflowStatus.reviewed:
                self._handle_reviewed_edit(asset)
        self._schedule_preview_refresh()

    def _on_delete_shortcut(self) -> None:
        if isinstance(self.focusWidget(), (QLineEdit, QTextEdit)):
            return
        self.delete_asset()

    def _on_preview_mode_changed(self, *_args) -> None:
        self.preview_view.set_preview_mode({0: "before", 1: "after", 2: "split"}[self.preview_mode_combo.currentIndex()])
        self._schedule_preview_refresh()

    def _sync_preview_zoom(self, value: int) -> None:
        self.preview_zoom_spin.blockSignals(True)
        self.preview_zoom_spin.setValue(value)
        self.preview_zoom_spin.blockSignals(False)
        self.zoom_status_label.setText(f"Current zoom: {value}%")

    def _schedule_preview_refresh(self, *_args) -> None:
        self._debounce_timer.start(120)

    def _refresh_preview(self) -> None:
        asset = self._current_asset()
        if asset is None:
            self.preview_view.set_images(None, None)
            self.manual_cleanup_widget.set_document(None)
            self.normalization_widget.set_context(self.project_manager, None, self.project_path)
            self._update_status_labels()
            return
        raw_image = self._raw_crop_for_asset(asset)
        if raw_image is None:
            self.preview_view.set_images(None, None)
            self.manual_cleanup_widget.set_document(None)
            self.normalization_widget.set_context(self.project_manager, asset, self.project_path)
            self._update_status_labels()
            return
        clean_result = self._apply_cleaning(raw_image, asset.background_removal)
        self.preview_view.set_images(raw_image, clean_result.cleaned_image)
        self.preview_view.set_preview_mode({0: "before", 1: "after", 2: "split"}[self.preview_mode_combo.currentIndex()])
        self.preview_view.set_background_style(self.background_style_combo.currentText(), self.checkerboard_combo.currentText())
        self.manual_cleanup_widget.set_document(self._manual_document_for_asset(asset, raw_image, clean_result.cleaned_image))
        self.normalization_widget.set_context(self.project_manager, asset, self.project_path)
        self._update_status_labels(clean_result)

    def _update_status_labels(self, result: BackgroundRemovalResult | None = None) -> None:
        asset = self._current_asset()
        if asset is None:
            self.crop_status_label.setText("Crop rectangle: none")
            self.dimensions_label.setText("Raw crop dimensions: none")
            self.removed_pixels_label.setText("Removed pixels: 0")
            self.removed_percentage_label.setText("Removed: 0.0%")
            self.zoom_status_label.setText(f"Current zoom: {self.preview_view.zoom_percent()}%")
            self.export_mode_label.setText("Export mode: raw + clean")
            self.warning_label.setText("")
            self._refresh_progress()
            return
        crop = asset.crop_rect
        if crop is None:
            self.crop_status_label.setText("Crop rectangle: none")
            self.dimensions_label.setText("Raw crop dimensions: none")
        else:
            self.crop_status_label.setText(f"Crop rectangle: {crop.x}, {crop.y}, {crop.width}, {crop.height}")
            self.dimensions_label.setText(f"Raw crop dimensions: {crop.width} x {crop.height}")
        self.raw_filename_edit.setText(asset.raw_output_filename)
        self.clean_filename_edit.setText(asset.clean_output_filename)
        self.output_folder_edit.setText(asset.output_folder or self.project_manager.project.defaults.output_folder)
        self.removed_pixels_label.setText(f"Removed pixels: {result.removed_pixels if result else 0}")
        self.removed_percentage_label.setText(f"Removed: {result.removal_percentage:.1f}%" if result else "Removed: 0.0%")
        self.export_mode_label.setText(f"Export mode: {asset.workflow_status.value}")
        self.zoom_status_label.setText(f"Current zoom: {self.preview_view.zoom_percent()}%")
        warnings = removal_warning_messages(
            crop_exists=asset.crop_rect is not None,
            background_rgba=asset.background_removal.background_rgba,
            connected_background_only=asset.background_removal.connected_background_only,
            removal_result=result,
        )
        if result is not None and result.removal_percentage > 80:
            warnings.append("Removal erases more than 80% of crop pixels.")
        self.warning_label.setText(" ".join(warnings))
        self._refresh_progress()

    def _refresh_progress(self) -> None:
        counts = self.project_manager.project_progress_counts()
        total = counts["total"]
        exported = counts["exported_count"]
        self.progress_label.setText(f"Project Progress: {exported} / {total} exported")
        self.progress_bar.setValue(0 if total == 0 else round(exported / total * 100))
        asset = self._current_asset()
        if asset is None or not asset.character_group:
            self.group_progress_label.setText("Group Progress: 0 / 0 exported")
        else:
            group_counts = self.project_manager.group_progress_counts(asset.character_group)
            self.group_progress_label.setText(
                f"Group Progress: {group_counts['exported_count']} / {group_counts['total']} exported"
            )

    def _update_ui_from_project(self) -> None:
        self.project_name_label.setText(self.project_manager.project.project.project_name)
        self.project_notes_edit.blockSignals(True)
        self.project_notes_edit.setPlainText(self.project_manager.project.project.notes)
        self.project_notes_edit.blockSignals(False)
        self._source_sheet_lookup = self._source_sheet_map()
        self._refresh_source_sheet_combo()
        self._refresh_asset_list()
        self._sync_active_asset_to_ui()
        self._refresh_progress()
        self._autosave_timer.setInterval(clamp_int(self.project_manager.project.project.autosave_interval_seconds, 30, 600) * 1000)

    def _refresh_source_sheet_combo(self) -> None:
        self.source_sheet_combo.blockSignals(True)
        self.source_sheet_combo.clear()
        for sheet in self.project_manager.project.source_sheets:
            self.source_sheet_combo.addItem(sheet.label, sheet.source_sheet_id)
        self.source_sheet_combo.blockSignals(False)
        if self.project_manager.project.source_sheets:
            self.source_sheet_combo.setCurrentIndex(0)
            self._on_source_sheet_selected()

    def _refresh_asset_list(self, *_args) -> None:
        search = self.search_edit.text().strip().lower()
        group = self.group_filter.currentText()
        category = self.category_filter.currentText()
        direction = self.direction_filter.currentText()
        status = self.status_filter.currentText()

        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        self.group_filter.blockSignals(True)
        self.category_filter.blockSignals(True)
        self.direction_filter.blockSignals(True)
        self.status_filter.blockSignals(True)
        self._populate_filters()
        self.group_filter.setCurrentText(group if group in [self.group_filter.itemText(i) for i in range(self.group_filter.count())] else "All groups")
        self.category_filter.setCurrentText(category if category in [self.category_filter.itemText(i) for i in range(self.category_filter.count())] else "All categories")
        self.direction_filter.setCurrentText(direction if direction in [self.direction_filter.itemText(i) for i in range(self.direction_filter.count())] else "All directions")
        self.status_filter.setCurrentText(status if status in [self.status_filter.itemText(i) for i in range(self.status_filter.count())] else "All statuses")
        self.group_filter.blockSignals(False)
        self.category_filter.blockSignals(False)
        self.direction_filter.blockSignals(False)
        self.status_filter.blockSignals(False)

        for asset in self.project_manager.project.assets:
            if search and search not in asset.display_name.lower() and search not in asset.notes.lower():
                continue
            if group != "All groups" and asset.character_group != group:
                continue
            if category != "All categories" and asset.category != category:
                continue
            if direction != "All directions" and asset.direction != direction:
                continue
            if status != "All statuses" and asset.workflow_status.value != status:
                continue
            item = QListWidgetItem(self._asset_label(asset))
            item.setData(Qt.ItemDataRole.UserRole, asset.asset_uuid)
            self.asset_list.addItem(item)
        self.asset_list.blockSignals(False)
        self._highlight_active_asset()

    def _sync_active_asset_to_ui(self) -> None:
        asset = self._current_asset()
        if asset is None:
            self._editing_programmatically = True
            self.raw_filename_edit.clear()
            self.clean_filename_edit.clear()
            self.output_folder_edit.clear()
            self._editing_programmatically = False
            return
        self._restore_asset_controls(asset)
        self._refresh_preview()

    def _populate_filters(self) -> None:
        def fill(combo: QComboBox, values: list[str], current: str) -> None:
            combo.clear()
            combo.addItem(current)
            for value in sorted({value for value in values if value}):
                combo.addItem(value)

        fill(self.group_filter, [asset.character_group for asset in self.project_manager.project.assets], "All groups")
        fill(self.category_filter, [asset.category for asset in self.project_manager.project.assets], "All categories")
        fill(self.direction_filter, [asset.direction for asset in self.project_manager.project.assets], "All directions")
        fill(self.status_filter, [asset.workflow_status.value for asset in self.project_manager.project.assets], "All statuses")

    def _asset_label(self, asset: AssetRecord) -> str:
        marker = {
            WorkflowStatus.planned: "○",
            WorkflowStatus.cropped: "◌",
            WorkflowStatus.cleaned: "◍",
            WorkflowStatus.reviewed: "✓",
            WorkflowStatus.exported: "✔",
            WorkflowStatus.needs_revision: "⚠",
        }[asset.workflow_status]
        parts = [asset.display_name]
        if asset.character_group:
            parts.insert(0, asset.character_group)
        if asset.category:
            parts.insert(1 if asset.character_group else 0, asset.category)
        if asset.direction:
            parts.append(asset.direction)
        if asset.frame_number is not None:
            parts.append(f"{asset.frame_number:02d}")
        return f"[{marker}] " + " / ".join(parts)

    def _highlight_active_asset(self) -> None:
        active = self._active_asset_id
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) == active)

    def _on_asset_selection_changed(self) -> None:
        selected = self.asset_list.selectedItems()
        if not selected:
            return
        asset_uuid = selected[0].data(Qt.ItemDataRole.UserRole)
        self._set_active_asset(asset_uuid)

    def _set_active_asset(self, asset_uuid: str) -> None:
        asset = self.project_manager.get_asset(asset_uuid)
        self.project_manager.active_asset_uuid = asset_uuid
        self._active_asset_id = asset_uuid
        sheet = self._sheet_for_asset(asset)
        if sheet is not None:
            self._load_source_sheet(sheet)
        self._restore_asset_controls(asset)
        self._refresh_preview()
        self._highlight_active_asset()

    def _restore_asset_controls(self, asset: AssetRecord) -> None:
        self._editing_programmatically = True
        self.tolerance_slider.setValue(asset.background_removal.tolerance_ui)
        self.tolerance_spin.setValue(asset.background_removal.tolerance_ui)
        self.connected_only_checkbox.setChecked(asset.background_removal.connected_background_only)
        self.connectivity_checkbox.setChecked(asset.background_removal.connectivity == 8)
        self.raw_filename_edit.setText(asset.raw_output_filename)
        self.clean_filename_edit.setText(asset.clean_output_filename)
        self.output_folder_edit.setText(asset.output_folder or self.project_manager.project.defaults.output_folder)
        self.project_notes_edit.setPlainText(self.project_manager.project.project.notes)
        self.preview_view.set_preview_mode("before" if self.preview_mode_combo.currentIndex() == 0 else "after" if self.preview_mode_combo.currentIndex() == 1 else "split")
        self._editing_programmatically = False

    def _sheet_for_asset(self, asset: AssetRecord) -> SourceSheet | None:
        if asset.source_sheet_id and asset.source_sheet_id in self._source_sheet_lookup:
            return self._source_sheet_lookup[asset.source_sheet_id]
        if asset.source_sheet_path:
            for sheet in self.project_manager.project.source_sheets:
                if Path(sheet.path) == Path(asset.source_sheet_path):
                    return sheet
        return None

    def _load_source_sheet(self, sheet: SourceSheet) -> None:
        path = Path(sheet.path)
        if not path.exists():
            sheet.missing = True
            self.source_image_info_label.setText(f"Missing source sheet: {sheet.label}\n{sheet.path}")
            return
        if sheet.source_sheet_id not in self._loaded_images:
            try:
                self._loaded_images[sheet.source_sheet_id] = load_png(path)
            except ImageLoadError as exc:
                self._show_error("Could not open source sheet", str(exc))
                return
        image = self._loaded_images[sheet.source_sheet_id]
        pixmap = pil_image_to_qpixmap(image)
        self.canvas.set_image(pixmap)
        self.source_image_info_label.setText(
            f"{sheet.label}\n{sheet.path}\n{sheet.width or image.width} x {sheet.height or image.height}"
        )

    def _on_source_sheet_selected(self, *_args) -> None:
        if self.source_sheet_combo.currentIndex() < 0:
            return
        sheet_id = self.source_sheet_combo.currentData()
        if not sheet_id:
            return
        self._active_sheet_id = str(sheet_id)
        sheet = self._source_sheet_lookup.get(self._active_sheet_id)
        if sheet is not None:
            self._load_source_sheet(sheet)
            self.source_sheet_label.setText(sheet.label)
        self._refresh_preview()

    def _raw_crop_for_asset(self, asset: AssetRecord) -> Image.Image | None:
        sheet = self._sheet_for_asset(asset)
        if sheet is None or not sheet.path:
            return None
        path = Path(sheet.path)
        if not path.exists():
            return None
        image = self._loaded_images.get(sheet.source_sheet_id)
        if image is None:
            image = load_png(path)
            self._loaded_images[sheet.source_sheet_id] = image
        if asset.crop_rect is None:
            return None
        return crop_image(image, asset.crop_rect)

    def _apply_cleaning(self, raw_image: Image.Image, settings: BackgroundRemovalSettingsModel) -> BackgroundRemovalResult:
        config = BackgroundRemovalSettings(
            background_rgba=settings.background_rgba,
            tolerance_ui=settings.tolerance_ui,
            connected_background_only=settings.connected_background_only,
            connectivity=settings.connectivity,
        )
        return apply_background_removal(raw_image, config)

    def _manual_document_for_asset(self, asset: AssetRecord, raw_image: Image.Image, auto_clean: Image.Image) -> ManualEditDocument:
        existing = self._manual_documents.get(asset.asset_uuid)
        if existing is not None and existing.size == raw_image.size:
            final_image = auto_clean
            if asset.manual_edit_sidecar:
                sidecar = self._manual_sidecar_for_asset(asset)
                if sidecar is not None and sidecar.exists():
                    try:
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
                            final_image = load_sidecar_png(sidecar)
                    except Exception:
                        pass
            existing.rebase_images(raw_image, auto_clean, final_image, clear_history=False)
            existing.background_rgba = asset.background_removal.background_rgba
            existing.cleanup_settings_checksum = compute_settings_checksum(asset.background_removal.to_dict())
            existing.source_sheet_checksum = self._source_sheet_checksum_for_asset(asset) or ""
            return existing
        sidecar = self._manual_sidecar_for_asset(asset)
        final_image = auto_clean
        if sidecar is not None and sidecar.exists():
            try:
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
                    final_image = load_sidecar_png(sidecar)
            except Exception:
                pass
        document = ManualEditDocument(
            raw_crop=raw_image,
            auto_clean=auto_clean,
            final_edited=final_image,
            background_rgba=asset.background_removal.background_rgba,
            cleanup_settings_checksum=compute_settings_checksum(asset.background_removal.to_dict()),
            source_sheet_checksum=self._source_sheet_checksum_for_asset(asset) or "",
        )
        self._manual_documents[asset.asset_uuid] = document
        return document

    def _manual_sidecar_for_asset(self, asset: AssetRecord) -> Path | None:
        if asset.manual_edit_sidecar:
            sidecar = Path(asset.manual_edit_sidecar)
            if not sidecar.is_absolute() and self.project_path is not None:
                sidecar = self.project_path.parent / sidecar
            return sidecar
        if self.project_path is None:
            return None
        return manual_edit_sidecar_path(self.project_path, asset.asset_uuid)

    def _source_sheet_checksum_for_asset(self, asset: AssetRecord) -> str | None:
        sheet = self._sheet_for_asset(asset)
        return sheet.checksum if sheet is not None else None

    def _background_rgba(self) -> tuple[int, int, int, int] | None:
        asset = self._current_asset()
        if asset is None:
            return None
        return asset.background_removal.background_rgba

    def _on_canvas_crop_changed(self, rect: QRectF) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        crop = self._rect_to_crop(rect)
        if crop is None:
            return
        if asset.workflow_status == WorkflowStatus.reviewed:
            self._handle_reviewed_edit(asset)
        self.project_manager.apply_crop_to_active_asset(crop)
        self._schedule_preview_refresh()
        self._refresh_asset_list()

    def _rect_to_crop(self, rect: QRectF):
        left = int(round(rect.left()))
        top = int(round(rect.top()))
        width = int(round(rect.width()))
        height = int(round(rect.height()))
        from ..models import CropRect

        return CropRect(left, top, width, height)

    def _on_color_picked(self, picked: PickedColor) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        self._eyedropper_active = False
        self.preview_view.set_eyedropper_active(False)
        if asset.workflow_status == WorkflowStatus.reviewed:
            self._handle_reviewed_edit(asset)
        self.project_manager.apply_background_settings_to_active_asset(
            picked.rgba,
            asset.background_removal.tolerance_ui,
            asset.background_removal.connected_background_only,
            asset.background_removal.connectivity,
        )
        self._schedule_preview_refresh()

    def _start_eyedropper(self) -> None:
        if self._current_asset() is None:
            self._show_warning("No active asset selected.")
            return
        self._eyedropper_active = True
        self.preview_view.set_eyedropper_active(True)

    def _reset_background_removal(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        self.project_manager.edit_asset(
            asset.asset_uuid,
            background_rgba=None,
            tolerance_ui=5,
            connected_background_only=True,
            connectivity=4,
        )
        self._restore_asset_controls(asset)
        self._schedule_preview_refresh()

    def _regenerate_filename(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        generated = generate_filename(
            asset.character_group or asset.display_name,
            asset.category,
            asset.action,
            asset.direction,
            asset.frame_number,
            asset.variant,
        )
        self.raw_filename_edit.setText(generated)
        self.clean_filename_edit.setText(generated.replace(".png", "_clean.png"))

    def _handle_reviewed_edit(self, asset: AssetRecord) -> None:
        response = QMessageBox.question(
            self,
            "Reviewed asset edited",
            f"{asset.display_name} is marked reviewed. Change it back to needs revision?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if response == QMessageBox.StandardButton.Yes:
            self.project_manager.mark_status(asset.asset_uuid, WorkflowStatus.needs_revision)

    def _mark_status(self, status: WorkflowStatus) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        self.project_manager.mark_status(asset.asset_uuid, status)
        self._refresh_asset_list()
        self._schedule_preview_refresh()

    def add_source_sheet(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Add Source Sheet", "", "PNG Images (*.png)")
        if not file_name:
            return
        sheet = self.project_manager.add_source_sheet(file_name)
        if sheet.source_sheet_id not in self._loaded_images:
            try:
                self._loaded_images[sheet.source_sheet_id] = load_png(file_name)
            except ImageLoadError:
                pass
        self._update_ui_from_project()
        self.statusBar().showMessage(f"Added source sheet {sheet.label}", 4000)

    def remove_source_sheet(self) -> None:
        sheet = self._current_source_sheet()
        if sheet is None:
            return
        used = [asset.display_name for asset in self.project_manager.project.assets if asset.source_sheet_id == sheet.source_sheet_id]
        text = f"Remove reference to {sheet.label}?"
        if used:
            text += f"\nAssets using it: {', '.join(used[:5])}"
        if QMessageBox.question(self, "Remove Source Sheet", text) != QMessageBox.StandardButton.Yes:
            return
        self.project_manager.remove_source_sheet(sheet.source_sheet_id)
        self._update_ui_from_project()

    def rename_source_sheet_label(self) -> None:
        sheet = self._current_source_sheet()
        if sheet is None:
            return
        from PySide6.QtWidgets import QInputDialog

        label, ok = QInputDialog.getText(self, "Rename Source Sheet", "Display label:", text=sheet.label)
        if ok and label.strip():
            sheet.label = label.strip()
            self.project_manager.project.mark_modified()
            self._update_ui_from_project()

    def relink_source_sheet(self) -> None:
        sheet = self._current_source_sheet()
        if sheet is None:
            return
        file_name, _ = QFileDialog.getOpenFileName(self, "Relink Source Sheet", "", "PNG Images (*.png)")
        if not file_name:
            return
        self.project_manager.relink_source_sheet(sheet.source_sheet_id, file_name)
        self._loaded_images.pop(sheet.source_sheet_id, None)
        self._update_ui_from_project()

    def _current_source_sheet(self) -> SourceSheet | None:
        sheet_id = self.source_sheet_combo.currentData()
        if not sheet_id:
            return None
        return self._source_sheet_lookup.get(str(sheet_id))

    def add_asset(self) -> None:
        dialog = AssetDialog(self, [(sheet.source_sheet_id, sheet.label) for sheet in self.project_manager.project.source_sheets])
        current_sheet = self._current_source_sheet()
        if current_sheet is not None:
            index = dialog.source_sheet_combo.findData(current_sheet.source_sheet_id)
            if index >= 0:
                dialog.source_sheet_combo.setCurrentIndex(index)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not payload["display_name"]:
            payload["display_name"] = payload["character_group"] or "asset"
        asset = self.project_manager.add_asset(
            display_name=str(payload["display_name"]),
            source_sheet_id=str(payload["source_sheet_id"]),
            source_sheet_path=str(self._source_sheet_lookup.get(str(payload["source_sheet_id"]), SourceSheet("", "", "")).path if payload["source_sheet_id"] else ""),
            character_group=str(payload["character_group"]),
            category=str(payload["category"]),
            action=str(payload["action"]),
            direction=str(payload["direction"]),
            frame_number=payload["frame_number"],
            variant=str(payload["variant"]),
            output_folder=str(payload["output_folder"]),
            notes=str(payload["notes"]),
        )
        self._active_asset_id = asset.asset_uuid
        self.project_manager.active_asset_uuid = asset.asset_uuid
        self._update_ui_from_project()
        self._set_active_asset(asset.asset_uuid)

    def duplicate_asset(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        duplicate = self.project_manager.duplicate_asset(asset.asset_uuid, preserve_crop_rect=True)
        self._update_ui_from_project()
        self._set_active_asset(duplicate.asset_uuid)

    def delete_asset(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QCheckBox, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Delete Asset")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Delete asset '{asset.display_name}'?"))
        trash_box = QCheckBox("Also move exported files to project trash")
        layout.addWidget(trash_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.project_manager.delete_asset(asset.asset_uuid, move_exports_to_trash=trash_box.isChecked())
        self._update_ui_from_project()

    def move_asset(self, step: int) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        self.project_manager.move_asset(asset.asset_uuid, step)
        self._update_ui_from_project()

    def create_freya_template(self) -> None:
        sheet = self._current_source_sheet()
        if sheet is None:
            self._show_warning("Choose the Freya source sheet first.")
            return
        created, skipped = self.project_manager.create_freya_movement_template(sheet.source_sheet_id, sheet.path)
        self._update_ui_from_project()
        summary = f"Created {len(created)} records."
        if skipped:
            summary += f" Skipped {len(skipped)} existing records."
        self.statusBar().showMessage(summary, 5000)

    def show_activity_log(self) -> None:
        dialog = ActivityLogDialog(self.project_manager.project.activity_log, self)
        dialog.exec()

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as()
            return
        self._save_to_path(self.project_path)

    def save_project_as(self) -> None:
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Project As", str(Path.cwd() / "config" / "freya_project.json"), "JSON Files (*.json)")
        if not file_name:
            return
        self._save_to_path(Path(file_name))

    def _save_to_path(self, path: Path) -> None:
        self.project_manager.project.path = path
        self.project_manager.project.project.project_root_directory = str(path.parent)
        self._persist_manual_edits(path)
        save_sprite_project(self.project_manager.project, path)
        self.project_path = path
        self.project_manager.project.modified = False
        from ..project_model import ActivityEntry, utc_now_iso

        self.project_manager.project.activity_log.append(
            ActivityEntry(timestamp=utc_now_iso(), event_type="project_save", message=f"Project saved to {path}")
        )
        self.project_manager.project.activity_log = self.project_manager.project.activity_log[-1000:]
        self.statusBar().showMessage(f"Saved project: {path}", 4000)

    def new_project(self) -> None:
        self.project_manager.new_project("Untitled Project", str(Path.cwd()))
        self.project_path = None
        self._loaded_images.clear()
        self._manual_documents.clear()
        self._active_sheet_id = None
        self._active_asset_id = None
        self._update_ui_from_project()

    def open_project(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Project", str(Path.cwd() / "config"), "JSON Files (*.json)")
        if not file_name:
            return
        path = Path(file_name)
        autosave = autosave_path(path)
        if has_newer_autosave(path):
            response = QMessageBox.question(
                self,
                "Recover autosave?",
                f"A newer autosave was found:\n{autosave}\nRecover it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if response == QMessageBox.StandardButton.Yes:
                self.project_manager.project = recover_autosave(path)
                self.project_manager.project.path = path
            else:
                self.project_manager.project = load_sprite_project(
                    path,
                    recover_backup=self._recover_backup_prompt,
                )
        else:
            self.project_manager.project = load_sprite_project(path, recover_backup=self._recover_backup_prompt)
        self.project_path = path
        self._loaded_images.clear()
        self._manual_documents.clear()
        self._update_ui_from_project()
        self.statusBar().showMessage(f"Loaded project: {path}", 4000)

    def _recover_backup_prompt(self, main_path: Path, backup_path: Path) -> bool:
        response = QMessageBox.question(
            self,
            "Recover backup?",
            f"The main project file appears malformed:\n{main_path}\nRecover from backup?\n{backup_path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return response == QMessageBox.StandardButton.Yes

    def _maybe_autosave(self) -> None:
        project = self.project_manager.project
        if project.path is None or not project.modified or not project.project.autosave_enabled:
            return
        try:
            self._persist_manual_edits(project.path)
            save_autosave(project)
        except Exception:
            pass

    def export_raw(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        raw_image = self._raw_crop_for_asset(asset)
        if raw_image is None:
            self._show_error("Nothing to export", "Selected asset has no valid crop.")
            return
        destination = self._export_destination(asset.raw_output_filename or "crop_raw.png", asset.output_folder)
        if destination is None:
            return
        self._save_export(asset, raw_image, destination, "raw")

    def export_clean(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        raw_image = self._raw_crop_for_asset(asset)
        if raw_image is None:
            self._show_error("Nothing to export", "Selected asset has no valid crop.")
            return
        clean = self._apply_cleaning(raw_image, asset.background_removal)
        destination = self._export_destination(asset.clean_output_filename or "crop_clean.png", asset.output_folder)
        if destination is None:
            return
        self._save_export(asset, clean.cleaned_image, destination, "clean")

    def export_final(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        document = self.manual_cleanup_widget.document()
        if document is None:
            self._show_error("Nothing to export", "No manual cleanup document is available.")
            return
        destination = self._export_destination(asset.clean_output_filename.replace("_clean.png", "_final.png") if asset.clean_output_filename else "crop_final.png", asset.output_folder)
        if destination is None:
            return
        if destination.exists():
            response = QMessageBox.question(
                self,
                "Overwrite file?",
                f"{destination} already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        document.export_final(destination)
        asset.export_info.exported_path = str(destination)
        asset.export_info.exported_at = utc_now_iso()
        asset.workflow_status = WorkflowStatus.exported
        asset.modified_at = utc_now_iso()
        self.project_manager.project.activity_log.append(
            ActivityEntry(timestamp=utc_now_iso(), event_type="export_success", message=f"Exported {asset.display_name} (final)", asset_uuid=asset.asset_uuid)
        )
        self.project_manager.project.activity_log = self.project_manager.project.activity_log[-1000:]
        self.project_manager.project.mark_modified()
        self._update_ui_from_project()
        self.statusBar().showMessage(f"Exported {destination.name}", 4000)

    def export_normalized(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        if self.project_manager.project.path is None:
            self._show_error("Cannot export", "Save the project first so normalization sidecars and exports have a project root.")
            return
        destination = self._export_destination(
            asset.normalization.normalized_output_filename
            or generate_normalized_filename(
                asset.character_group or asset.display_name,
                asset.category,
                asset.action,
                asset.direction,
                asset.frame_number,
                asset.variant,
                canvas_size=(asset.normalization.output_width, asset.normalization.output_height),
            ),
            asset.output_folder,
        )
        if destination is None:
            return
        self.project_manager.export_normalized_asset(destination, asset.asset_uuid)
        self._update_ui_from_project()

    def _open_normalization_report(self) -> None:
        rows = self.project_manager.asset_normalization_report()
        dialog = NormalizationReportDialog(rows, self)
        dialog.exec()

    def _open_compare_alignment(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        group_assets = [
            item
            for item in self.project_manager.project.assets
            if item.alignment_group == asset.alignment_group or item.character_group == asset.character_group
        ]
        dialog = CompareAlignmentDialog(group_assets, self)
        dialog.exec()

    def _export_destination(self, filename: str, output_folder: str) -> Path | None:
        start = Path(output_folder or self.project_manager.project.project.defaults.output_folder or Path.cwd() / "output")
        file_name, _ = QFileDialog.getSaveFileName(self, "Export PNG", str(start / filename), "PNG Files (*.png)")
        return Path(file_name) if file_name else None

    def _save_export(self, asset: AssetRecord, image: Image.Image, destination: Path, kind: str) -> None:
        if destination.exists():
            response = QMessageBox.question(
                self,
                "Overwrite file?",
                f"{destination} already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        export_png(image, destination)
        asset.export_info.exported_path = str(destination)
        asset.export_info.exported_at = utc_now_iso()
        asset.workflow_status = WorkflowStatus.exported
        asset.modified_at = utc_now_iso()
        self.project_manager.project.activity_log.append(
            ActivityEntry(timestamp=utc_now_iso(), event_type="export_success", message=f"Exported {asset.display_name} ({kind})", asset_uuid=asset.asset_uuid)
        )
        self.project_manager.project.activity_log = self.project_manager.project.activity_log[-1000:]
        self.project_manager.project.mark_modified()
        self._update_ui_from_project()
        self.statusBar().showMessage(f"Exported {destination.name}", 4000)

    def open_output_folder(self) -> None:
        asset = self._current_asset()
        folder = Path(asset.output_folder if asset and asset.output_folder else self.project_manager.project.project.defaults.output_folder or Path.cwd() / "output")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(folder)
        except Exception:
            self._show_warning(f"Could not open folder: {folder}")

    def undo(self) -> None:
        pass

    def redo(self) -> None:
        pass

    def eventFilter(self, obj, event):
        if obj is self.asset_list and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Delete:
            if isinstance(self.focusWidget(), (QLineEdit, QTextEdit)):
                return False
            self.delete_asset()
            return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:
        if self.project_manager.project.modified and self.project_path is not None:
            self._persist_manual_edits(self.project_path)
            self._maybe_autosave()
        super().closeEvent(event)

    def _persist_manual_edits(self, project_path: Path) -> None:
        for asset in self.project_manager.project.assets:
            document = self._manual_documents.get(asset.asset_uuid)
            if document is None or not document.dirty:
                continue
            try:
                self.project_manager.save_manual_edit_document(asset.asset_uuid, document, project_path)
            except Exception:
                continue

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _show_warning(self, message: str) -> None:
        QMessageBox.warning(self, "Warning", message)
