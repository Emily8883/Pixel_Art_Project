from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView


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
        self._detection_items: list[object] = []
        self._detection_data = {
            "proposals": [],
            "exclusions": [],
            "selected": set(),
            "show_numbers": True,
            "show_confidence": True,
            "show_assigned": True,
            "hide_rejected": False,
            "show_exclusion_zones": True,
        }

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
        self._clear_detection_overlay()
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

    def set_detection_overlay(
        self,
        proposals: list[object],
        exclusion_zones: list[object] | None = None,
        *,
        selected_ids: set[str] | None = None,
        show_numbers: bool = True,
        show_confidence: bool = True,
        show_assigned: bool = True,
        hide_rejected: bool = False,
        show_exclusion_zones: bool = True,
    ) -> None:
        self._detection_data = {
            "proposals": list(proposals),
            "exclusions": list(exclusion_zones or []),
            "selected": set(selected_ids or set()),
            "show_numbers": show_numbers,
            "show_confidence": show_confidence,
            "show_assigned": show_assigned,
            "hide_rejected": hide_rejected,
            "show_exclusion_zones": show_exclusion_zones,
        }
        self._rebuild_detection_overlay()

    def clear_detection_overlay(self) -> None:
        self._detection_data["proposals"] = []
        self._detection_data["exclusions"] = []
        self._detection_data["selected"] = set()
        self._clear_detection_overlay()

    def _clear_detection_overlay(self) -> None:
        for item in self._detection_items:
            try:
                self.scene().removeItem(item)
            except Exception:
                pass
        self._detection_items = []

    def _rebuild_detection_overlay(self) -> None:
        self._clear_detection_overlay()
        if self._pixmap_item is None:
            return
        proposals = self._detection_data["proposals"]
        if proposals:
            for index, proposal in enumerate(proposals, start=1):
                status = getattr(proposal.status, "value", str(getattr(proposal, "status", "proposed")))
                if self._detection_data["hide_rejected"] and status == "rejected":
                    continue
                if status == "assigned" and not self._detection_data["show_assigned"]:
                    continue
                rect = getattr(proposal, "rect", None)
                if rect is None:
                    continue
                color = QColor(160, 160, 160, 220)
                if status == "accepted":
                    color = QColor(60, 180, 75, 220)
                elif status == "rejected":
                    color = QColor(150, 150, 150, 180)
                elif status == "modified":
                    color = QColor(255, 165, 0, 230)
                elif status == "assigned":
                    color = QColor(0, 160, 220, 230)
                pen = QPen(color, 2)
                rect_item = self.scene().addRect(QRectF(rect.x, rect.y, rect.width, rect.height), pen)
                rect_item.setZValue(20)
                self._detection_items.append(rect_item)
                if self._detection_data["show_numbers"] or self._detection_data["show_confidence"]:
                    label = str(index)
                    if self._detection_data["show_confidence"]:
                        confidence = getattr(proposal, "confidence", 0.0)
                        label = f"{label} {confidence:.2f}"
                    text_item = QGraphicsSimpleTextItem(label)
                    text_item.setBrush(color)
                    text_item.setPos(rect.x, max(0, rect.y - 14))
                    text_item.setZValue(21)
                    self.scene().addItem(text_item)
                    self._detection_items.append(text_item)
        if self._detection_data["show_exclusion_zones"]:
            for zone in self._detection_data["exclusions"]:
                rect = getattr(zone, "rect", None)
                if rect is None:
                    continue
                pen = QPen(QColor(200, 60, 60, 180), 1, Qt.PenStyle.DashLine)
                rect_item = self.scene().addRect(QRectF(rect.x, rect.y, rect.width, rect.height), pen)
                rect_item.setZValue(19)
                self._detection_items.append(rect_item)

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
