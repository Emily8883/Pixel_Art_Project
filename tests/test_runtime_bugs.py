from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

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
    def __init__(self, delta_y: int, pos: QPoint | None = None) -> None:
        self._delta_y = delta_y
        self._pos = QPoint(0, 0) if pos is None else pos
        self.accepted = False

    def angleDelta(self):
        return QPoint(0, self._delta_y)

    def position(self):
        return QPointF(self._pos)

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


class DummyKeyEvent:
    def __init__(self, key: int, auto_repeat: bool = False) -> None:
        self._key = key
        self._auto_repeat = auto_repeat
        self.accepted = False

    def key(self):
        return self._key

    def isAutoRepeat(self):
        return self._auto_repeat

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
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(1000, 500)
    pixmap.fill(QColor(255, 0, 0))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    before = canvas.transform().m11()
    cursor = QPoint(250, 150)
    scene_before = canvas.mapToScene(cursor)

    canvas.wheelEvent(DummyWheelEvent(120, cursor))

    assert canvas.transform().m11() > before
    scene_after = canvas.mapToScene(cursor)
    assert abs(scene_after.x() - scene_before.x()) < 2
    assert abs(scene_after.y() - scene_before.y()) < 2


def test_source_canvas_pan_changes_visible_position(qapp):
    canvas = ImageCanvasView()
    pixmap = QPixmap(2000, 2000)
    pixmap.fill(QColor(0, 255, 0))
    canvas.resize(600, 400)
    canvas.show()
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    canvas.actual_pixels()
    before_h = canvas.horizontalScrollBar().value()
    before_v = canvas.verticalScrollBar().value()

    canvas.mousePressEvent(DummyMouseEvent(Qt.MouseButton.MiddleButton, QPoint(10, 10)))
    canvas.mouseMoveEvent(DummyMouseEvent(Qt.MouseButton.MiddleButton, QPoint(30, 30)))
    canvas.mouseReleaseEvent(DummyMouseEvent(Qt.MouseButton.MiddleButton, QPoint(30, 30)))

    assert canvas.horizontalScrollBar().value() != before_h
    assert canvas.verticalScrollBar().value() != before_v


def test_loaded_image_scene_bounds_match_image_dimensions(qapp):
    canvas = ImageCanvasView()
    pixmap = QPixmap(128, 64)
    pixmap.fill(QColor(255, 255, 0))
    canvas.display_pixmap(pixmap)

    assert canvas.sceneRect().width() == 128
    assert canvas.sceneRect().height() == 64
    assert canvas._pixmap_item is not None
    assert canvas._pixmap_item.boundingRect().width() == 128
    assert canvas._pixmap_item.boundingRect().height() == 64


def test_fit_action_places_image_center_near_viewport_center(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(1000, 500)
    pixmap.fill(QColor(0, 0, 255))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)

    viewport_center = canvas.viewport().rect().center()
    scene_center = canvas.mapToScene(viewport_center)
    image_center = canvas._pixmap_item.sceneBoundingRect().center()

    assert abs(scene_center.x() - image_center.x()) < 4
    assert abs(scene_center.y() - image_center.y()) < 4


def test_image_remains_fully_visible_after_first_load(qapp):
    canvas = ImageCanvasView()
    canvas.resize(420, 280)
    canvas.show()
    pixmap = QPixmap(1200, 600)
    pixmap.fill(QColor(255, 0, 255))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)

    viewport = canvas.viewport().rect()
    image_rect = canvas._pixmap_item.sceneBoundingRect()
    for point in (
        canvas.mapFromScene(image_rect.topLeft()),
        canvas.mapFromScene(image_rect.topRight()),
        canvas.mapFromScene(image_rect.bottomLeft()),
        canvas.mapFromScene(image_rect.bottomRight()),
    ):
        assert -8 <= point.x() <= viewport.width() + 8
        assert -8 <= point.y() <= viewport.height() + 8


