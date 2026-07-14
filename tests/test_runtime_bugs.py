from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from pixel_asset_extractor.detection import DetectionResult
from pixel_asset_extractor.image_tools import load_png, pil_image_to_qpixmap
from pixel_asset_extractor.logging_utils import configure_logging, install_excepthook
from pixel_asset_extractor.project_manager import ProjectManager
from pixel_asset_extractor.project_model import SourceSheet
from pixel_asset_extractor.ui.canvas_view import ImageCanvasView
from pixel_asset_extractor.ui.detection_panel import DetectionPanelWidget
from pixel_asset_extractor.ui.main_window import MainWindow


def make_png(path: Path, size=(16, 16), color=(90, 120, 160, 255)) -> Path:
    image = Image.new("RGBA", size, color)
    image.save(path)
    return path


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    return predicate()


class DummyWheelEvent:
    def __init__(self, delta_y: int) -> None:
        self._delta_y = delta_y
        self.accepted = False

    def angleDelta(self):
        return QPoint(0, self._delta_y)

    def accept(self):
        self.accepted = True


class DummyMouseEvent:
    def __init__(self, button, pos: QPoint) -> None:
        self._button = button
        self._pos = pos
        self.accepted = False

    def button(self):
        return self._button

    def pos(self):
        return self._pos

    def accept(self):
        self.accepted = True


def test_add_source_sheet_displays_pixmap_item(qapp, tmp_path, monkeypatch):
    window = MainWindow()
    window.project_manager.new_project("Test Project", str(tmp_path))
    window.project_path = tmp_path / "project.json"
    sheet_path = make_png(tmp_path / "sheet.png", size=(32, 24))
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(sheet_path), "PNG Images (*.png)"))

    window.add_source_sheet()

    assert window.canvas._pixmap_item is not None
    assert window.canvas._image_size == (32, 24)
    assert window.canvas.scene().items()


def test_selecting_source_sheet_replaces_displayed_pixmap(qapp, tmp_path):
    window = MainWindow()
    window.project_manager.new_project("Test Project", str(tmp_path))
    window.project_path = tmp_path / "project.json"
    first = window.project_manager.add_source_sheet(make_png(tmp_path / "first.png", size=(16, 16)))
    second = window.project_manager.add_source_sheet(make_png(tmp_path / "second.png", size=(40, 20)))

    window._update_ui_from_project()
    window.source_sheet_combo.setCurrentIndex(window.source_sheet_combo.findData(second.source_sheet_id))

    assert window.canvas._image_size == (40, 20)
    assert window.source_image_info_label.text().endswith("40 x 20")
    assert window.source_sheet_combo.currentData() == second.source_sheet_id
    assert first.source_sheet_id != second.source_sheet_id


def test_open_project_restores_selected_source_sheet(qapp, tmp_path, monkeypatch):
    window = MainWindow()
    window.project_manager.new_project("Test Project", str(tmp_path))
    window.project_path = tmp_path / "project.json"
    first = window.project_manager.add_source_sheet(make_png(tmp_path / "first.png", size=(16, 16)))
    second = window.project_manager.add_source_sheet(make_png(tmp_path / "second.png", size=(48, 28)))
    window._update_ui_from_project()
    window.source_sheet_combo.setCurrentIndex(window.source_sheet_combo.findData(second.source_sheet_id))

    project_path = tmp_path / "saved_project.json"
    window._save_to_path(project_path)

    reopened = MainWindow()
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(project_path), "JSON Files (*.json)"))
    reopened.open_project()

    assert reopened.source_sheet_combo.currentData() == second.source_sheet_id
    assert reopened.canvas._image_size == (48, 28)
    assert first.source_sheet_id != second.source_sheet_id


def test_relink_source_sheet_refreshes_canvas(qapp, tmp_path, monkeypatch):
    window = MainWindow()
    window.project_manager.new_project("Test Project", str(tmp_path))
    window.project_path = tmp_path / "project.json"
    original = window.project_manager.add_source_sheet(make_png(tmp_path / "original.png", size=(16, 16)))
    window._update_ui_from_project()
    window.source_sheet_combo.setCurrentIndex(window.source_sheet_combo.findData(original.source_sheet_id))
    relinked = make_png(tmp_path / "relinked.png", size=(64, 32))

    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(relinked), "PNG Images (*.png)"))
    window.relink_source_sheet()

    assert window.canvas._image_size == (64, 32)
    assert "64 x 32" in window.source_image_info_label.text()


def test_missing_source_file_shows_error_without_closing_app(qapp, tmp_path, monkeypatch):
    window = MainWindow()
    window.project_manager.new_project("Test Project", str(tmp_path))
    window.project_path = tmp_path / "project.json"
    sheet = window.project_manager.add_source_sheet(tmp_path / "missing.png")
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_show_error", lambda title, message: messages.append((title, message)))

    assert window.display_source_sheet(sheet) is False
    assert messages
    assert window.canvas._pixmap_item is None


