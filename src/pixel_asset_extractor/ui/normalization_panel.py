from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..image_tools import pil_image_to_qpixmap
from ..manual_editing import ManualEditDocument
from ..naming import generate_normalized_filename
from ..normalization import (
    ANCHOR_MODES,
    OUTPUT_PRESETS,
    SCALE_MODES,
    alignment_diagnostics,
    copy_normalization_from_group_leader,
    copy_normalization_from_previous_frame,
    detect_bottommost_visible_pixel,
    scale_size_for_mode,
    suggest_contact_point,
    transparent_bounds,
)
from ..project_model import AssetRecord


class NormalizationPreviewView(QGraphicsView):
    offsetChanged = Signal(int, int)
    baselineChanged = Signal(int)
    contactChanged = Signal(int, int)
    pivotChanged = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setMouseTracking(True)
        self.setBackgroundBrush(QColor(32, 32, 32))
        self._result = None
        self._settings = None
        self._asset = None
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._zoom_percent = 100
        self._panning = False
        self._pan_start = None
        self._drag_sprite = False
        self._drag_baseline = False
        self._set_contact_mode = False
        self._set_pivot_mode = False
        self._overlay_flags = {
            "canvas": True,
            "bounds": True,
            "scaled": True,
            "padding": False,
            "center_x": False,
            "center_y": False,
            "baseline": True,
            "contact": True,
            "pivot": True,
            "clipping": True,
            "grid": False,
        }

    def set_result(self, result, settings, asset=None) -> None:
        self._result = result
        self._settings = settings
        self._asset = asset
        scene = self.scene()
        scene.clear()
        self._pixmap_item = None
        if result is not None:
            self._pixmap_item = scene.addPixmap(pil_image_to_qpixmap(result.image))
            self._pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
            scene.setSceneRect(QRectF(0, 0, result.image.width, result.image.height))
        self._apply_zoom()
        self.viewport().update()

    def set_zoom_percent(self, zoom_percent: int) -> None:
        self._zoom_percent = max(100, min(6400, int(zoom_percent)))
        self._apply_zoom()

    def zoom_percent(self) -> int:
        return self._zoom_percent

    def set_overlay_flags(self, **flags) -> None:
        self._overlay_flags.update({key: bool(value) for key, value in flags.items() if key in self._overlay_flags})
        self.viewport().update()

    def enable_contact_mode(self, enabled: bool) -> None:
        self._set_contact_mode = enabled
        self._set_pivot_mode = False

    def enable_pivot_mode(self, enabled: bool) -> None:
        self._set_pivot_mode = enabled
        self._set_contact_mode = False

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() == 0:
            return
        delta = 25 if event.angleDelta().y() > 0 else -25
        self.set_zoom_percent(self._zoom_percent + delta)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.SpaceModifier:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._result is None or self._settings is None:
            super().mousePressEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        x = int(round(scene_pos.x()))
        y = int(round(scene_pos.y()))
        sprite_rect = QRectF(*self._result.placed_rect)
        baseline_y = self._settings.baseline_y
        if self._set_contact_mode and self._canvas_contains(x, y):
            self.contactChanged.emit(x, y)
            event.accept()
            return
        if self._set_pivot_mode and self._canvas_contains(x, y):
            self.pivotChanged.emit(x, y)
            event.accept()
            return
        if sprite_rect.contains(scene_pos):
            self._drag_sprite = True
            self._drag_start = (x, y)
            self._drag_offset_start = (self._settings.offset_x, self._settings.offset_y)
            event.accept()
            return
        if baseline_y is not None and abs(y - baseline_y) <= 2:
            self._drag_baseline = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_start is not None:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._result is None or self._settings is None:
            super().mouseMoveEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        x = int(round(scene_pos.x()))
        y = int(round(scene_pos.y()))
        if self._drag_sprite:
            dx = x - self._drag_start[0]
            dy = y - self._drag_start[1]
            self.offsetChanged.emit(self._drag_offset_start[0] + dx, self._drag_offset_start[1] + dy)
            event.accept()
            return
        if self._drag_baseline:
            self.baselineChanged.emit(y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_sprite:
            self._drag_sprite = False
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_baseline:
            self._drag_baseline = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._settings is None:
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ControlModifier else 5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx = -step if event.key() == Qt.Key.Key_Left else step if event.key() == Qt.Key.Key_Right else 0
            dy = -step if event.key() == Qt.Key.Key_Up else step if event.key() == Qt.Key.Key_Down else 0
            self.offsetChanged.emit(self._settings.offset_x + dx, self._settings.offset_y + dy)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Home:
            self.offsetChanged.emit(0, self._settings.offset_y)
            event.accept()
            return
        if event.key() == Qt.Key.Key_End:
            self.baselineChanged.emit(self._settings.baseline_y or 0)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.enable_contact_mode(False)
            self.enable_pivot_mode(False)
            self._drag_sprite = False
            self._drag_baseline = False
            event.accept()
            return
        super().keyPressEvent(event)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        if self._result is None or self._settings is None:
            return
        painter.save()
        if self._overlay_flags["canvas"]:
            painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
            painter.drawRect(QRectF(0, 0, self._settings.output_width - 1, self._settings.output_height - 1))
        if self._overlay_flags["bounds"]:
            bounds = self._result.content_bounds
            painter.setPen(QPen(QColor(0, 255, 255, 140), 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(bounds.left, bounds.top, bounds.content_width, bounds.content_height))
        if self._overlay_flags["scaled"]:
            painter.setPen(QPen(QColor(255, 200, 0, 140), 1, Qt.PenStyle.DashDotLine))
            x, y, w, h = self._result.placed_rect
            painter.drawRect(QRectF(x, y, w, h))
        if self._overlay_flags["center_x"]:
            painter.setPen(QPen(QColor(120, 120, 255, 120), 1))
            painter.drawLine(self._settings.output_width / 2, 0, self._settings.output_width / 2, self._settings.output_height)
        if self._overlay_flags["center_y"]:
            painter.setPen(QPen(QColor(120, 120, 255, 120), 1))
            painter.drawLine(0, self._settings.output_height / 2, self._settings.output_width, self._settings.output_height / 2)
        if self._overlay_flags["baseline"] and self._settings.baseline_y is not None:
            painter.setPen(QPen(QColor(255, 120, 0, 220), 1))
            painter.drawLine(0, self._settings.baseline_y, self._settings.output_width, self._settings.baseline_y)
        contact_x = getattr(self._asset, "contact_x", None)
        contact_y = getattr(self._asset, "contact_y", None)
        if self._overlay_flags["contact"] and contact_x is not None and contact_y is not None:
            painter.setPen(QPen(QColor(255, 0, 255, 255), 1))
            painter.drawEllipse(QPointF(contact_x, contact_y), 2, 2)
        if self._overlay_flags["pivot"] and self._settings.pivot_x is not None and self._settings.pivot_y is not None:
            painter.setPen(QPen(QColor(0, 255, 0, 255), 1))
            painter.drawLine(self._settings.pivot_x - 3, self._settings.pivot_y, self._settings.pivot_x + 3, self._settings.pivot_y)
            painter.drawLine(self._settings.pivot_x, self._settings.pivot_y - 3, self._settings.pivot_x, self._settings.pivot_y + 3)
        painter.restore()

    def _apply_zoom(self) -> None:
        self.resetTransform()
        self.scale(self._zoom_percent / 100.0, self._zoom_percent / 100.0)

    def _canvas_contains(self, x: int, y: int) -> bool:
        if self._settings is None:
            return False
        return 0 <= x < self._settings.output_width and 0 <= y < self._settings.output_height


class NormalizationInspectorWidget(QWidget):
    changed = Signal()
    exportRequested = Signal()
    compareRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._asset: AssetRecord | None = None
        self._manager = None
        self._project_path = None
        self._document: ManualEditDocument | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._apply_to_asset)
        self._programmatic = False
        self._last_result = None
        self._last_warnings: list[tuple[str, str]] = []

        self._build_ui()

    def _build_ui(self) -> None:
        self.toggle_button = QToolButton()
        self.toggle_button.setText("Normalization")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.ArrowType.DownArrow)
        self.toggle_button.toggled.connect(self._set_collapsed)

        header = QHBoxLayout()
        header.addWidget(self.toggle_button)
        header.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        inner = QWidget()
        self.scroll.setWidget(inner)
        layout = QVBoxLayout(inner)

        self.preview = NormalizationPreviewView()
        self.preview.setMinimumHeight(280)
        self.preview.offsetChanged.connect(self._set_offset_from_preview)
        self.preview.baselineChanged.connect(self._set_baseline_from_preview)
        self.preview.contactChanged.connect(self._set_contact_from_preview)
        self.preview.pivotChanged.connect(self._set_pivot_from_preview)

        self.enabled_checkbox = QCheckBox("Enable Normalization")
        self.enabled_checkbox.toggled.connect(self._queue_apply)

        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["16x16", "24x24", "32x32", "48x48", "64x64", "96x96", "128x128", "Custom"])
        self.preset_combo.currentTextChanged.connect(self._preset_changed)

        self.width_spin = self._make_spin()
        self.height_spin = self._make_spin()
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItems(list(SCALE_MODES))
        self.scale_mode_combo.currentTextChanged.connect(self._scale_mode_changed)
        self.scale_percent_spin = self._make_spin(1, 6400, 100)
        self.target_width_spin = self._make_spin()
        self.target_height_spin = self._make_spin()
        self.preserve_aspect_checkbox = QCheckBox("Preserve Aspect Ratio")
        self.trim_checkbox = QCheckBox("Trim Transparent Padding")
        self.minimum_padding_spin = self._make_spin(0, 999, 2)
        self.anchor_combo = QComboBox()
        self.anchor_combo.addItems(list(ANCHOR_MODES))
        self.baseline_spin = self._make_spin(0, 4096, 45)
        self.lock_baseline_checkbox = QCheckBox("Lock Baseline")
        self.offset_x_spin = self._make_spin(-4096, 4096, 0)
        self.offset_y_spin = self._make_spin(-4096, 4096, 0)
        self.pivot_x_spin = self._make_spin(-4096, 4096, 24)
        self.pivot_y_spin = self._make_spin(-4096, 4096, 45)
        self.contact_x_spin = self._make_spin(-4096, 4096, 0)
        self.contact_y_spin = self._make_spin(-4096, 4096, 0)
        self.allow_overflow_checkbox = QCheckBox("Allow Overflow")
        self.include_shadow_checkbox = QCheckBox("Include Ground Shadow")
        self.shadow_separate_checkbox = QCheckBox("Shadow Should Be Separate")
        self.group_leader_checkbox = QCheckBox("Group Leader")
        self.group_edit = QLineEdit()
        self.filename_edit = QLineEdit()
        self.show_canvas_thumbnail = QCheckBox("Show normalized canvas")
        self.show_canvas_thumbnail.setChecked(False)
        self.show_canvas_thumbnail.toggled.connect(self._queue_apply)

        for widget in (
            self.enabled_checkbox,
            self.width_spin,
            self.height_spin,
            self.scale_mode_combo,
            self.scale_percent_spin,
            self.target_width_spin,
            self.target_height_spin,
            self.preserve_aspect_checkbox,
            self.trim_checkbox,
            self.minimum_padding_spin,
            self.anchor_combo,
            self.offset_x_spin,
            self.offset_y_spin,
            self.baseline_spin,
            self.lock_baseline_checkbox,
            self.pivot_x_spin,
            self.pivot_y_spin,
            self.allow_overflow_checkbox,
            self.include_shadow_checkbox,
            self.shadow_separate_checkbox,
            self.group_leader_checkbox,
            self.group_edit,
            self.filename_edit,
        ):
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self._queue_apply)
            if hasattr(widget, "toggled"):
                widget.toggled.connect(self._queue_apply)
            if hasattr(widget, "currentTextChanged"):
                widget.currentTextChanged.connect(self._queue_apply)
            if hasattr(widget, "editingFinished"):
                widget.editingFinished.connect(self._queue_apply)

        self.warning_label = QLabel("No warnings")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #cc3b3b;")
        self.canvas_info = QLabel("")
        self.canvas_info.setWordWrap(True)

        buttons = QGridLayout()
        self.reset_button = QPushButton("Reset Normalization")
        self.fit_button = QPushButton("Fit Sprite Inside Canvas")
        self.center_x_button = QPushButton("Center Horizontally")
        self.align_bottom_button = QPushButton("Align Bottom to Baseline")
        self.detect_bottom_button = QPushButton("Detect Bottommost Visible Pixel")
        self.set_contact_button = QPushButton("Set Contact Point")
        self.set_pivot_button = QPushButton("Set Pivot Point")
        self.apply_selected_button = QPushButton("Apply to Selected Assets")
        self.apply_matching_button = QPushButton("Apply to Matching Animation")
        self.compare_button = QPushButton("Compare Alignment")
        self.export_button = QPushButton("Export Normalized PNG")
        for button in (
            self.reset_button,
            self.fit_button,
            self.center_x_button,
            self.align_bottom_button,
            self.detect_bottom_button,
            self.set_contact_button,
            self.set_pivot_button,
            self.apply_selected_button,
            self.apply_matching_button,
            self.compare_button,
            self.export_button,
        ):
            button.clicked.connect(self._queue_apply)

        form = QFormLayout()
        form.addRow("Output Preset", self.preset_combo)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Scale Mode", self.scale_mode_combo)
        form.addRow("Scale Percent", self.scale_percent_spin)
        form.addRow("Target Sprite Width", self.target_width_spin)
        form.addRow("Target Sprite Height", self.target_height_spin)
        form.addRow("Preserve Aspect Ratio", self.preserve_aspect_checkbox)
        form.addRow("Trim Transparent Padding", self.trim_checkbox)
        form.addRow("Minimum Padding", self.minimum_padding_spin)
        form.addRow("Anchor Mode", self.anchor_combo)
        form.addRow("Offset X", self.offset_x_spin)
        form.addRow("Offset Y", self.offset_y_spin)
        form.addRow("Baseline Y", self.baseline_spin)
        form.addRow("Lock Baseline", self.lock_baseline_checkbox)
        form.addRow("Pivot X", self.pivot_x_spin)
        form.addRow("Pivot Y", self.pivot_y_spin)
        form.addRow("Contact X", self.contact_x_spin)
        form.addRow("Contact Y", self.contact_y_spin)
        form.addRow("Alignment Group", self.group_edit)
        form.addRow("Normalized Filename", self.filename_edit)
        form.addRow("Group Leader", self.group_leader_checkbox)
        form.addRow("Allow Overflow", self.allow_overflow_checkbox)
        form.addRow("Include Ground Shadow", self.include_shadow_checkbox)
        form.addRow("Shadow Separate", self.shadow_separate_checkbox)
        form.addRow("Show Canvas Thumbnail", self.show_canvas_thumbnail)
        form.addRow("Canvas Info", self.canvas_info)

        layout.addWidget(self.preview)
        layout.addLayout(form)
        layout.addWidget(self.warning_label)
        layout.addLayout(buttons)
        buttons.addWidget(self.reset_button, 0, 0)
        buttons.addWidget(self.fit_button, 0, 1)
        buttons.addWidget(self.center_x_button, 0, 2)
        buttons.addWidget(self.align_bottom_button, 1, 0)
        buttons.addWidget(self.detect_bottom_button, 1, 1)
        buttons.addWidget(self.set_contact_button, 1, 2)
        buttons.addWidget(self.set_pivot_button, 2, 0)
        buttons.addWidget(self.apply_selected_button, 2, 1)
        buttons.addWidget(self.apply_matching_button, 2, 2)
        buttons.addWidget(self.compare_button, 3, 0)
        buttons.addWidget(self.export_button, 3, 1)

        self.scroll_layout = QVBoxLayout(self)
        self.scroll_layout.addLayout(header)
        self.scroll_layout.addWidget(self.scroll)
        self._set_collapsed(True)
        self._connect_buttons()

    def _connect_buttons(self) -> None:
        self.reset_button.clicked.connect(self.reset_normalization)
        self.fit_button.clicked.connect(self.fit_sprite_inside_canvas)
        self.center_x_button.clicked.connect(self.center_horizontally)
        self.align_bottom_button.clicked.connect(self.align_bottom_to_baseline)
        self.detect_bottom_button.clicked.connect(self.detect_bottommost_visible_pixel)
        self.set_contact_button.clicked.connect(lambda: self.preview.enable_contact_mode(True))
        self.set_pivot_button.clicked.connect(lambda: self.preview.enable_pivot_mode(True))
        self.apply_selected_button.clicked.connect(self.apply_to_selected_assets)
        self.apply_matching_button.clicked.connect(self.apply_to_matching_animation)
        self.compare_button.clicked.connect(self.compare_alignment)
        self.export_button.clicked.connect(self.export_normalized_requested)

    def set_context(self, manager, asset: AssetRecord | None, project_path=None) -> None:
        self._manager = manager
        self._asset = asset
        self._project_path = project_path
        self._load_from_asset()

    def current_asset(self) -> AssetRecord | None:
        return self._asset

    def current_result(self):
        return self._last_result

    def warnings(self) -> list[tuple[str, str]]:
        return list(self._last_warnings)

    def _load_from_asset(self) -> None:
        asset = self._asset
        if asset is None or self._manager is None:
            self._programmatic = True
            self.enabled_checkbox.setChecked(False)
            self._programmatic = False
            self.preview.set_result(None, None)
            self.warning_label.setText("No active asset")
            return
        settings = asset.normalization
        self._programmatic = True
        self.enabled_checkbox.setChecked(settings.enabled)
        self.width_spin.setValue(settings.output_width)
        self.height_spin.setValue(settings.output_height)
        self.scale_mode_combo.setCurrentText(settings.scale_mode)
        self.scale_percent_spin.setValue(settings.scale_percent)
        self.target_width_spin.setValue(settings.target_sprite_width)
        self.target_height_spin.setValue(settings.target_sprite_height)
        self.preserve_aspect_checkbox.setChecked(settings.preserve_aspect_ratio)
        self.trim_checkbox.setChecked(settings.trim_transparent_before_placement)
        self.minimum_padding_spin.setValue(settings.minimum_padding)
        self.anchor_combo.setCurrentText(settings.anchor_mode)
        self.offset_x_spin.setValue(settings.offset_x)
        self.offset_y_spin.setValue(settings.offset_y)
        self.baseline_spin.setValue(settings.baseline_y)
        self.pivot_x_spin.setValue(settings.pivot_x)
        self.pivot_y_spin.setValue(settings.pivot_y)
        self.contact_x_spin.setValue(asset.contact_x or 0)
        self.contact_y_spin.setValue(asset.contact_y or 0)
        self.group_edit.setText(asset.alignment_group)
        self.filename_edit.setText(settings.normalized_output_filename)
        self.group_leader_checkbox.setChecked(asset.is_alignment_group_leader)
        self.allow_overflow_checkbox.setChecked(settings.allow_overflow)
        self.include_shadow_checkbox.setChecked(asset.includes_ground_shadow)
        self.shadow_separate_checkbox.setChecked(asset.shadow_should_be_separate)
        self._programmatic = False
        self._queue_apply()

    def _asset_document(self) -> ManualEditDocument | None:
        if self._asset is None:
            return None
        try:
            raw = self._manager._raw_crop_for_asset(self._asset)
            clean = self._manager._apply_cleaning(raw, self._asset.background_removal) if raw is not None else None
        except Exception:
            return None
        if raw is None or clean is None:
            return None
        return self._manager._manual_document_for_asset(self._asset, raw, clean.cleaned_image)

    def _queue_apply(self, *_args) -> None:
        if self._programmatic:
            return
        self._refresh_timer.start(100)

    def _apply_to_asset(self) -> None:
        if self._asset is None or self._manager is None:
            return
        settings = self._asset.normalization
        settings.enabled = self.enabled_checkbox.isChecked()
        settings.output_width = self.width_spin.value()
        settings.output_height = self.height_spin.value()
        settings.scale_mode = self.scale_mode_combo.currentText()
        settings.scale_percent = self.scale_percent_spin.value()
        settings.target_sprite_width = self.target_width_spin.value()
        settings.target_sprite_height = self.target_height_spin.value()
        settings.preserve_aspect_ratio = self.preserve_aspect_checkbox.isChecked()
        settings.trim_transparent_before_placement = self.trim_checkbox.isChecked()
        settings.minimum_padding = self.minimum_padding_spin.value()
        settings.anchor_mode = self.anchor_combo.currentText()
        settings.offset_x = self.offset_x_spin.value()
        settings.offset_y = self.offset_y_spin.value()
        settings.baseline_y = self.baseline_spin.value()
        settings.pivot_x = self.pivot_x_spin.value()
        settings.pivot_y = self.pivot_y_spin.value()
        settings.allow_overflow = self.allow_overflow_checkbox.isChecked()
        settings.normalized_output_filename = self.filename_edit.text().strip() or generate_normalized_filename(
            self._asset.character_group or self._asset.display_name,
            self._asset.category,
            self._asset.action,
            self._asset.direction,
            self._asset.frame_number,
            self._asset.variant,
            canvas_size=(settings.output_width, settings.output_height),
        )
        self._asset.alignment_group = self.group_edit.text().strip()
        self._asset.is_alignment_group_leader = self.group_leader_checkbox.isChecked()
        self._asset.contact_x = self.contact_x_spin.value() if self.contact_x_spin.value() or self.contact_y_spin.value() else None
        self._asset.contact_y = self.contact_y_spin.value() if self.contact_x_spin.value() or self.contact_y_spin.value() else None
        self._asset.includes_ground_shadow = self.include_shadow_checkbox.isChecked()
        self._asset.shadow_should_be_separate = self.shadow_separate_checkbox.isChecked()
        if self._manager is not None:
            self._manager.edit_asset(self._asset.asset_uuid, normalization=settings)
            self._manager.project.mark_modified()
        self.refresh_preview()
        self.changed.emit()

    def refresh_preview(self) -> None:
        if self._asset is None or self._manager is None:
            self.preview.set_result(None, None)
            return
        result = self._manager._normalized_output_for_asset(self._asset)
        self._last_result = result
        self.preview.set_result(result, self._asset.normalization, self._asset)
        self._last_warnings = [("warning", warning) for warning in result.warnings]
        if result.content_bounds.is_empty:
            self._last_warnings.append(("error", "Final Edited image is fully transparent"))
        if result.clipped_pixels > 0:
            self._last_warnings.append(("warning", f"Visible pixels clipped: {result.clipped_pixels}"))
        if self._asset.normalization.output_width < 1 or self._asset.normalization.output_height < 1:
            self._last_warnings.append(("error", "Invalid canvas dimensions"))
        if result.scaled_content_size[0] < 16 or result.scaled_content_size[1] < 16:
            self._last_warnings.append(("warning", "Output sprite smaller than 16 pixels in width or height"))
        self.warning_label.setText(" | ".join(message for _, message in self._last_warnings) or "No warnings")
        self.canvas_info.setText(f"{self._asset.normalization.output_width}x{self._asset.normalization.output_height}")
        self._update_field_state()
        self.preview.viewport().update()

    def _update_field_state(self) -> None:
        mode = self.scale_mode_combo.currentText()
        percent_enabled = mode == "percent"
        target_enabled = mode in {"fit_inside", "fit_width", "fit_height", "exact_dimensions"}
        self.scale_percent_spin.setEnabled(percent_enabled)
        self.target_width_spin.setEnabled(target_enabled)
        self.target_height_spin.setEnabled(target_enabled)
        if self.preset_combo.currentText() == "Custom":
            self.width_spin.setEnabled(True)
            self.height_spin.setEnabled(True)

    def _set_collapsed(self, collapsed: bool) -> None:
        self.scroll.setVisible(not collapsed)
        self.toggle_button.setArrowType(Qt.ArrowType.RightArrow if collapsed else Qt.ArrowType.DownArrow)

    def _preset_changed(self, text: str) -> None:
        if self._programmatic:
            return
        presets = {
            "16x16": (16, 16),
            "24x24": (24, 24),
            "32x32": (32, 32),
            "48x48": (48, 48),
            "64x64": (64, 64),
            "96x96": (96, 96),
            "128x128": (128, 128),
        }
        if text in presets:
            width, height = presets[text]
            self._programmatic = True
            self.width_spin.setValue(width)
            self.height_spin.setValue(height)
            self._programmatic = False
            self._queue_apply()
        self._update_field_state()

    def _scale_mode_changed(self, *_args) -> None:
        self._update_field_state()
        self._queue_apply()

    def _make_spin(self, minimum: int = 1, maximum: int = 4096, value: int = 1) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _set_offset_from_preview(self, x: int, y: int) -> None:
        self._programmatic = True
        self.offset_x_spin.setValue(x)
        self.offset_y_spin.setValue(y)
        self._programmatic = False
        self._queue_apply()

    def _set_baseline_from_preview(self, y: int) -> None:
        if self.lock_baseline_checkbox.isChecked():
            return
        self._programmatic = True
        self.baseline_spin.setValue(max(0, y))
        self._programmatic = False
        self._queue_apply()

    def _set_contact_from_preview(self, x: int, y: int) -> None:
        self._programmatic = True
        self.contact_x_spin.setValue(x)
        self.contact_y_spin.setValue(y)
        self._programmatic = False
        self._queue_apply()

    def _set_pivot_from_preview(self, x: int, y: int) -> None:
        self._programmatic = True
        self.pivot_x_spin.setValue(x)
        self.pivot_y_spin.setValue(y)
        self._programmatic = False
        self._queue_apply()

    def reset_normalization(self) -> None:
        if self._asset is None:
            return
        self._asset.normalization.reset_defaults()
        self._asset.normalization.normalized_output_filename = generate_normalized_filename(
            self._asset.character_group or self._asset.display_name,
            self._asset.category,
            self._asset.action,
            self._asset.direction,
            self._asset.frame_number,
            self._asset.variant,
            canvas_size=(self._asset.normalization.output_width, self._asset.normalization.output_height),
        )
        self._load_from_asset()

    def fit_sprite_inside_canvas(self) -> None:
        if self._asset is None or self._manager is None:
            return
        raw = self._manager._raw_crop_for_asset(self._asset)
        if raw is None:
            return
        bounds = transparent_bounds(self._manager._manual_document_for_asset(self._asset, raw, self._manager._apply_cleaning(raw, self._asset.background_removal).cleaned_image).final_image())
        size, warnings = scale_size_for_mode((bounds.content_width or raw.width, bounds.content_height or raw.height), self._asset.normalization)
        self.width_spin.setValue(max(self.width_spin.value(), size[0]))
        self.height_spin.setValue(max(self.height_spin.value(), size[1]))
        self._queue_apply()

    def center_horizontally(self) -> None:
        if self._asset is None:
            return
        self.offset_x_spin.setValue(0)
        self._queue_apply()

    def align_bottom_to_baseline(self) -> None:
        if self._asset is None:
            return
        self.offset_y_spin.setValue((self.baseline_spin.value() or 0) - self.height_spin.value() + 1)
        self._queue_apply()

    def detect_bottommost_visible_pixel(self) -> None:
        if self._asset is None or self._manager is None:
            return
        raw = self._manager._raw_crop_for_asset(self._asset)
        if raw is None:
            return
        document = self._manager._manual_document_for_asset(self._asset, raw, self._manager._apply_cleaning(raw, self._asset.background_removal).cleaned_image)
        point = detect_bottommost_visible_pixel(document.final_image())
        if point is not None:
            self.contact_x_spin.setValue(point[0])
            self.contact_y_spin.setValue(point[1])
        self._queue_apply()

    def set_contact_point_suggestion(self) -> None:
        if self._asset is None or self._manager is None:
            return
        raw = self._manager._raw_crop_for_asset(self._asset)
        if raw is None:
            return
        document = self._manager._manual_document_for_asset(self._asset, raw, self._manager._apply_cleaning(raw, self._asset.background_removal).cleaned_image)
        point = suggest_contact_point(document.final_image())
        if point is not None:
            self.contact_x_spin.setValue(point[0])
            self.contact_y_spin.setValue(point[1])
            self._queue_apply()

    def clear_contact_point(self) -> None:
        self.contact_x_spin.setValue(0)
        self.contact_y_spin.setValue(0)
        self._queue_apply()

    def apply_to_selected_assets(self) -> None:
        if self._manager is None or self._asset is None:
            return
        for asset in self._manager.project.assets:
            if asset.asset_uuid == self._asset.asset_uuid:
                continue
            if asset.character_group == self._asset.character_group:
                asset.normalization = copy_normalization_from_group_leader(self._asset.normalization)
                asset.alignment_group = self._asset.alignment_group
                asset.normalization.normalized_output_filename = generate_normalized_filename(
                    asset.character_group or asset.display_name,
                    asset.category,
                    asset.action,
                    asset.direction,
                    asset.frame_number,
                    asset.variant,
                    canvas_size=(asset.normalization.output_width, asset.normalization.output_height),
                )
        self.changed.emit()

    def apply_to_matching_animation(self) -> None:
        if self._manager is None or self._asset is None:
            return
        group_key = (self._asset.character_group, self._asset.category, self._asset.action, self._asset.direction, self._asset.variant)
        for asset in self._manager.project.assets:
            other_key = (asset.character_group, asset.category, asset.action, asset.direction, asset.variant)
            if other_key == group_key:
                asset.normalization = copy_normalization_from_previous_frame(self._asset.normalization, asset.normalization)
        self.changed.emit()

    def compare_alignment(self) -> None:
        self.compareRequested.emit()

    def export_normalized_requested(self) -> None:
        self.exportRequested.emit()

    def update_from_preview_signals(self) -> None:
        self.refresh_preview()