def test_resize_triggers_valid_refit_when_appropriate(qapp):
    canvas = ImageCanvasView()
    canvas.resize(320, 320)
    canvas.show()
    pixmap = QPixmap(1200, 600)
    pixmap.fill(QColor(0, 128, 128))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    first_zoom = canvas.zoom_percent()
    canvas._auto_fit_pending = True
    canvas.resize(640, 320)
    assert wait_until(lambda: canvas.zoom_percent() != first_zoom)
    canvas.wheelEvent(DummyWheelEvent(120, QPoint(100, 100)))
    zoom_after_user = canvas.zoom_percent()
    canvas.resize(800, 400)
    time.sleep(0.1)
    assert canvas.zoom_percent() == zoom_after_user


def test_switching_sheets_resets_stale_scrollbars_and_centers_new_sheet(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    first = QPixmap(2000, 2000)
    first.fill(QColor(20, 20, 20))
    second = QPixmap(600, 300)
    second.fill(QColor(200, 100, 50))
    canvas.display_pixmap(first)
    assert wait_until(lambda: canvas._initial_fit_completed)
    canvas.actual_pixels()
    canvas.horizontalScrollBar().setValue(canvas.horizontalScrollBar().maximum())
    canvas.verticalScrollBar().setValue(canvas.verticalScrollBar().maximum())
    canvas.display_pixmap(second)
    assert wait_until(lambda: canvas._initial_fit_completed)
    assert canvas.horizontalScrollBar().value() != canvas.horizontalScrollBar().maximum()
    assert canvas.verticalScrollBar().value() != canvas.verticalScrollBar().maximum()
    assert abs(canvas.mapToScene(canvas.viewport().rect().center()).x() - canvas._pixmap_item.sceneBoundingRect().center().x()) < 4


def test_home_centers_without_changing_zoom(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(1000, 500)
    pixmap.fill(QColor(100, 100, 255))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
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
    assert wait_until(lambda: canvas._initial_fit_completed)
    expected = min(canvas.viewport().width() / pixmap.width(), canvas.viewport().height() / pixmap.height())
    assert abs(canvas.transform().m11() - expected) < 0.05


def test_actual_pixels_uses_one_to_one_transform(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(800, 400)
    pixmap.fill(QColor(0, 200, 0))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
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


def test_fit_preserves_image_visibility_and_no_invalid_transform(qapp):
    canvas = ImageCanvasView()
    canvas.resize(480, 320)
    canvas.show()
    pixmap = QPixmap(1408, 768)
    pixmap.fill(QColor(10, 20, 30))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    assert canvas.transform().m11() > 0
    assert canvas._pixmap_item is not None
    viewport = canvas.viewport().rect()
    center = canvas.mapFromScene(canvas._pixmap_item.sceneBoundingRect().center())
    assert viewport.contains(center)


def test_no_deferred_callback_resets_transform_after_fit(qapp):
    canvas = ImageCanvasView()
    canvas.resize(480, 320)
    canvas.show()
    pixmap = QPixmap(800, 400)
    pixmap.fill(QColor(40, 40, 40))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    canvas.fit_and_center_image()
    zoom_before = canvas.zoom_percent()
    canvas.resize(700, 400)
    time.sleep(0.1)
    assert canvas.zoom_percent() == zoom_before


def test_wheel_zoom_keeps_scene_point_under_cursor_approx_stable(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(1200, 600)
    pixmap.fill(QColor(200, 50, 50))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    cursor = QPoint(200, 120)
    before = canvas.mapToScene(cursor)
    canvas.wheelEvent(DummyWheelEvent(120, cursor))
    after = canvas.mapToScene(cursor)
    assert abs(after.x() - before.x()) < 2
    assert abs(after.y() - before.y()) < 2


def test_space_left_drag_changes_scrollbars_and_blocks_crop(qapp):
    canvas = ImageCanvasView()
    canvas.resize(600, 400)
    canvas.show()
    pixmap = QPixmap(1600, 1200)
    pixmap.fill(QColor(0, 100, 200))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    canvas.keyPressEvent(DummyKeyEvent(Qt.Key.Key_Space))
    before_h = canvas.horizontalScrollBar().value()
    before_v = canvas.verticalScrollBar().value()
    canvas.mousePressEvent(DummyMouseEvent(Qt.MouseButton.LeftButton, QPoint(20, 20)))
    canvas.mouseMoveEvent(DummyMouseEvent(Qt.MouseButton.LeftButton, QPoint(40, 50)))
    canvas.mouseReleaseEvent(DummyMouseEvent(Qt.MouseButton.LeftButton, QPoint(40, 50)))
    canvas.keyReleaseEvent(DummyKeyEvent(Qt.Key.Key_Space))
    assert canvas.horizontalScrollBar().value() != before_h
    assert canvas.verticalScrollBar().value() != before_v
    assert canvas.current_crop_rect() is None


def test_crop_coordinates_remain_correct_with_padded_scene_margins(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(300, 200)
    pixmap.fill(QColor(120, 10, 220))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    start = canvas.mapFromScene(QPointF(30, 40))
    end = canvas.mapFromScene(QPointF(110, 140))
    canvas.mousePressEvent(DummyMouseEvent(Qt.MouseButton.LeftButton, start))
    canvas.mouseMoveEvent(DummyMouseEvent(Qt.MouseButton.LeftButton, end))
    canvas.mouseReleaseEvent(DummyMouseEvent(Qt.MouseButton.LeftButton, end))
    rect = canvas.current_crop_rect()
    assert rect is not None
    assert abs(rect.left() - 30) < 2
    assert abs(rect.top() - 40) < 2
    assert abs(rect.width() - 80) < 2
    assert abs(rect.height() - 100) < 2


def test_clicking_outside_pixmap_does_not_create_valid_crop(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(300, 200)
    pixmap.fill(QColor(220, 220, 0))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    outside = canvas.mapFromScene(QPointF(-80, -80))
    canvas.mousePressEvent(DummyMouseEvent(Qt.MouseButton.LeftButton, outside))
    canvas.mouseReleaseEvent(DummyMouseEvent(Qt.MouseButton.LeftButton, outside))
    assert canvas.current_crop_rect() is None


def test_initial_auto_fit_occurs_once(qapp):
    canvas = ImageCanvasView()
    canvas.resize(420, 280)
    canvas.show()
    pixmap = QPixmap(600, 300)
    pixmap.fill(QColor(50, 150, 50))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    zoom_before = canvas.zoom_percent()
    canvas.resize(700, 300)
    time.sleep(0.1)
    assert canvas.zoom_percent() == zoom_before


def test_source_canvas_actions_exist(qapp):
    canvas = ImageCanvasView()
    assert canvas.fit_action.text() == "Fit Image to View"
    assert canvas.actual_pixels_action.text() == "100%"
    assert canvas.center_action.text() == "Center Image"
    assert canvas.reset_view_action.text() == "Reset View"


def test_clear_pixmap_resets_canvas_state(qapp):
    canvas = ImageCanvasView()
    canvas.resize(500, 300)
    canvas.show()
    pixmap = QPixmap(300, 200)
    pixmap.fill(QColor(20, 180, 120))
    canvas.display_pixmap(pixmap)
    assert wait_until(lambda: canvas._initial_fit_completed)
    canvas.actual_pixels()
    canvas.clear_pixmap()

    assert canvas._pixmap_item is None
    assert canvas.current_crop_rect() is None
    assert canvas.zoom_percent() == 100
    assert canvas.sceneRect().isNull()


def test_removing_last_source_sheet_clears_canvas_state(qapp, tmp_path, monkeypatch):
    window = MainWindow()
    window.project_manager.new_project("Test Project", str(tmp_path))
    window.project_path = tmp_path / "project.json"
    window.project_manager.add_source_sheet(make_png(tmp_path / "sheet.png", size=(64, 32)))
    window._update_ui_from_project()
    assert window.canvas._pixmap_item is not None
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    window.remove_source_sheet()

    assert window.project_manager.project.source_sheets == []
    assert window.canvas._pixmap_item is None
    assert window.canvas.zoom_percent() == 100
    assert window.source_sheet_combo.count() == 0


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