def test_source_canvas_wheel_event_changes_zoom(qapp):
    canvas = ImageCanvasView()
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(255, 0, 0))
    canvas.display_pixmap(pixmap)
    before = canvas.transform().m11()

    canvas.wheelEvent(DummyWheelEvent(120))

    assert canvas.transform().m11() > before


def test_source_canvas_pan_changes_visible_position(qapp):
    canvas = ImageCanvasView()
    pixmap = QPixmap(2000, 2000)
    pixmap.fill(QColor(0, 255, 0))
    canvas.display_pixmap(pixmap)
    canvas.wheelEvent(DummyWheelEvent(120))
    canvas.wheelEvent(DummyWheelEvent(120))
    before = canvas.horizontalScrollBar().value()

    canvas.mousePressEvent(DummyMouseEvent(Qt.MouseButton.MiddleButton, QPoint(10, 10)))
    canvas.mouseMoveEvent(DummyMouseEvent(Qt.MouseButton.MiddleButton, QPoint(30, 30)))
    canvas.mouseReleaseEvent(DummyMouseEvent(Qt.MouseButton.MiddleButton, QPoint(30, 30)))

    assert canvas.horizontalScrollBar().value() != before


def test_loaded_image_scene_bounds_match_image_dimensions(qapp):
    canvas = ImageCanvasView()
    pixmap = QPixmap(128, 64)
    pixmap.fill(QColor(255, 255, 0))
    canvas.display_pixmap(pixmap)

    assert canvas.sceneRect().width() == 128
    assert canvas.sceneRect().height() == 64


def test_fit_action_places_image_center_near_viewport_center(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(1000, 500)
    pixmap.fill(QColor(0, 0, 255))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: not canvas._needs_fit)

    viewport_center = canvas.viewport().rect().center()
    scene_center = canvas.mapToScene(viewport_center)

    assert abs(scene_center.x() - canvas.sceneRect().center().x()) < 4
    assert abs(scene_center.y() - canvas.sceneRect().center().y()) < 4


def test_image_remains_fully_visible_after_first_load(qapp):
    canvas = ImageCanvasView()
    canvas.resize(420, 280)
    canvas.show()
    pixmap = QPixmap(1200, 600)
    pixmap.fill(QColor(255, 0, 255))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: not canvas._needs_fit)

    viewport = canvas.viewport().rect()
    for point in (
        canvas.mapFromScene(canvas.sceneRect().topLeft()),
        canvas.mapFromScene(canvas.sceneRect().topRight()),
        canvas.mapFromScene(canvas.sceneRect().bottomLeft()),
        canvas.mapFromScene(canvas.sceneRect().bottomRight()),
    ):
        assert viewport.contains(point)


def test_resize_triggers_valid_refit_when_appropriate(qapp):
    canvas = ImageCanvasView()
    canvas.resize(320, 320)
    canvas.show()
    pixmap = QPixmap(1200, 600)
    pixmap.fill(QColor(0, 128, 128))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: not canvas._needs_fit)
    first_zoom = canvas.zoom_percent()
    canvas._needs_fit = True
    canvas.resize(640, 320)
    assert wait_until(lambda: canvas.zoom_percent() != first_zoom)


def test_switching_sheets_resets_stale_scrollbars_and_centers_new_sheet(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    first = QPixmap(2000, 2000)
    first.fill(QColor(20, 20, 20))
    second = QPixmap(600, 300)
    second.fill(QColor(200, 100, 50))
    canvas.display_pixmap(first)
    assert wait_until(lambda: not canvas._needs_fit)
    canvas.actual_pixels()
    canvas.horizontalScrollBar().setValue(canvas.horizontalScrollBar().maximum())
    canvas.verticalScrollBar().setValue(canvas.verticalScrollBar().maximum())
    canvas.display_pixmap(second)
    assert wait_until(lambda: not canvas._needs_fit)
    assert canvas.horizontalScrollBar().value() == 0
    assert canvas.verticalScrollBar().value() == 0
    assert abs(canvas.mapToScene(canvas.viewport().rect().center()).x() - canvas.sceneRect().center().x()) < 4


def test_home_centers_without_changing_zoom(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(1000, 500)
    pixmap.fill(QColor(100, 100, 255))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: not canvas._needs_fit)
    canvas.wheelEvent(DummyWheelEvent(120))
    zoom_before = canvas.zoom_percent()
    canvas.center_image()
    assert canvas.zoom_percent() == zoom_before


def test_fit_preserves_aspect_ratio(qapp):
    canvas = ImageCanvasView()
    canvas.resize(640, 360)
    canvas.show()
    pixmap = QPixmap(1600, 800)
    pixmap.fill(QColor(80, 40, 160))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: not canvas._needs_fit)
    expected = min(canvas.viewport().width() / pixmap.width(), canvas.viewport().height() / pixmap.height())
    assert abs(canvas.transform().m11() - expected) < 0.05


