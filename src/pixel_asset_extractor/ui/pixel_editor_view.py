from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from ..manual_editing import ManualEditDocument
from ..image_tools import pil_image_to_qpixmap


@dataclass(frozen=True, slots=True)
class CursorInfo:
    x: int
    y: int
    rgba: tuple[int, int, int, int]


class PixelEditorView(QGraphicsView):
    cursorInfoChanged = Signal(object)
    selectionChanged = Signal(object)
    zoomChanged = Signal(int)
    dirtyChanged = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
        self.setMouseTracking(True)

        self._document: ManualEditDocument | None = None
        self._display_mode = "final"
        self._tool = "select"
        self._brush_size = 1
        self._foreground_color: tuple[int, int, int, int] = (255, 255, 255, 255)
        self._zoom_percent = 100
        self._grid_enabled = True
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._selection_item: QGraphicsRectItem | None = None
        self._stroke_points: list[tuple[int, int]] = []
        self._select_start: QPointF | None = None
        self._panning = False
        self._space_panning = False
        self._pan_start: QPoint | None = None
        self._overlay_alpha = False
        self._overlay_edge = False
        self._overlay_halo = False
        self._overlay_isolated = False
        self._overlay_semi = False

    def set_document(self, document: ManualEditDocument | None) -> None:
        self._document = document
        self._refresh_pixmap()
        self._refresh_selection()

    def set_display_mode(self, mode: str) -> None:
        if mode not in {"raw", "auto", "final"}:
            raise ValueError(f"Unsupported editor mode: {mode}")
        self._display_mode = mode
        self._refresh_pixmap()

    def set_tool(self, tool: str) -> None:
        self._tool = tool

    def set_brush_size(self, size: int) -> None:
        self._brush_size = max(1, min(5, int(size)))

    def set_foreground_color(self, rgba: tuple[int, int, int, int]) -> None:
        self._foreground_color = tuple(int(v) for v in rgba)

    def set_grid_enabled(self, enabled: bool) -> None:
        self._grid_enabled = bool(enabled)
        self.viewport().update()

    def set_overlay_flags(
        self,
        *,
        alpha: bool = False,
        edge: bool = False,
        halo: bool = False,
        isolated: bool = False,
        semi: bool = False,
    ) -> None:
        self._overlay_alpha = alpha
        self._overlay_edge = edge
        self._overlay_halo = halo
        self._overlay_isolated = isolated
        self._overlay_semi = semi
        self._refresh_pixmap()

    def zoom_percent(self) -> int:
        return self._zoom_percent

    def current_tool(self) -> str:
        return self._tool

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() == 0:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        new_zoom = max(100, min(6400, int(round(self._zoom_percent * factor))))
        self.set_zoom_percent(new_zoom)
        event.accept()

    def set_zoom_percent(self, zoom_percent: int) -> None:
        zoom_percent = max(100, min(6400, int(zoom_percent)))
        if zoom_percent == self._zoom_percent:
            return
        self._zoom_percent = zoom_percent
        self.resetTransform()
        self.scale(self._zoom_percent / 100.0, self._zoom_percent / 100.0)
        self.zoomChanged.emit(self._zoom_percent)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.SpaceModifier:
            self._space_panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        pixel = self._scene_pixel_at(event.position().toPoint())
        if pixel is None or self._document is None:
            super().mousePressEvent(event)
            return
        x, y = pixel
        if self._tool in {"pencil", "eraser"} and self._display_mode == "final":
            self._stroke_points = [(x, y)]
            event.accept()
            return
        if self._tool == "picker":
            self.cursorInfoChanged.emit(CursorInfo(x, y, self._document.pick_color(x, y, self._display_mode)))
            self._tool = "select"
            event.accept()
            return
        if self._tool == "fill" and self._display_mode == "final":
            self._document.flood_fill(x, y, self._foreground_color, exact_color=True, tolerance_ui=0, connectivity=4)
            self._refresh_pixmap()
            self.dirtyChanged.emit(self._document.dirty)
            event.accept()
            return
        self._select_start = QPointF(x, y)
        self._set_selection_rect(QRectF(QPointF(x, y), QPointF(x + 1, y + 1)))
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._panning or self._space_panning:
            if self._pan_start is not None:
                delta = event.pos() - self._pan_start
                self._pan_start = event.pos()
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
                event.accept()
                return

        pixel = self._scene_pixel_at(event.position().toPoint())
        if pixel is not None and self._document is not None:
            x, y = pixel
            if 0 <= x < self._document.size[0] and 0 <= y < self._document.size[1]:
                self.cursorInfoChanged.emit(CursorInfo(x, y, self._document.pick_color(x, y, self._display_mode)))

        if self._select_start is not None:
            current = self._scene_pixel_at(event.position().toPoint())
            if current is None:
                return
            x, y = current
            left = int(min(self._select_start.x(), x))
            top = int(min(self._select_start.y(), y))
            width = int(abs(x - self._select_start.x())) + 1
            height = int(abs(y - self._select_start.y())) + 1
            self._set_selection_rect(QRectF(left, top, width, height))
            event.accept()
            return

        if self._stroke_points and pixel is not None:
            self._stroke_points.append(pixel)
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
        if event.button() == Qt.MouseButton.LeftButton and self._space_panning:
            self._space_panning = False
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return
        if self._stroke_points and self._document is not None:
            if self._tool == "pencil":
                self._document.apply_pencil(self._stroke_points, self._foreground_color, self._brush_size)
            elif self._tool == "eraser":
                self._document.apply_eraser(self._stroke_points, self._brush_size)
            self._stroke_points = []
            self._refresh_pixmap()
            self.dirtyChanged.emit(self._document.dirty)
            event.accept()
            return
        if self._select_start is not None:
            self._select_start = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Undo):
            if self._document and self._document.undo():
                self._refresh_pixmap()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Redo) or (event.key() == Qt.Key.Key_Y and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            if self._document and self._document.redo():
                self._refresh_pixmap()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._stroke_points = []
            self._select_start = None
            event.accept()
            return
        if event.key() == Qt.Key.Key_V:
            self._tool = "select"
            event.accept()
            return
        if event.key() == Qt.Key.Key_B:
            self._tool = "pencil"
            event.accept()
            return
        if event.key() == Qt.Key.Key_E:
            self._tool = "eraser"
            event.accept()
            return
        if event.key() == Qt.Key.Key_I:
            self._tool = "picker"
            event.accept()
            return
        if event.key() == Qt.Key.Key_F:
            self._tool = "fill"
            event.accept()
            return
        if event.key() == Qt.Key.Key_BracketLeft:
            self.set_brush_size(self._brush_size - 1)
            event.accept()
            return
        if event.key() == Qt.Key.Key_BracketRight:
            self.set_brush_size(self._brush_size + 1)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete and self._document and self._document.selection_rect is not None:
            self._document.delete_selection()
            self._refresh_pixmap()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return) and self._document and self._document.commit_floating_selection():
            self._refresh_pixmap()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self._space_panning = True
            event.accept()
            return
        if self._document and self._document.selection_rect is not None and event.key() in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx = -step if event.key() == Qt.Key.Key_Left else step if event.key() == Qt.Key.Key_Right else 0
            dy = -step if event.key() == Qt.Key.Key_Up else step if event.key() == Qt.Key.Key_Down else 0
            self._document.move_selection(dx, dy)
            self._refresh_pixmap()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self._space_panning = False
        super().keyReleaseEvent(event)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        if self._grid_enabled and self._zoom_percent >= 800 and self._document is not None:
            painter.save()
            painter.setPen(QPen(QColor(255, 255, 255, 60), 0))
            width, height = self._document.size
            for x in range(width + 1):
                painter.drawLine(x, 0, x, height)
            for y in range(height + 1):
                painter.drawLine(0, y, width, y)
            painter.restore()
        if self._selection_item is not None:
            painter.save()
            painter.setPen(QPen(QColor(255, 255, 255, 200), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._selection_item.rect())
            painter.restore()

    def _scene_pixel_at(self, viewport_pos: QPoint) -> tuple[int, int] | None:
        if self._pixmap_item is None or self._document is None:
            return None
        scene_pos = self.mapToScene(viewport_pos)
        x = int(scene_pos.x())
        y = int(scene_pos.y())
        if x < 0 or y < 0 or x >= self._document.size[0] or y >= self._document.size[1]:
            return None
        return x, y

    def _refresh_pixmap(self) -> None:
        scene = self.scene()
        scene.clear()
        self._pixmap_item = None
        if self._document is None:
            return
        image = self._document.current_image(self._display_mode)
        if self._overlay_alpha:
            image = self._document.alpha_preview()
        elif self._overlay_edge:
            image = self._document.edge_highlight()
        elif self._overlay_halo:
            image = self._document.suspected_halo_highlight(self._document.background_rgba)
        elif self._overlay_isolated:
            mask = self._document.isolated_pixel_mask()
            overlay = Image.new("RGBA", self._document.size, (0, 0, 0, 0))
            pixels = overlay.load()
            for y in range(mask.shape[0]):
                for x in range(mask.shape[1]):
                    if mask[y, x]:
                        pixels[x, y] = (0, 255, 255, 255)
            image = overlay
        elif self._overlay_semi:
            mask = self._document.semi_transparent_mask()
            overlay = Image.new("RGBA", self._document.size, (0, 0, 0, 0))
            pixels = overlay.load()
            for y in range(mask.shape[0]):
                for x in range(mask.shape[1]):
                    if mask[y, x]:
                        pixels[x, y] = (255, 0, 255, 255)
            image = overlay
        pixmap = pil_image_to_qpixmap(image)
        self._pixmap_item = scene.addPixmap(pixmap)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self.resetTransform()
        self.scale(self._zoom_percent / 100.0, self._zoom_percent / 100.0)
        self._refresh_selection()

    def _set_selection_rect(self, rect: QRectF) -> None:
        if self._selection_item is None:
            self._selection_item = self.scene().addRect(rect, QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
            self._selection_item.setZValue(10)
        else:
            self._selection_item.setRect(rect)
        self.selectionChanged.emit(rect)

    def _refresh_selection(self) -> None:
        if self._document is None or self._document.selection_rect is None:
            if self._selection_item is not None:
                self.scene().removeItem(self._selection_item)
                self._selection_item = None
            return
        x, y, w, h = self._document.selection_rect
        self._set_selection_rect(QRectF(x, y, w, h))

