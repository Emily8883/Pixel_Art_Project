from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..manual_editing import ManualEditDocument
from .pixel_editor_view import PixelEditorView


class ManualCleanupWidget(QWidget):
    toolChanged = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.editor = PixelEditorView()
        self._document: ManualEditDocument | None = None
        self._recent_colors: list[tuple[int, int, int, int]] = []

        self.tool_combo = QComboBox()
        self.tool_combo.addItems(["select", "pencil", "eraser", "picker", "fill"])
        self.tool_combo.currentTextChanged.connect(self._set_tool)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["raw", "auto", "final"])
        self.mode_combo.currentTextChanged.connect(self.editor.set_display_mode)

        self.brush_size_spin = QSpinBox()
        self.brush_size_spin.setRange(1, 5)
        self.brush_size_spin.setValue(1)
        self.brush_size_spin.valueChanged.connect(self.editor.set_brush_size)

        self.grid_checkbox = QCheckBox("Show pixel grid")
        self.grid_checkbox.setChecked(True)
        self.grid_checkbox.toggled.connect(self.editor.set_grid_enabled)

        self.alpha_checkbox = QCheckBox("Alpha Preview")
        self.edge_checkbox = QCheckBox("Edge Highlight")
        self.halo_checkbox = QCheckBox("Suspected Halo Highlight")
        self.isolated_checkbox = QCheckBox("Isolated Pixel Highlight")
        self.semi_checkbox = QCheckBox("Semi-Transparent Highlight")
        for checkbox in (self.alpha_checkbox, self.edge_checkbox, self.halo_checkbox, self.isolated_checkbox, self.semi_checkbox):
            checkbox.toggled.connect(self._update_overlays)

        self.r_spin = self._make_channel_spin()
        self.g_spin = self._make_channel_spin()
        self.b_spin = self._make_channel_spin()
        self.a_spin = self._make_channel_spin()
        for spin in (self.r_spin, self.g_spin, self.b_spin, self.a_spin):
            spin.valueChanged.connect(self._sync_color_controls)

        self.hex_edit = QLineEdit("#FFFFFFFF")
        self.hex_edit.editingFinished.connect(self._hex_edited)
        self.swatch = QLabel()
        self.swatch.setFixedSize(40, 24)
        self.swatch.setAutoFillBackground(True)

        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(self._undo)
        self.redo_button = QPushButton("Redo")
        self.redo_button.clicked.connect(self._redo)
        self.reset_button = QPushButton("Reset Manual Edits")
        self.reset_button.clicked.connect(self._reset_manual_edits)

        self.cursor_label = QLabel("Cursor: -")
        self.status_label = QLabel("Tool: select | Brush: 1 | Zoom: 100%")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Tool"))
        row.addWidget(self.tool_combo)
        row.addWidget(QLabel("Mode"))
        row.addWidget(self.mode_combo)
        row.addWidget(QLabel("Brush"))
        row.addWidget(self.brush_size_spin)
        layout.addLayout(row)
        layout.addWidget(self.editor, 1)

        form = QFormLayout()
        form.addRow("R", self.r_spin)
        form.addRow("G", self.g_spin)
        form.addRow("B", self.b_spin)
        form.addRow("A", self.a_spin)
        form.addRow("Hex", self.hex_edit)
        layout.addLayout(form)
        layout.addWidget(self.swatch)

        overlay_row = QGridLayout()
        overlay_row.addWidget(self.grid_checkbox, 0, 0)
        overlay_row.addWidget(self.alpha_checkbox, 0, 1)
        overlay_row.addWidget(self.edge_checkbox, 1, 0)
        overlay_row.addWidget(self.halo_checkbox, 1, 1)
        overlay_row.addWidget(self.isolated_checkbox, 2, 0)
        overlay_row.addWidget(self.semi_checkbox, 2, 1)
        layout.addLayout(overlay_row)

        button_row = QHBoxLayout()
        button_row.addWidget(self.undo_button)
        button_row.addWidget(self.redo_button)
        button_row.addWidget(self.reset_button)
        layout.addLayout(button_row)
        layout.addWidget(self.cursor_label)
        layout.addWidget(self.status_label)

        self.editor.cursorInfoChanged.connect(self._on_cursor_info)
        self.editor.selectionChanged.connect(self._on_selection_changed)
        self.editor.zoomChanged.connect(self._on_zoom_changed)
        self._sync_color_controls()

    def set_document(self, document: ManualEditDocument | None) -> None:
        self._document = document
        self.editor.set_document(document)
        self._update_status()

    def document(self) -> ManualEditDocument | None:
        return self._document

    def set_background_color(self, rgba: tuple[int, int, int, int] | None) -> None:
        if self._document is not None:
            self._document.background_rgba = rgba

    def current_color(self) -> tuple[int, int, int, int]:
        return (self.r_spin.value(), self.g_spin.value(), self.b_spin.value(), self.a_spin.value())

    def _make_channel_spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 255)
        return spin

    def _set_tool(self, tool: str) -> None:
        self.editor.set_tool(tool)
        self.toolChanged.emit(tool)
        self._update_status()

    def _update_overlays(self) -> None:
        self.editor.set_overlay_flags(
            alpha=self.alpha_checkbox.isChecked(),
            edge=self.edge_checkbox.isChecked(),
            halo=self.halo_checkbox.isChecked(),
            isolated=self.isolated_checkbox.isChecked(),
            semi=self.semi_checkbox.isChecked(),
        )

    def _sync_color_controls(self, *_args) -> None:
        rgba = self.current_color()
        self.editor.set_foreground_color(rgba)
        self.hex_edit.blockSignals(True)
        self.hex_edit.setText("#%02X%02X%02X%02X" % rgba)
        self.hex_edit.blockSignals(False)
        palette = self.swatch.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(*rgba))
        self.swatch.setPalette(palette)
        self.swatch.setAutoFillBackground(True)

    def _hex_edited(self) -> None:
        text = self.hex_edit.text().strip().lstrip("#")
        if len(text) == 6:
            text += "FF"
        if len(text) != 8:
            return
        try:
            rgba = tuple(int(text[index : index + 2], 16) for index in range(0, 8, 2))
        except ValueError:
            return
        for spin, value in zip((self.r_spin, self.g_spin, self.b_spin, self.a_spin), rgba):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._sync_color_controls()

    def _on_cursor_info(self, info) -> None:
        self.cursor_label.setText(f"Cursor: ({info.x}, {info.y}) RGBA {info.rgba}")

    def _on_selection_changed(self, rect) -> None:
        self.status_label.setText(f"Tool: {self.editor.current_tool()} | Brush: {self.brush_size_spin.value()} | Zoom: {self.editor.zoom_percent()}%")

    def _on_zoom_changed(self, zoom: int) -> None:
        self._update_status()

    def _update_status(self) -> None:
        self.status_label.setText(f"Tool: {self.editor.current_tool()} | Brush: {self.brush_size_spin.value()} | Zoom: {self.editor.zoom_percent()}%")

    def _undo(self) -> None:
        if self._document is not None and self._document.undo():
            self.editor.set_document(self._document)

    def _redo(self) -> None:
        if self._document is not None and self._document.redo():
            self.editor.set_document(self._document)

    def _reset_manual_edits(self) -> None:
        if self._document is not None:
            self._document.reset_manual_edits()
            self.editor.set_document(self._document)