def test_actual_pixels_uses_one_to_one_transform(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(800, 400)
    pixmap.fill(QColor(0, 200, 0))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: not canvas._needs_fit)
    canvas.actual_pixels()
    assert abs(canvas.transform().m11() - 1.0) < 0.001


def test_nearest_neighbor_rendering_remains_enabled(qapp):
    canvas = ImageCanvasView()
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(255, 0, 0))
    canvas.display_pixmap(pixmap)

    assert canvas._pixmap_item is not None
    assert canvas._pixmap_item.transformationMode() == Qt.TransformationMode.FastTransformation
    assert not bool(canvas.renderHints() & QPainter.RenderHint.SmoothPixmapTransform)


def test_detection_action_with_no_source_sheet_shows_validation_message(qapp, monkeypatch):
    widget = DetectionPanelWidget()
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", lambda *args, **kwargs: messages.append((args[1], args[2])))

    widget.generate_proposals()

    assert messages
    assert widget._thread is None


class BlockingManager:
    def __init__(self, sheet: SourceSheet, release_event: threading.Event) -> None:
        self._sheet = sheet
        self._release_event = release_event

    def source_sheet(self, source_sheet_id: str) -> SourceSheet:
        return self._sheet

    def preview_sprite_regions(self, source_sheet_id: str, settings, cancel_requested=None):
        self._release_event.wait(timeout=5)
        result = DetectionResult(
            image_size=(16, 16),
            proposals=[],
            foreground_mask=__import__("numpy").zeros((16, 16), dtype=bool),
            edge_mask=__import__("numpy").zeros((16, 16), dtype=bool),
            variance_mask=__import__("numpy").zeros((16, 16), dtype=bool),
            combined_mask=__import__("numpy").zeros((16, 16), dtype=bool),
        )
        return result, settings


def test_detection_action_with_valid_source_sheet_keeps_window_open(qapp, tmp_path):
    source_path = make_png(tmp_path / "source.png", size=(32, 32))
    sheet = SourceSheet(source_sheet_id="sheet-1", label="Sheet", path=str(source_path))
    release = threading.Event()
    manager = BlockingManager(sheet, release)

    widget = DetectionPanelWidget()
    widget.set_context(manager, sheet.source_sheet_id, tmp_path / "project.json")
    widget.generate_proposals()

    assert wait_until(lambda: widget._thread is not None and widget._thread.isRunning())
    assert widget._worker is not None
    assert widget._thread is not None
    release.set()
    assert wait_until(lambda: widget._thread is None)


def test_detection_worker_exception_is_reported_without_terminating_app(qapp, tmp_path, monkeypatch):
    source_path = make_png(tmp_path / "source.png", size=(32, 32))
    sheet = SourceSheet(source_sheet_id="sheet-1", label="Sheet", path=str(source_path))

    class FailingManager:
        def source_sheet(self, source_sheet_id: str) -> SourceSheet:
            return sheet

        def preview_sprite_regions(self, source_sheet_id: str, settings, cancel_requested=None):
            raise RuntimeError("worker boom")

    widget = DetectionPanelWidget()
    widget.set_context(FailingManager(), sheet.source_sheet_id, tmp_path / "project.json")
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda *args, **kwargs: messages.append((args[1], args[2])))

    widget.generate_proposals()

    assert wait_until(lambda: widget._thread is None)
    assert messages
    assert "worker boom" in widget.analysis_summary.text()


def test_worker_and_thread_references_remain_alive_during_processing(qapp, tmp_path):
    source_path = make_png(tmp_path / "source.png", size=(32, 32))
    sheet = SourceSheet(source_sheet_id="sheet-1", label="Sheet", path=str(source_path))
    release = threading.Event()
    manager = BlockingManager(sheet, release)

    widget = DetectionPanelWidget()
    widget.set_context(manager, sheet.source_sheet_id, tmp_path / "project.json")
    widget.analyze()

    assert wait_until(lambda: widget._thread is not None and widget._thread.isRunning())
    assert widget._worker is not None
    assert widget._thread is not None
    release.set()
    assert wait_until(lambda: widget._thread is None)


def test_top_level_exception_hook_writes_traceback_to_log(tmp_path):
    log_path = configure_logging(tmp_path)
    messages: list[tuple[str, str]] = []
    hook = install_excepthook(lambda title, message: messages.append((title, message)))

    try:
        1 / 0
    except Exception as exc:
        hook(type(exc), exc, exc.__traceback__)

    content = log_path.read_text(encoding="utf-8")
    assert "ZeroDivisionError" in content
    assert "Traceback" in content
    assert messages and messages[0][0] == "Unexpected error"


def test_gui_startup_smoke(qapp):
    window = MainWindow()
    assert window.windowTitle() == "Pixel Asset Extractor"
    assert window.canvas.scene() is not None
