from __future__ import annotations

import logging

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QContextMenuEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QMenu


class ImageCanvasView(QGraphicsView):
    cropChanged = Signal(object)
    zoomChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint(0))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(QColor(45, 45, 48)))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._pixmap: QPixmap | None = None
        self._crop_item: QGraphicsRectItem | None = None
        self._crop_rect_image: QRectF | None = None
        self._image_size: tuple[int, int] | None = None
        self._zoom_percent = 100
        self._auto_fit_pending = False
        self._initial_fit_completed = False
        self._fit_retry_scheduled = False
        self._user_has_navigated = False
        self._draw_start: QPointF | None = None
        self._panning = False
        self._pan_start = None
        self._space_pan_active = False
        self._space_was_panning = False
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
        self._build_actions()

    def display_pixmap(self, pixmap: QPixmap) -> None:
        scene = self.scene()
        scene.clear()
        self._pixmap = QPixmap(pixmap)
        self._pixmap_item = scene.addPixmap(self._pixmap)
        self._pixmap_item.setZValue(0)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self._image_size = (pixmap.width(), pixmap.height())
        self._crop_item = None
        self._crop_rect_image = None
        self._draw_start = None
        self._panning = False
        self._pan_start = None
        self._space_pan_active = False
        self._space_was_panning = False
        self._zoom_percent = 100
        self._auto_fit_pending = True
        self._initial_fit_completed = False
        self._fit_retry_scheduled = False
        self._user_has_navigated = False
        self._clear_detection_overlay()
        scene.setSceneRect(self._image_bounds() or QRectF())
        self.reset_view()
        QTimer.singleShot(0, self.fit_and_center_image)

    def set_image(self, pixmap) -> None:
        self.display_pixmap(pixmap)

    def clear_pixmap(self) -> None:
        scene = self.scene()
        scene.clear()
        self._pixmap_item = None
        self._pixmap = None
        self._crop_item = None
        self._crop_rect_image = None
        self._image_size = None
        self._zoom_percent = 100
        self._auto_fit_pending = False
        self._initial_fit_completed = False
        self._fit_retry_scheduled = False
        self._user_has_navigated = False
        self._draw_start = None
        self._panning = False
        self._pan_start = None
        self._space_pan_active = False
        self._space_was_panning = False
        self._clear_detection_overlay()
        self.reset_view()
        scene.setSceneRect(QRectF(0, 0, 0, 0))

    def _image_bounds(self) -> QRectF | None:
        if self._pixmap_item is None:
            return None
        return self._pixmap_item.boundingRect()

    def _viewport_ready(self) -> bool:
        return self.viewport().width() > 2 and self.viewport().height() > 2

    def _log_view_state(self, label: str) -> None:
        self._logger.debug(
            "%s viewport=%sx%s scene_rect=%s transform=%.4f h=%s v=%s",
            label,
            self.viewport().width(),
            self.viewport().height(),
            self.sceneRect(),
            self.transform().m11(),
            self.horizontalScrollBar().value(),
            self.verticalScrollBar().value(),
        )

    def _update_zoom_state(self) -> None:
        self._zoom_percent = max(1, int(round(self.transform().m11() * 100)))
        self.zoomChanged.emit(self._zoom_percent)

    def _build_actions(self) -> None:
        self.fit_action = QAction("Fit Image to View", self)
        self.fit_action.triggered.connect(self.fit_and_center_image)
        self.actual_pixels_action = QAction("100%", self)
        self.actual_pixels_action.triggered.connect(self.actual_pixels)
        self.center_action = QAction("Center Image", self)
        self.center_action.triggered.connect(self.center_image)
        self.reset_view_action = QAction("Reset View", self)
        self.reset_view_action.triggered.connect(self.reset_view_and_fit)
        self.fit_action.setShortcut("F")
        self.actual_pixels_action.setShortcut("1")
        self.center_action.setShortcut("Home")
        self.reset_view_action.setShortcut("Ctrl+0")
        for action in (self.fit_action, self.actual_pixels_action, self.center_action, self.reset_view_action):
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.addAction(action)

    def _schedule_fit_retry(self) -> None:
        if self._fit_retry_scheduled:
            return
        self._fit_retry_scheduled = True
        QTimer.singleShot(0, self.fit_and_center_image)

    def fit_and_center_image(self) -> None:
        bounds = self._image_bounds()
        if bounds is None:
            return
        self._log_view_state("fit-start")
        if not self._viewport_ready() or min(self.viewport().width(), self.viewport().height()) < 64:
            if self._auto_fit_pending:
                self._schedule_fit_retry()
            return

        self._fit_retry_scheduled = False
        self.setSceneRect(bounds)
        self.resetTransform()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(bounds.center())
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self._auto_fit_pending = False
        self._initial_fit_completed = True
        self._update_zoom_state()
        self._log_view_state("fit-end")

    def fit_to_image(self) -> None:
        self.fit_and_center_image()

    def center_image(self) -> None:
        bounds = self._image_bounds()
        if bounds is None:
            return
        self._logger.debug("center-image bounds=%s", bounds)
        self.centerOn(bounds.center())

    def actual_pixels(self) -> None:
        bounds = self._image_bounds()
        if bounds is None:
            return
        self._logger.debug("actual-pixels bounds=%s", bounds)
        self.resetTransform()
        self.scale(1.0, 1.0)
        self.centerOn(bounds.center())
        self._update_zoom_state()

    def reset_view(self) -> None:
        self._logger.debug("reset-view")
        self.resetTransform()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self._update_zoom_state()

    def reset_view_and_fit(self) -> None:
        self.reset_view()
        self.fit_and_center_image()

    def zoom_percent(self) -> int:
        return self._zoom_percent

    def set_crop_rect(self, rect: QRectF | None) -> None:
        if rect is None:
            if self._crop_item is not None:
                self.scene().removeItem(self._crop_item)
                self._crop_item = None
            self._crop_rect_image = None
            return

        self._crop_rect_image = rect
        scene_rect = self._pixmap_item.mapRectToScene(rect) if self._pixmap_item is not None else rect
        if self._crop_item is None:
            pen = QPen(QColor(255, 209, 102), 2, Qt.PenStyle.DashLine)
            brush = QBrush(QColor(255, 209, 102, 40))
            self._crop_item = self.scene().addRect(scene_rect, pen, brush)
            self._crop_item.setZValue(10)
        else:
            self._crop_item.setRect(scene_rect)

    def current_crop_rect(self) -> QRectF | None:
        return self._crop_rect_image

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
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and self._space_pan_active
        ):
            self.setFocus()
            self._panning = True
            self._space_was_panning = event.button() == Qt.MouseButton.LeftButton and self._space_pan_active
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._user_has_navigated = True
            self._auto_fit_pending = False
            self._logger.debug("pan-start pos=(%s, %s)", event.pos().x(), event.pos().y())
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._pixmap_item is not None and not self._space_pan_active:
            scene_point = self.mapToScene(event.pos())
            image_point = self._pixmap_item.mapFromScene(scene_point)
            if self._pixmap_item.boundingRect().contains(image_point):
                self._draw_start = image_point
                self.set_crop_rect(QRectF(self._draw_start, self._draw_start))
                event.accept()
                return
            event.accept()
            return

        try:
            super().mousePressEvent(event)
        except TypeError:
            pass

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_start is not None:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._user_has_navigated = True
            self._auto_fit_pending = False
            self._logger.debug(
                "pan dx=%s dy=%s h=%s v=%s",
                delta.x(),
                delta.y(),
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value(),
            )
            event.accept()
            return

        if self._draw_start is not None and self._image_size is not None and self._pixmap_item is not None:
            current = self._pixmap_item.mapFromScene(self.mapToScene(event.pos()))
            rect = QRectF(self._draw_start, current).normalized()
            rect = self._clamp_rect(rect)
            self.set_crop_rect(rect)
            event.accept()
            return

        try:
            super().mouseMoveEvent(event)
        except TypeError:
            pass

    def mouseReleaseEvent(self, event) -> None:
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton) and self._panning:
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            self._space_was_panning = False
            self._logger.debug("pan-end")
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._draw_start is not None:
            rect = self.current_crop_rect()
            self._draw_start = None
            if rect is not None:
                self.cropChanged.emit(rect)
            event.accept()
            return

        try:
            super().mouseReleaseEvent(event)
        except TypeError:
            pass

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() == 0:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        current = max(1, self.zoom_percent())
        target = max(1, min(6400, int(round(current * factor))))
        cursor_point = event.position().toPoint()
        scene_before = self.mapToScene(cursor_point)
        self.scale(target / current, target / current)
        scene_after = self.mapToScene(cursor_point)
        delta = scene_after - scene_before
        self.translate(-delta.x(), -delta.y())
        self._zoom_percent = target
        self._user_has_navigated = True
        self._auto_fit_pending = False
        self.zoomChanged.emit(self._zoom_percent)
        self._logger.debug(
            "wheel factor=%.2f zoom=%s cursor=(%.1f, %.1f) delta=(%.4f, %.4f)",
            factor,
            self._zoom_percent,
            event.position().x(),
            event.position().y(),
            delta.x(),
            delta.y(),
        )
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._pixmap_item is not None and self._auto_fit_pending and not self._user_has_navigated:
            self.fit_and_center_image()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = True
            if not self._panning:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._space_pan_active = False
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return
        try:
            super().keyPressEvent(event)
        except TypeError:
            pass

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = False
            if self._space_was_panning and not self._panning:
                self.unsetCursor()
            self._space_was_panning = False
            event.accept()
            return
        try:
            super().keyReleaseEvent(event)
        except TypeError:
            pass

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = QMenu(self)
        menu.addAction(self.fit_action)
        menu.addAction(self.actual_pixels_action)
        menu.addAction(self.center_action)
        menu.addAction(self.reset_view_action)
        menu.exec(event.globalPos())

    def _clamp_rect(self, rect: QRectF) -> QRectF:
        if self._image_size is None:
            return rect
        width, height = self._image_size
        left = max(0.0, min(rect.left(), width))
        top = max(0.0, min(rect.top(), height))
        right = max(0.0, min(rect.right(), width))
        bottom = max(0.0, min(rect.bottom(), height))
        return QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