class CompareAlignmentDialog(QDialog):
    def __init__(self, assets: list[AssetRecord], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare Alignment")
        self.resize(1000, 700)
        layout = QVBoxLayout(self)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Side by Side", "Onion Skin", "Rapid Cycling", "Silhouette Overlay", "Baseline Comparison"])
        layout.addWidget(self.mode_combo)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Frame",
            "Content Center X",
            "Content Center Y",
            "Baseline",
            "Pivot",
            "Contact",
            "Bounds",
            "Offset",
            "Scale",
            "Group Median Delta",
        ])
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._populate(assets)

    def _populate(self, assets: list[AssetRecord]) -> None:
        self.table.setRowCount(len(assets))
        for row, asset in enumerate(assets):
            diag = alignment_diagnostics([asset])
            self.table.setItem(row, 0, QTableWidgetItem(asset.display_name))
            self.table.setItem(row, 1, QTableWidgetItem(str(asset.contact_x or 0)))
            self.table.setItem(row, 2, QTableWidgetItem(str(asset.contact_y or 0)))
            self.table.setItem(row, 3, QTableWidgetItem(str(asset.baseline_y or asset.normalization.baseline_y)))
            self.table.setItem(row, 4, QTableWidgetItem(f"{asset.pivot_x or asset.normalization.pivot_x},{asset.pivot_y or asset.normalization.pivot_y}"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{asset.contact_x or 0},{asset.contact_y or 0}"))
            self.table.setItem(row, 6, QTableWidgetItem(f"{asset.crop_rect.width if asset.crop_rect else 0}x{asset.crop_rect.height if asset.crop_rect else 0}"))
            self.table.setItem(row, 7, QTableWidgetItem(f"{asset.normalization.offset_x},{asset.normalization.offset_y}"))
            self.table.setItem(row, 8, QTableWidgetItem(str(asset.normalization.scale_percent)))
            self.table.setItem(row, 9, QTableWidgetItem(diag.likely_frame_jump_warning or ""))


class NormalizationReportDialog(QDialog):
    def __init__(self, rows: list[dict[str, object]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Normalization Report")
        self.resize(1100, 700)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(rows[0]) if rows else 0)
        self.table.setHorizontalHeaderLabels(list(rows[0].keys()) if rows else [])
        layout.addWidget(self.table)
        self._populate(rows)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self, rows: list[dict[str, object]]) -> None:
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row.values()):
                self.table.setItem(row_index, col_index, QTableWidgetItem(str(value)))
