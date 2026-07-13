from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..config_store import load_config, save_config
from ..exceptions import ConfigError, CropError, ImageLoadError
from ..image_tools import analyze_image, load_png
from ..models import CropConfig, CropRect
from ..processing import (
    BackgroundRemovalResult,
    BackgroundRemovalSettings,
    apply_background_removal,
    clamp_int,
    crop_image,
    format_hex,
    format_rgb,
    format_rgba,
    export_png,
    removal_warning_messages,
    ui_tolerance_to_distance,
)
from .canvas_view import ImageCanvasView
from .preview_view import CropPreviewView, PickedColor


@dataclass(frozen=True, slots=True)
class EditorSnapshot:
    background_rgba: tuple[int, int, int, int] | None
    tolerance_ui: int
    connected_background_only: bool
    connectivity: int
    preview_mode: str
    background_style: str
    checkerboard_size: str
    output_raw_filename: str
    output_clean_filename: str
    last_export_directory: str | None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pixel Asset Extractor")
        self.resize(1700, 1000)

        self._source_path: Path | None = None
        self._source_image: Image.Image | None = None
        self._raw_crop: Image.Image | None = None
        self._clean_crop: Image.Image | None = None
        self._crop_rect: CropRect | None = None
        self._removal_result: BackgroundRemovalResult | None = None
        self._history: list[EditorSnapshot] = []
        self._history_index = -1
        self._last_export_directory: str | None = None
        self._eyedropper_active = False
        self._shortcuts: list[QShortcut] = []

        self._background_rgba: tuple[int, int, int, int] | None = None
        self._tolerance_ui = 5
        self._connected_background_only = True
        self._connectivity = 4
        self._preview_mode = "after"
        self._background_style = "checkerboard"
        self._checkerboard_size = "medium"
        self._output_raw_filename = ""
        self._output_clean_filename = ""

        self.canvas = ImageCanvasView()
        self.preview_view = CropPreviewView()
        self.preview_view.set_zoom_percent(100)
        self.preview_view.set_preview_mode(self._preview_mode)

        self.preview_zoom_spin = QSpinBox()
        self.preview_zoom_spin.setRange(25, 3200)
        self.preview_zoom_spin.setSuffix("%")
        self.preview_zoom_spin.setValue(100)
        self.preview_zoom_spin.valueChanged.connect(self.preview_view.set_zoom_percent)
        self.preview_view.zoomChanged.connect(self._sync_preview_zoom_spin)

        self.preview_mode_combo = QComboBox()
        self.preview_mode_combo.addItems(["Before", "After", "Split"])
        self.preview_mode_combo.currentIndexChanged.connect(self._on_preview_mode_changed)

        self.background_style_combo = QComboBox()
        self.background_style_combo.addItems(["checkerboard", "white", "black", "bright red", "bright green"])
        self.background_style_combo.currentTextChanged.connect(self._on_preview_style_changed)

        self.checker_size_combo = QComboBox()
        self.checker_size_combo.addItems(["small", "medium", "large"])
        self.checker_size_combo.currentTextChanged.connect(self._on_preview_style_changed)

        self.pick_background_button = QPushButton("Pick Background Color")
        self.pick_background_button.clicked.connect(self.start_eyedropper)

        self.reset_button = QPushButton("Reset Background Removal")
        self.reset_button.clicked.connect(self.reset_background_removal)

        self.tolerance_slider = QSlider(Qt.Orientation.Horizontal)
        self.tolerance_slider.setRange(0, 100)
        self.tolerance_slider.setValue(self._tolerance_ui)
        self.tolerance_slider.valueChanged.connect(self._on_tolerance_changed)

        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(0, 100)
        self.tolerance_spin.setValue(self._tolerance_ui)
        self.tolerance_spin.valueChanged.connect(self._on_tolerance_changed)

        self.connected_only_checkbox = QCheckBox("Remove connected background only")
        self.connected_only_checkbox.setChecked(True)
        self.connected_only_checkbox.toggled.connect(self._on_connected_mode_changed)

        self.connectivity_checkbox = QCheckBox("Use 8-way connectivity")
        self.connectivity_checkbox.setChecked(False)
        self.connectivity_checkbox.toggled.connect(self._on_connectivity_changed)

        self.background_swatch = QLabel()
        self.background_swatch.setFixedSize(48, 24)
        self.background_swatch.setStyleSheet("background: transparent; border: 1px solid #666;")

        self.rgb_label = QLabel("RGB: not selected")
        self.rgba_label = QLabel("RGBA: not selected")
        self.hex_label = QLabel("Hex: not selected")
        self.color_pick_status = QLabel("No background color selected.")

        self.crop_status_label = QLabel("Crop rectangle: none")
        self.raw_dimensions_label = QLabel("Raw crop dimensions: none")
        self.removed_pixels_label = QLabel("Removed pixels: 0")
        self.removed_percentage_label = QLabel("Removed: 0.0%")
        self.export_mode_label = QLabel("Export mode: raw + clean")
        self.zoom_label = QLabel("Current zoom: 100%")
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #c0392b; font-weight: 600;")

        self.raw_filename_edit = QLineEdit()
        self.raw_filename_edit.setPlaceholderText("raw filename")
        self.raw_filename_edit.textChanged.connect(self._on_export_name_changed)

        self.clean_filename_edit = QLineEdit()
        self.clean_filename_edit.setPlaceholderText("clean filename")
        self.clean_filename_edit.textChanged.connect(self._on_export_name_changed)

        self.export_directory_label = QLabel("Last export directory: not set")
        self.project_info_label = QLabel("Load a PNG reference sheet to begin.")
        self.project_info_label.setWordWrap(True)

        self._build_layout()
        self._build_toolbar()
        self._build_shortcuts()

        self.canvas.cropChanged.connect(self._on_crop_changed)
        self.preview_view.colorPicked.connect(self._on_preview_color_picked)

        self._update_ui_from_state()
        self._push_history(reset=True)

    def _build_layout(self) -> None:
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(self._section_widget("Crop Preview", self._build_preview_section()))
        right_layout.addWidget(self._section_widget("Background Removal", self._build_background_section()))
        right_layout.addWidget(self._section_widget("Comparison Mode", self._build_comparison_section()))
        right_layout.addWidget(self._section_widget("Export Information", self._build_export_section()))
        right_layout.addStretch(1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.canvas)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready")

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        actions = [
            ("Open PNG", self.open_png),
            ("Save Project", self.save_project),
            ("Load Project", self.load_project),
            ("Undo", self.undo),
            ("Redo", self.redo),
            ("Export Raw", self.export_raw),
            ("Export Clean", self.export_clean),
        ]
        for text, slot in actions:
            action = QAction(text, self)
            action.triggered.connect(slot)
            toolbar.addAction(action)

        toolbar.addSeparator()
        toolbar.addWidget(self.preview_zoom_spin)

    def _build_shortcuts(self) -> None:
        self._shortcuts.append(QShortcut(QKeySequence.StandardKey.Undo, self, self.undo))
        self._shortcuts.append(QShortcut(QKeySequence.StandardKey.Redo, self, self.redo))
        self._shortcuts.append(QShortcut(QKeySequence("Esc"), self, self.cancel_eyedropper))

    def _section_widget(self, title: str, content: QWidget) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.addWidget(content)
        return box

    def _build_preview_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Mode"))
        controls.addWidget(self.preview_mode_combo)
        controls.addWidget(QLabel("Zoom"))
        controls.addWidget(self.preview_zoom_spin)
        layout.addLayout(controls)

        layout.addWidget(self.preview_view, 1)
        return widget

    def _build_background_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        color_row = QHBoxLayout()
        color_row.addWidget(self.pick_background_button)
        color_row.addWidget(self.reset_button)
        layout.addLayout(color_row)

        layout.addWidget(self.background_swatch)
        layout.addWidget(self.rgb_label)
        layout.addWidget(self.rgba_label)
        layout.addWidget(self.hex_label)
        layout.addWidget(self.color_pick_status)

        tolerance_row = QHBoxLayout()
        tolerance_row.addWidget(QLabel("Tolerance"))
        tolerance_row.addWidget(self.tolerance_slider, 1)
        tolerance_row.addWidget(self.tolerance_spin)
        layout.addLayout(tolerance_row)

        layout.addWidget(self.connected_only_checkbox)
        layout.addWidget(self.connectivity_checkbox)

        self.tolerance_threshold_label = QLabel("Threshold: 0.00")
        layout.addWidget(self.tolerance_threshold_label)
        return widget

    def _build_comparison_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel("Preview backgrounds"))
        layout.addWidget(self.background_style_combo)
        layout.addWidget(QLabel("Checkerboard size"))
        layout.addWidget(self.checker_size_combo)
        return widget

    def _build_export_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        form = QFormLayout()
        form.addRow("Raw filename", self.raw_filename_edit)
        form.addRow("Clean filename", self.clean_filename_edit)
        layout.addLayout(form)

        layout.addWidget(self.crop_status_label)
        layout.addWidget(self.raw_dimensions_label)
        layout.addWidget(self.removed_pixels_label)
        layout.addWidget(self.removed_percentage_label)
        layout.addWidget(self.export_mode_label)
        layout.addWidget(self.zoom_label)
        layout.addWidget(self.export_directory_label)
        layout.addWidget(self.project_info_label)
        layout.addWidget(self.warning_label)
        return widget

    def _sync_preview_zoom_spin(self, value: int) -> None:
        if self.preview_zoom_spin.value() != value:
            self.preview_zoom_spin.blockSignals(True)
            self.preview_zoom_spin.setValue(value)
            self.preview_zoom_spin.blockSignals(False)
        self.zoom_label.setText(f"Current zoom: {value}%")

    def _on_preview_mode_changed(self, *_args) -> None:
        self._preview_mode = {0: "before", 1: "after", 2: "split"}[self.preview_mode_combo.currentIndex()]
        self.preview_view.set_preview_mode(self._preview_mode)
        self._push_history()
        self._refresh_all()

    def _on_preview_style_changed(self, *_args) -> None:
        self._background_style = self.background_style_combo.currentText()
        self._checkerboard_size = self.checker_size_combo.currentText()
        self.preview_view.set_background_style(self._background_style, self._checkerboard_size)
        self._push_history()

    def _on_tolerance_changed(self, *_args) -> None:
        value = self.tolerance_slider.value() if self.sender() is self.tolerance_slider else self.tolerance_spin.value()
        value = clamp_int(value, 0, 100)
        if self.tolerance_slider.value() != value:
            self.tolerance_slider.blockSignals(True)
            self.tolerance_slider.setValue(value)
            self.tolerance_slider.blockSignals(False)
        if self.tolerance_spin.value() != value:
            self.tolerance_spin.blockSignals(True)
            self.tolerance_spin.setValue(value)
            self.tolerance_spin.blockSignals(False)
        self._tolerance_ui = value
        self._push_history()
        self._refresh_processing()

    def _on_connected_mode_changed(self, *_args) -> None:
        self._connected_background_only = self.connected_only_checkbox.isChecked()
        self._push_history()
        self._refresh_processing()

    def _on_connectivity_changed(self, *_args) -> None:
        self._connectivity = 8 if self.connectivity_checkbox.isChecked() else 4
        self._push_history()
        self._refresh_processing()

    def _on_export_name_changed(self, *_args) -> None:
        self._output_raw_filename = self.raw_filename_edit.text().strip()
        self._output_clean_filename = self.clean_filename_edit.text().strip()
        self._push_history()

    def start_eyedropper(self) -> None:
        if self._raw_crop is None:
            self._show_warning("No crop exists yet.")
            return
        self._eyedropper_active = True
        self.preview_view.set_eyedropper_active(True)
        self.statusBar().showMessage("Eyedropper active. Click the preview to pick a background color, or press Escape to cancel.")

    def cancel_eyedropper(self) -> None:
        if not self._eyedropper_active:
            return
        self._eyedropper_active = False
        self.preview_view.set_eyedropper_active(False)
        self.statusBar().showMessage("Eyedropper cancelled", 3000)

    def _on_preview_color_picked(self, picked: PickedColor) -> None:
        self._eyedropper_active = False
        self.preview_view.set_eyedropper_active(False)
        self._set_background_color(picked.rgba)

    def _set_background_color(self, rgba: tuple[int, int, int, int]) -> None:
        self._background_rgba = rgba
        self._push_history()
        self._refresh_processing()

    def reset_background_removal(self) -> None:
        self._background_rgba = None
        self._tolerance_ui = 5
        self._connected_background_only = True
        self._connectivity = 4
        self._eyedropper_active = False
        self._restore_controls_from_state()
        self._push_history()
        self._refresh_processing()

    def open_png(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Open PNG reference sheet",
            "",
            "PNG Images (*.png);;All Files (*)",
        )
        if not file_name:
            return
        self.load_image(Path(file_name))

    def load_image(self, file_path: Path, reset_project: bool = True) -> None:
        try:
            image = load_png(file_path)
        except ImageLoadError as exc:
            self._show_error("Could not open image", str(exc))
            return

        self._source_path = file_path
        self._source_image = image
        self.project_info_label.setText(
            f"Loaded source image: {file_path}\n"
            f"Image size: {image.width} x {image.height}"
        )
        analysis = analyze_image(image)
        self.statusBar().showMessage(
            f"Loaded {file_path.name} | size {analysis.width} x {analysis.height} | edge pixels {analysis.edge_pixel_count}"
        )
        if reset_project:
            self._background_rgba = None
            self._tolerance_ui = 5
            self._connected_background_only = True
            self._connectivity = 4
            self._preview_mode = "after"
            self._background_style = "checkerboard"
            self._checkerboard_size = "medium"
            self._set_default_output_names()
            self._crop_rect = None
            self._raw_crop = None
            self._clean_crop = None
            self._removal_result = None
        self.canvas.set_image(self._source_to_pixmap(image))
        self.canvas.set_crop_rect(None)
        self.preview_view.set_images(None, None)
        self.preview_view.set_preview_mode(self._preview_mode)
        if reset_project:
            self._push_history(reset=True)
        self._refresh_all()

    def _source_to_pixmap(self, image: Image.Image):
        from ..image_tools import pil_image_to_qpixmap

        return pil_image_to_qpixmap(image)

    def _on_crop_changed(self, rect: QRectF) -> None:
        crop = CropRect(int(round(rect.x())), int(round(rect.y())), int(round(rect.width())), int(round(rect.height())))
        if not crop.is_valid() or self._source_image is None:
            return
        try:
            self._raw_crop = crop_image(self._source_image, crop)
        except CropError as exc:
            self._show_error("Invalid crop", str(exc))
            return
        self._crop_rect = crop
        self._push_history(reset=True)
        self._refresh_processing()

    def _refresh_processing(self) -> None:
        if self._raw_crop is None:
            self.preview_view.set_images(None, None)
            self._removal_result = None
            self._update_status()
            return

        settings = BackgroundRemovalSettings(
            background_rgba=self._background_rgba,
            tolerance_ui=self._tolerance_ui,
            connected_background_only=self._connected_background_only,
            connectivity=self._connectivity,
        )
        result = apply_background_removal(self._raw_crop, settings)
        self._removal_result = result
        self._clean_crop = result.cleaned_image
        self.preview_view.set_images(self._raw_crop, self._clean_crop)
        self.preview_view.set_preview_mode(self._preview_mode)
        self.preview_view.set_background_style(self._background_style, self._checkerboard_size)
        self._update_status()

    def _update_status(self) -> None:
        if self._crop_rect is None or self._raw_crop is None:
            self.crop_status_label.setText("Crop rectangle: none")
            self.raw_dimensions_label.setText("Raw crop dimensions: none")
        else:
            self.crop_status_label.setText(
                f"Crop rectangle: {self._crop_rect.x}, {self._crop_rect.y}, {self._crop_rect.width}, {self._crop_rect.height}"
            )
            self.raw_dimensions_label.setText(
                f"Raw crop dimensions: {self._raw_crop.width} x {self._raw_crop.height}"
            )

        self.rgb_label.setText(format_rgb(self._background_rgba))
        self.rgba_label.setText(format_rgba(self._background_rgba))
        self.hex_label.setText(format_hex(self._background_rgba))
        self.background_swatch.setStyleSheet(self._swatch_style())
        self.color_pick_status.setText(
            "Background color selected." if self._background_rgba is not None else "No background color selected."
        )

        threshold = ui_tolerance_to_distance(self._tolerance_ui)
        self.tolerance_threshold_label.setText(f"Threshold: {threshold:.2f}")
        self.removed_pixels_label.setText(
            f"Removed pixels: {self._removal_result.removed_pixels if self._removal_result else 0}"
        )
        self.removed_percentage_label.setText(
            f"Removed: {self._removal_result.removal_percentage:.1f}%" if self._removal_result else "Removed: 0.0%"
        )
        self.export_mode_label.setText("Export mode: raw + clean")
        self.zoom_label.setText(f"Current zoom: {self.preview_view.zoom_percent()}%")
        self.export_directory_label.setText(
            f"Last export directory: {self._last_export_directory or 'not set'}"
        )

        warnings = removal_warning_messages(
            crop_exists=self._crop_rect is not None and self._raw_crop is not None,
            background_rgba=self._background_rgba,
            connected_background_only=self._connected_background_only,
            removal_result=self._removal_result,
        )
        if self._raw_crop is not None and (self._raw_crop.width == 0 or self._raw_crop.height == 0):
            warnings.append("The crop is empty.")

        self.warning_label.setText(" ".join(warnings))
        if warnings:
            self.statusBar().showMessage(warnings[0], 5000)
        elif self._crop_rect is not None:
            self.statusBar().showMessage("Crop ready", 3000)

    def _swatch_style(self) -> str:
        if self._background_rgba is None:
            return "background: transparent; border: 1px solid #666;"
        r, g, b, a = self._background_rgba
        return f"background: rgba({r}, {g}, {b}, {a}); border: 1px solid #666;"

    def _set_default_output_names(self) -> None:
        if self._source_path is None:
            return
        stem = self._source_path.stem
        self._output_raw_filename = f"{stem}_raw.png"
        self._output_clean_filename = f"{stem}_clean.png"
        self._restore_controls_from_state()

    def _restore_controls_from_state(self) -> None:
        self.tolerance_slider.blockSignals(True)
        self.tolerance_spin.blockSignals(True)
        self.connected_only_checkbox.blockSignals(True)
        self.connectivity_checkbox.blockSignals(True)
        self.preview_mode_combo.blockSignals(True)
        self.background_style_combo.blockSignals(True)
        self.checker_size_combo.blockSignals(True)
        self.raw_filename_edit.blockSignals(True)
        self.clean_filename_edit.blockSignals(True)

        self.tolerance_slider.setValue(self._tolerance_ui)
        self.tolerance_spin.setValue(self._tolerance_ui)
        self.connected_only_checkbox.setChecked(self._connected_background_only)
        self.connectivity_checkbox.setChecked(self._connectivity == 8)
        self.preview_mode_combo.setCurrentIndex({"before": 0, "after": 1, "split": 2}[self._preview_mode])
        self.background_style_combo.setCurrentText(self._background_style)
        self.checker_size_combo.setCurrentText(self._checkerboard_size)
        self.raw_filename_edit.setText(self._output_raw_filename)
        self.clean_filename_edit.setText(self._output_clean_filename)

        self.tolerance_slider.blockSignals(False)
        self.tolerance_spin.blockSignals(False)
        self.connected_only_checkbox.blockSignals(False)
        self.connectivity_checkbox.blockSignals(False)
        self.preview_mode_combo.blockSignals(False)
        self.background_style_combo.blockSignals(False)
        self.checker_size_combo.blockSignals(False)
        self.raw_filename_edit.blockSignals(False)
        self.clean_filename_edit.blockSignals(False)

        self.preview_view.set_preview_mode(self._preview_mode)
        self.preview_view.set_background_style(self._background_style, self._checkerboard_size)
        self.preview_view.set_eyedropper_active(self._eyedropper_active)

    def _update_ui_from_state(self) -> None:
        self._restore_controls_from_state()
        self._update_status()

    def _current_snapshot(self) -> EditorSnapshot:
        return EditorSnapshot(
            background_rgba=self._background_rgba,
            tolerance_ui=self._tolerance_ui,
            connected_background_only=self._connected_background_only,
            connectivity=self._connectivity,
            preview_mode=self._preview_mode,
            background_style=self._background_style,
            checkerboard_size=self._checkerboard_size,
            output_raw_filename=self._output_raw_filename,
            output_clean_filename=self._output_clean_filename,
            last_export_directory=self._last_export_directory,
        )

    def _push_history(self, reset: bool = False) -> None:
        snapshot = self._current_snapshot()
        if reset:
            self._history = [snapshot]
            self._history_index = 0
            return

        if self._history and self._history_index >= 0 and self._history[self._history_index] == snapshot:
            return

        if self._history_index < len(self._history) - 1:
            self._history = self._history[: self._history_index + 1]

        self._history.append(snapshot)
        if len(self._history) > 50:
            self._history.pop(0)
        self._history_index = len(self._history) - 1

    def undo(self) -> None:
        if self._history_index <= 0:
            return
        self._history_index -= 1
        self._restore_snapshot(self._history[self._history_index])

    def redo(self) -> None:
        if self._history_index >= len(self._history) - 1:
            return
        self._history_index += 1
        self._restore_snapshot(self._history[self._history_index])

    def _restore_snapshot(self, snapshot: EditorSnapshot) -> None:
        self._background_rgba = snapshot.background_rgba
        self._tolerance_ui = snapshot.tolerance_ui
        self._connected_background_only = snapshot.connected_background_only
        self._connectivity = snapshot.connectivity
        self._preview_mode = snapshot.preview_mode
        self._background_style = snapshot.background_style
        self._checkerboard_size = snapshot.checkerboard_size
        self._output_raw_filename = snapshot.output_raw_filename
        self._output_clean_filename = snapshot.output_clean_filename
        self._last_export_directory = snapshot.last_export_directory
        self._restore_controls_from_state()
        self._refresh_processing()

    def save_project(self) -> None:
        if self._source_image is None or self._crop_rect is None:
            self._show_error("Nothing to save", "Open a PNG and select a crop before saving a project.")
            return

        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            str(Path.cwd() / "config" / "project.json"),
            "JSON Files (*.json)",
        )
        if not file_name:
            return

        config = self._build_config()
        try:
            save_config(config, file_name)
            self.statusBar().showMessage(f"Saved project: {file_name}", 4000)
        except OSError as exc:
            self._show_error("Could not save project", str(exc))

    def load_project(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Load Project",
            str(Path.cwd() / "config"),
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_name:
            return

        try:
            config = load_config(file_name)
        except ConfigError as exc:
            self._show_error("Could not load project", str(exc))
            return

        self._apply_config(config)

    def _apply_config(self, config: CropConfig) -> None:
        self._background_rgba = config.background_rgba
        self._tolerance_ui = clamp_int(config.tolerance_ui, 0, 100)
        self._connected_background_only = config.connected_background_only
        self._connectivity = 8 if config.connectivity == 8 else 4
        self._preview_mode = "after"
        self._background_style = "checkerboard"
        self._checkerboard_size = "medium"
        self._output_raw_filename = config.output_raw_filename
        self._output_clean_filename = config.output_clean_filename
        self._last_export_directory = config.export_directory
        self._restore_controls_from_state()

        source_path = Path(config.source_image) if config.source_image else None
        if source_path is not None and source_path.exists():
            self.load_image(source_path, reset_project=False)
            self._crop_rect = config.crop_rect
            self.canvas.set_crop_rect(QRectF(config.crop_rect.x, config.crop_rect.y, config.crop_rect.width, config.crop_rect.height))
            try:
                self._raw_crop = crop_image(self._source_image, config.crop_rect)
            except Exception as exc:
                self._show_error("Could not apply project crop", str(exc))
                return
            self._refresh_processing()
            self.statusBar().showMessage(f"Loaded project: {source_path.name}", 4000)
        else:
            self._source_path = None
            self._source_image = None
            self._raw_crop = None
            self._clean_crop = None
            self._removal_result = None
            self.canvas.set_image(self._source_to_pixmap(Image.new("RGBA", (1, 1), (0, 0, 0, 0))))
            self.canvas.set_crop_rect(None)
            self.preview_view.set_images(None, None)
            self._update_status()
            self._show_warning("Project loaded without a source image path.")

        self._push_history(reset=True)

    def _build_config(self) -> CropConfig:
        if self._source_path is None or self._crop_rect is None:
            raise ConfigError("No source image or crop rectangle available.")
        return CropConfig(
            source_image=str(self._source_path),
            crop_rect=self._crop_rect,
            background_rgba=self._background_rgba,
            tolerance_ui=self._tolerance_ui,
            tolerance_threshold=ui_tolerance_to_distance(self._tolerance_ui),
            connected_background_only=self._connected_background_only,
            connectivity=self._connectivity,
            output_raw_filename=self._output_raw_filename,
            output_clean_filename=self._output_clean_filename,
            export_directory=self._last_export_directory,
            config_version=2,
        )

    def export_raw(self) -> None:
        if self._raw_crop is None:
            self._show_error("Nothing to export", "Open a PNG and select a crop first.")
            return
        destination = self._prompt_export_path(self._output_raw_filename or "crop_raw.png")
        if destination is None:
            return
        self._export_image(self._raw_crop, destination, "raw")

    def export_clean(self) -> None:
        if self._clean_crop is None:
            self._show_error("Nothing to export", "Open a PNG, select a crop, and configure background removal first.")
            return
        destination = self._prompt_export_path(self._output_clean_filename or "crop_clean.png")
        if destination is None:
            return
        self._export_image(self._clean_crop, destination, "clean")

    def _prompt_export_path(self, default_name: str) -> Path | None:
        start_dir = self._last_export_directory or str(Path.cwd() / "output")
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export PNG",
            str(Path(start_dir) / default_name),
            "PNG Files (*.png)",
        )
        if not file_name:
            return None
        return Path(file_name)

    def _export_image(self, image: Image.Image, destination: Path, kind: str) -> None:
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
        try:
            export_png(image, destination)
            self._last_export_directory = str(destination.parent)
            if kind == "raw":
                self._output_raw_filename = destination.name
            else:
                self._output_clean_filename = destination.name
            self._restore_controls_from_state()
            self._update_status()
            self.statusBar().showMessage(f"Exported {destination.name}", 4000)
        except OSError as exc:
            self._show_error("Could not export image", str(exc))

    def _refresh_all(self) -> None:
        self._refresh_processing()
        self._restore_controls_from_state()
        self._update_status()

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _show_warning(self, message: str) -> None:
        self.warning_label.setText(message)
        QMessageBox.warning(self, "Warning", message)
