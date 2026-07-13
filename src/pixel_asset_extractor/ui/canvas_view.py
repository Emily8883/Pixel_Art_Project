from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView


class ImageCanvasView(QGraphicsView):
    cropChanged = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint(0))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(QColor(45, 45, 48)))
        self.setMouseTracking(True)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._crop_item: QGraphicsRectItem | None = None
        self._image_size: tuple[int, int] | None = None
        self._draw_start: QPointF | None = None
        self._panning = False
        self._pan_start = None

    def set_image(self, pixmap) -> None:
        scene = self.scene()
        scene.clear()
        self._pixmap_item = scene.addPixmap(pixmap)
        self._pixmap_item.setZValue(0)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self._image_size = (pixmap.width(), pixmap.height())
        self._crop_item = None
        self._draw_start = None
        self._panning = False
        self.fit_to_image()

    def fit_to_image(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def set_crop_rect(self, rect: QRectF | None) -> None:
        if rect is None:
            if self._crop_item is not None:
                self.scene().removeItem(self._crop_item)
                self._crop_item = None
            return

        if self._crop_item is None:
            pen = QPen(QColor(255, 209, 102), 2, Qt.PenStyle.DashLine)
            brush = QBrush(QColor(255, 209, 102, 40))
            self._crop_item = self.scene().addRect(rect, pen, brush)
            self._crop_item.setZValue(10)
        else:
            self._crop_item.setRect(rect)

    def current_crop_rect(self) -> QRectF | None:
        if self._crop_item is None:
            return None
        return self._crop_item.rect()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._pixmap_item is not None:
            self._draw_start = self.mapToScene(event.pos())
            self.set_crop_rect(QRectF(self._draw_start, self._draw_start))
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

        if self._draw_start is not None and self._image_size is not None:
            current = self.mapToScene(event.pos())
            rect = QRectF(self._draw_start, current).normalized()
            rect = self._clamp_rect(rect)
            self.set_crop_rect(rect)
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

        if event.button() == Qt.MouseButton.LeftButton and self._draw_start is not None:
            rect = self.current_crop_rect()
            self._draw_start = None
            if rect is not None:
                self.cropChanged.emit(rect)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() == 0:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    def _clamp_rect(self, rect: QRectF) -> QRectF:
        if self._image_size is None:
            return rect
        width, height = self._image_size
        left = max(0.0, min(rect.left(), width))
        top = max(0.0, min(rect.top(), height))
        right = max(0.0, min(rect.right(), width))
        bottom = max(0.0, min(rect.bottom(), height))
        return QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
