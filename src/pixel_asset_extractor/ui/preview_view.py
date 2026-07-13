from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from ..image_tools import pil_image_to_qpixmap


PREVIEW_MODES = ("before", "after", "split")
PREVIEW_BACKGROUNDS = ("checkerboard", "white", "black", "bright red", "bright green")
CHECKERBOARD_SIZES = {"small": 8, "medium": 16, "large": 32}


@dataclass(frozen=True, slots=True)
class PickedColor:
    rgba: tuple[int, int, int, int]
    source: str


class CropPreviewView(QGraphicsView):
    zoomChanged = Signal(int)
    colorPicked = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._raw_image: Image.Image | None = None
        self._clean_image: Image.Image | None = None
        self._raw_pixmap: QPixmap | None = None
        self._clean_pixmap: QPixmap | None = None
        self._raw_item: QGraphicsPixmapItem | None = None
        self._clean_item: QGraphicsPixmapItem | None = None
        self._divider_item: QGraphicsLineItem | None = None
        self._mode = "before"
        self._background_style = "checkerboard"
        self._checkerboard_size = "medium"
        self._zoom_percent = 100
        self._eyedropper_active = False
        self._scene_layout = {
            "raw_rect": QRectF(),
            "clean_rect": QRectF(),
        }

    def set_images(self, raw_image: Image.Image | None, clean_image: Image.Image | None) -> None:
        self._raw_image = raw_image.convert("RGBA") if raw_image is not None else None
        self._clean_image = clean_image.convert("RGBA") if clean_image is not None else None
        self._raw_pixmap = pil_image_to_qpixmap(self._raw_image) if self._raw_image is not None else None
        self._clean_pixmap = pil_image_to_qpixmap(self._clean_image) if self._clean_image is not None else None
        self._rebuild_scene()

    def set_preview_mode(self, mode: str) -> None:
        if mode not in PREVIEW_MODES:
            raise ValueError(f"Unsupported preview mode: {mode}")
        self._mode = mode
        self._rebuild_scene()

    def set_background_style(self, style: str, checkerboard_size: str) -> None:
        if style not in PREVIEW_BACKGROUNDS:
            raise ValueError(f"Unsupported background style: {style}")
        if checkerboard_size not in CHECKERBOARD_SIZES:
            raise ValueError(f"Unsupported checkerboard size: {checkerboard_size}")
        self._background_style = style
        self._checkerboard_size = checkerboard_size
        self._apply_background()

    def set_zoom_percent(self, zoom_percent: int) -> None:
        zoom_percent = max(25, min(3200, int(zoom_percent)))
        if zoom_percent == self._zoom_percent:
            return
        self._zoom_percent = zoom_percent
        self.resetTransform()
        self.scale(self._zoom_percent / 100.0, self._zoom_percent / 100.0)
        self.zoomChanged.emit(self._zoom_percent)

    def zoom_percent(self) -> int:
        return self._zoom_percent

    def set_eyedropper_active(self, active: bool) -> None:
        self._eyedropper_active = active
        self.setCursor(Qt.CursorShape.CrossCursor if active else Qt.CursorShape.ArrowCursor)

    def is_eyedropper_active(self) -> bool:
        return self._eyedropper_active

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() == 0:
            return
        delta = 25 if event.angleDelta().y() > 0 else -25
        self.set_zoom_percent(self._zoom_percent + delta)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if not self._eyedropper_active:
            super().mousePressEvent(event)
            return

        color = self.pick_color(event.position().toPoint())
        if color is not None:
            self.colorPicked.emit(color)
            event.accept()
            return
        super().mousePressEvent(event)

    def pick_color(self, viewport_pos) -> PickedColor | None:
        scene_pos = self.mapToScene(viewport_pos)
        if self._mode == "split":
            if self._scene_layout["raw_rect"].contains(scene_pos):
                color = self._sample_image(self._raw_image, self._scene_layout["raw_rect"], scene_pos)
                if color is not None:
                    return PickedColor(color, "raw")
            if self._scene_layout["clean_rect"].contains(scene_pos):
                color = self._sample_image(self._clean_image, self._scene_layout["clean_rect"], scene_pos)
                if color is not None:
                    return PickedColor(color, "clean")
            return None

        image = self._raw_image if self._mode == "before" else self._clean_image
        rect = self._scene_layout["raw_rect"]
        color = self._sample_image(image, rect, scene_pos)
        if color is None:
            return None
        return PickedColor(color, self._mode)

    def _sample_image(
        self,
        image: Image.Image | None,
        rect: QRectF,
        scene_pos: QPointF,
    ) -> tuple[int, int, int, int] | None:
        if image is None or not rect.contains(scene_pos):
            return None
        x = int(scene_pos.x() - rect.left())
        y = int(scene_pos.y() - rect.top())
        if x < 0 or y < 0 or x >= image.width or y >= image.height:
            return None
        return tuple(image.getpixel((x, y)))  # type: ignore[return-value]

    def _rebuild_scene(self) -> None:
        scene = self.scene()
        scene.clear()
        self._raw_item = None
        self._clean_item = None
        self._divider_item = None
        raw_rect = QRectF()
        clean_rect = QRectF()
        gap = 16

        if self._mode in ("before", "split") and self._raw_pixmap is not None:
            self._raw_item = scene.addPixmap(self._raw_pixmap)
            self._raw_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
            self._raw_item.setPos(0, 0)
            raw_rect = QRectF(0, 0, self._raw_pixmap.width(), self._raw_pixmap.height())

        if self._mode == "after" and self._clean_pixmap is not None:
            self._clean_item = scene.addPixmap(self._clean_pixmap)
            self._clean_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
            self._clean_item.setPos(0, 0)
            clean_rect = QRectF(0, 0, self._clean_pixmap.width(), self._clean_pixmap.height())

        if self._mode == "split":
            if self._raw_pixmap is not None:
                self._raw_item = scene.addPixmap(self._raw_pixmap)
                self._raw_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
                self._raw_item.setPos(0, 0)
                raw_rect = QRectF(0, 0, self._raw_pixmap.width(), self._raw_pixmap.height())
            if self._clean_pixmap is not None:
                x_offset = (raw_rect.width() + gap) if not raw_rect.isNull() else 0
                self._clean_item = scene.addPixmap(self._clean_pixmap)
                self._clean_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
                self._clean_item.setPos(x_offset, 0)
                clean_rect = QRectF(x_offset, 0, self._clean_pixmap.width(), self._clean_pixmap.height())
                self._divider_item = scene.addLine(
                    x_offset - gap / 2,
                    0,
                    x_offset - gap / 2,
                    max(raw_rect.height(), clean_rect.height()),
                    QPen(QColor(230, 230, 230), 1, Qt.PenStyle.SolidLine),
                )

        self._scene_layout["raw_rect"] = raw_rect
        self._scene_layout["clean_rect"] = clean_rect

        if not raw_rect.isNull() and not clean_rect.isNull():
            scene_rect = raw_rect.united(clean_rect).adjusted(-8, -8, 8, 8)
        elif not raw_rect.isNull():
            scene_rect = raw_rect.adjusted(-8, -8, 8, 8)
        elif not clean_rect.isNull():
            scene_rect = clean_rect.adjusted(-8, -8, 8, 8)
        else:
            scene_rect = QRectF(0, 0, 1, 1)
        scene.setSceneRect(scene_rect)
        self._apply_background()
        self.resetTransform()
        self.scale(self._zoom_percent / 100.0, self._zoom_percent / 100.0)

    def _apply_background(self) -> None:
        if self._background_style == "checkerboard":
            self.setBackgroundBrush(QBrush(self._checkerboard_brush()))
        elif self._background_style == "white":
            self.setBackgroundBrush(QBrush(QColor(255, 255, 255)))
        elif self._background_style == "black":
            self.setBackgroundBrush(QBrush(QColor(0, 0, 0)))
        elif self._background_style == "bright red":
            self.setBackgroundBrush(QBrush(QColor(255, 0, 0)))
        elif self._background_style == "bright green":
            self.setBackgroundBrush(QBrush(QColor(0, 255, 0)))

    def _checkerboard_brush(self) -> QBrush:
        size = CHECKERBOARD_SIZES[self._checkerboard_size]
        tile = QPixmap(size * 2, size * 2)
        tile.fill(QColor(210, 210, 210))
        painter = QPainter(tile)
        painter.fillRect(0, 0, size, size, QColor(180, 180, 180))
        painter.fillRect(size, size, size, size, QColor(180, 180, 180))
        painter.end()
        return QBrush(tile)
