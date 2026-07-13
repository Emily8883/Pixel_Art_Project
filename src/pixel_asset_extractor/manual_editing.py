from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from collections import deque
from pathlib import Path
from typing import Iterable
from uuid import uuid4

import numpy as np
from PIL import Image

from .processing import rgb_distance, ui_tolerance_to_distance


RGBA = tuple[int, int, int, int]


def _clamp_u8(value: int) -> int:
    return max(0, min(255, int(value)))


def _image_to_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()


def _array_to_image(array: np.ndarray) -> Image.Image:
    return Image.fromarray(array.astype(np.uint8, copy=False), mode="RGBA")


def _copy_array(array: np.ndarray) -> np.ndarray:
    return np.array(array, copy=True)


def _line_points(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        twice = 2 * err
        if twice >= dy:
            err += dy
            x += sx
        if twice <= dx:
            err += dx
            y += sy
    return points


def _brush_offsets(size: int) -> list[tuple[int, int]]:
    half = size // 2
    offsets: list[tuple[int, int]] = []
    for y in range(-half, -half + size):
        for x in range(-half, -half + size):
            offsets.append((x, y))
    return offsets


def _checksum_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


@dataclass(slots=True)
class ManualEditSnapshot:
    label: str
    final_image: np.ndarray
    floating_selection: np.ndarray | None = None
    selection_rect: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class ManualEditDocument:
    """In-memory pixel editor state.

    The image representation is a NumPy RGBA array so edits can be applied
    pixel-exactly without repeatedly converting between Pillow and Qt during
    mouse movement.
    """

    raw_crop: Image.Image
    auto_clean: Image.Image
    final_edited: Image.Image | None = None
    background_rgba: RGBA | None = None
    cleanup_settings_checksum: str = ""
    source_sheet_checksum: str = ""
    history_limit: int = 100
    _undo: list[ManualEditSnapshot] = field(default_factory=list, init=False, repr=False)
    _redo: list[ManualEditSnapshot] = field(default_factory=list, init=False, repr=False)
    _selection_rect: tuple[int, int, int, int] | None = field(default=None, init=False, repr=False)
    _clipboard: np.ndarray | None = field(default=None, init=False, repr=False)
    _floating_selection: np.ndarray | None = field(default=None, init=False, repr=False)
    _floating_origin: tuple[int, int] | None = field(default=None, init=False, repr=False)
    _dirty: bool = field(default=False, init=False, repr=False)
    _raw: np.ndarray = field(init=False, repr=False)
    _auto: np.ndarray = field(init=False, repr=False)
    _final: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._raw = _image_to_array(self.raw_crop)
        self._auto = _image_to_array(self.auto_clean)
        if self.final_edited is None:
            self._final = _copy_array(self._auto)
        else:
            self._final = _image_to_array(self.final_edited)
        self._sync_images()

    @property
    def size(self) -> tuple[int, int]:
        return self._final.shape[1], self._final.shape[0]

    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    @property
    def selection_rect(self) -> tuple[int, int, int, int] | None:
        return self._selection_rect

    @property
    def has_floating_selection(self) -> bool:
        return self._floating_selection is not None

    @property
    def undo_count(self) -> int:
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        return len(self._redo)

    def raw_image(self) -> Image.Image:
        return _array_to_image(self._raw)

    def auto_image(self) -> Image.Image:
        return _array_to_image(self._auto)

    def final_image(self) -> Image.Image:
        return _array_to_image(self._final)

    def current_image(self, mode: str) -> Image.Image:
        if mode == "raw":
            return self.raw_image()
        if mode == "auto":
            return self.auto_image()
        if mode == "final":
            return self.final_image()
        raise ValueError(f"Unknown view mode: {mode}")

    def reset_manual_edits(self) -> None:
        self._push_history("reset_manual_edits")
        self._final = _copy_array(self._auto)
        self._selection_rect = None
        self._floating_selection = None
        self._floating_origin = None
        self._sync_images()
        self._dirty = True

    def reset_background_removal(self, new_auto_clean: Image.Image) -> None:
        self._push_history("reset_background_removal")
        self._auto = _image_to_array(new_auto_clean)
        self._final = _copy_array(self._auto)
        self._selection_rect = None
        self._floating_selection = None
        self._floating_origin = None
        self._sync_images()
        self._dirty = True

    def set_final_image(self, image: Image.Image) -> None:
        self._final = _image_to_array(image)
        self._sync_images()

    def rebase_images(
        self,
        raw_crop: Image.Image,
        auto_clean: Image.Image,
        final_edited: Image.Image | None = None,
        *,
        clear_history: bool = True,
    ) -> None:
        self.raw_crop = raw_crop
        self.auto_clean = auto_clean
        self.final_edited = final_edited if final_edited is not None else auto_clean
        self._raw = _image_to_array(raw_crop)
        self._auto = _image_to_array(auto_clean)
        self._final = _image_to_array(self.final_edited)
        if clear_history:
            self._undo.clear()
            self._redo.clear()
            self._selection_rect = None
            self._floating_selection = None
            self._floating_origin = None
        self._sync_images()

    def clone_final_image(self) -> Image.Image:
        return self.final_image()

    def sample_rgba(self, x: int, y: int, mode: str = "final") -> RGBA:
        array = self._image_array_for_mode(mode)
        if not self._in_bounds(x, y, array):
            raise IndexError("pixel is outside the image bounds")
        return tuple(int(v) for v in array[y, x])  # type: ignore[return-value]

    def pick_color(self, x: int, y: int, mode: str = "final") -> RGBA:
        return self.sample_rgba(x, y, mode)

    def apply_pencil(self, points: Iterable[tuple[int, int]], color: RGBA, brush_size: int = 1) -> None:
        self._apply_brush(points, color, brush_size, erase=False)

    def apply_eraser(self, points: Iterable[tuple[int, int]], brush_size: int = 1) -> None:
        self._apply_brush(points, (0, 0, 0, 0), brush_size, erase=True)

    def flood_fill(
        self,
        x: int,
        y: int,
        color: RGBA,
        *,
        exact_color: bool = True,
        tolerance_ui: int = 0,
        connectivity: int = 4,
    ) -> int:
        if not self._in_bounds(x, y, self._final):
            return 0
        target = tuple(int(v) for v in self._final[y, x])
        replacement = tuple(_clamp_u8(v) for v in color)
        if target == replacement:
            return 0

        self._push_history("flood_fill")
        mask = self._flood_mask(x, y, target, exact_color, tolerance_ui, connectivity)
        self._final[mask] = replacement
        self._sync_images()
        self._dirty = True
        return int(np.count_nonzero(mask))

    def select_rect(self, left: int, top: int, width: int, height: int) -> tuple[int, int, int, int]:
        x1 = max(0, min(left, self._final.shape[1]))
        y1 = max(0, min(top, self._final.shape[0]))
        x2 = max(0, min(left + width, self._final.shape[1]))
        y2 = max(0, min(top + height, self._final.shape[0]))
        if x2 <= x1 or y2 <= y1:
            self._selection_rect = None
            return (x1, y1, 0, 0)
        self._selection_rect = (x1, y1, x2 - x1, y2 - y1)
        return self._selection_rect

    def clear_selection(self) -> None:
        self._selection_rect = None
        self._floating_selection = None
        self._floating_origin = None

    def delete_selection(self) -> None:
        if self._selection_rect is None:
            return
        self._push_history("selection_delete")
        x, y, w, h = self._selection_rect
        self._final[y : y + h, x : x + w] = (0, 0, 0, 0)
        self.clear_selection()
        self._sync_images()
        self._dirty = True

    def copy_selection(self) -> None:
        if self._selection_rect is None:
            self._clipboard = None
            return
        x, y, w, h = self._selection_rect
        self._clipboard = _copy_array(self._final[y : y + h, x : x + w])

    def cut_selection(self) -> None:
        self.copy_selection()
        self.delete_selection()

    def paste_clipboard(self, x: int, y: int) -> bool:
        if self._clipboard is None:
            return False
        self._push_history("paste")
        h, w = self._clipboard.shape[:2]
        self._floating_selection = _copy_array(self._clipboard)
        self._floating_origin = (x, y)
        self._selection_rect = (x, y, w, h)
        self._composite_floating()
        self._sync_images()
        self._dirty = True
        return True

    def move_selection(self, dx: int, dy: int, *, commit: bool = False) -> bool:
        if self._selection_rect is None:
            return False
        self._push_history("selection_move")
        x, y, w, h = self._selection_rect
        block = _copy_array(self._final[y : y + h, x : x + w])
        self._final[y : y + h, x : x + w] = (0, 0, 0, 0)
        nx = max(0, min(self._final.shape[1] - w, x + dx))
        ny = max(0, min(self._final.shape[0] - h, y + dy))
        self._final[ny : ny + h, nx : nx + w] = block
        self._selection_rect = (nx, ny, w, h)
        if commit:
            self._floating_selection = None
            self._floating_origin = None
        self._sync_images()
        self._dirty = True
        return True

    def commit_floating_selection(self) -> bool:
        if self._floating_selection is None or self._selection_rect is None:
            return False
        self._push_history("paste_commit")
        self._composite_floating()
        self._floating_selection = None
        self._floating_origin = None
        self._sync_images()
        self._dirty = True
        return True

    def cancel_floating_selection(self) -> None:
        if self._floating_selection is None:
            return
        self.undo()

    def undo(self) -> bool:
        if not self._undo:
            return False
        snapshot = self._undo.pop()
        self._redo.append(self._snapshot("redo"))
        self._restore(snapshot)
        self._dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        snapshot = self._redo.pop()
        self._undo.append(self._snapshot("undo"))
        self._restore(snapshot)
        self._dirty = True
        return True

    def alpha_preview(self) -> Image.Image:
        alpha = self._final[:, :, 3]
        rgba = np.zeros_like(self._final)
        rgba[:, :, 0] = alpha
        rgba[:, :, 1] = alpha
        rgba[:, :, 2] = alpha
        rgba[:, :, 3] = 255
        return _array_to_image(rgba)

    def edge_highlight(self, connectivity: int = 4, overlay_color: RGBA = (255, 0, 0, 255)) -> Image.Image:
        mask = self._edge_mask(connectivity)
        return self._overlay_mask(mask, overlay_color)

    def suspected_halo_highlight(
        self,
        background_rgba: RGBA | None,
        tolerance_ui: int = 8,
        overlay_color: RGBA = (255, 128, 0, 255),
    ) -> Image.Image:
        mask = self.suspected_halo_mask(background_rgba, tolerance_ui)
        return self._overlay_mask(mask, overlay_color)

    def suspected_halo_mask(self, background_rgba: RGBA | None, tolerance_ui: int = 8) -> np.ndarray:
        if background_rgba is None:
            return np.zeros(self._final.shape[:2], dtype=bool)
        rgb = self._final[:, :, :3].astype(np.int32)
        background = np.array(background_rgba[:3], dtype=np.int32)
        delta = rgb - background
        dist = np.sqrt(np.sum(delta * delta, axis=2, dtype=np.int64), dtype=np.float64)
        alpha = self._final[:, :, 3] > 0
        return alpha & (dist <= ui_tolerance_to_distance(tolerance_ui))

    def isolated_pixel_mask(self) -> np.ndarray:
        alpha = self._final[:, :, 3] > 0
        height, width = alpha.shape
        result = np.zeros_like(alpha)
        for y in range(height):
            for x in range(width):
                if not alpha[y, x]:
                    continue
                neighbors = self._neighbor_coords(x, y, width, height, 8)
                if not any(alpha[ny, nx] for ny, nx in neighbors):
                    result[y, x] = True
        return result

    def semi_transparent_mask(self) -> np.ndarray:
        alpha = self._final[:, :, 3]
        return (alpha > 0) & (alpha < 255)

    def export_final(self, output_path: str | Path) -> Path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.final_image().save(destination, format="PNG")
        return destination

    def thumbnail(self, size: int = 64) -> Image.Image:
        source = self.final_image() if self.has_manual_pixels() else self.auto_image() if self.auto_clean is not None else self.raw_image()
        ratio = min(size / source.width if source.width else 1.0, size / source.height if source.height else 1.0)
        target_size = (max(1, int(round(source.width * ratio))), max(1, int(round(source.height * ratio))))
        resized = source.resize(target_size, resample=Image.Resampling.NEAREST)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        checker = self._checkerboard_image(size)
        canvas.alpha_composite(checker)
        offset = ((size - target_size[0]) // 2, (size - target_size[1]) // 2)
        canvas.alpha_composite(resized, dest=offset)
        return canvas

    def has_manual_pixels(self) -> bool:
        return self._dirty or bool(self._undo) or self._selection_rect is not None

    def to_sidecar_payload(self, asset_uuid: str, sidecar_relpath: str) -> dict[str, object]:
        return {
            "asset_uuid": asset_uuid,
            "manual_edit_sidecar": sidecar_relpath,
            "manual_edit_checksum": self.checksum(),
            "crop_width": self.size[0],
            "crop_height": self.size[1],
            "source_sheet_checksum_at_edit_time": self.source_sheet_checksum,
            "auto_clean_settings_checksum": self.cleanup_settings_checksum,
            "manual_edit_modified_at": _timestamp(),
        }

    def checksum(self) -> str:
        return _checksum_bytes(self.final_image().tobytes())

    def save_sidecar(self, output_path: str | Path) -> Path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + f".{uuid4().hex}.tmp")
        self.final_image().save(tmp, format="PNG")
        if destination.exists():
            destination.unlink()
        tmp.replace(destination)
        return destination

    @staticmethod
    def load_sidecar(path: str | Path) -> Image.Image:
        with Image.open(path) as image:
            return image.convert("RGBA")

    def _apply_brush(self, points: Iterable[tuple[int, int]], color: RGBA, brush_size: int, erase: bool) -> None:
        clamped_size = max(1, min(5, int(brush_size)))
        coords = list(points)
        if not coords:
            return
        self._push_history("eraser" if erase else "pencil")
        offsets = _brush_offsets(clamped_size)
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            for px, py in _line_points(x0, y0, x1, y1):
                self._stamp(px, py, color, offsets)
        x, y = coords[-1]
        self._stamp(x, y, color, offsets)
        self._sync_images()
        self._dirty = True

    def _stamp(self, x: int, y: int, color: RGBA, offsets: list[tuple[int, int]]) -> None:
        height, width = self._final.shape[:2]
        for dx, dy in offsets:
            px = x + dx
            py = y + dy
            if 0 <= px < width and 0 <= py < height:
                self._final[py, px] = color

    def _flood_mask(
        self,
        x: int,
        y: int,
        target: RGBA,
        exact_color: bool,
        tolerance_ui: int,
        connectivity: int,
    ) -> np.ndarray:
        if connectivity not in (4, 8):
            raise ValueError("connectivity must be 4 or 8")
        height, width = self._final.shape[:2]
        mask = np.zeros((height, width), dtype=bool)
        queue: deque[tuple[int, int]] = deque([(x, y)])
        visited = np.zeros_like(mask)
        if connectivity == 4:
            neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1))
        else:
            neighbors = (
                (1, 0),
                (-1, 0),
                (0, 1),
                (0, -1),
                (1, 1),
                (1, -1),
                (-1, 1),
                (-1, -1),
            )
        while queue:
            px, py = queue.popleft()
            if visited[py, px]:
                continue
            visited[py, px] = True
            current = tuple(int(v) for v in self._final[py, px])
            if exact_color:
                matches = current == target
            else:
                distance = self._rgba_distance(current, target)
                matches = distance <= ui_tolerance_to_distance(tolerance_ui)
            if not matches:
                continue
            mask[py, px] = True
            for dx, dy in neighbors:
                nx = px + dx
                ny = py + dy
                if 0 <= nx < width and 0 <= ny < height and not visited[ny, nx]:
                    queue.append((nx, ny))
        return mask

    def _edge_mask(self, connectivity: int = 4) -> np.ndarray:
        alpha = self._final[:, :, 3] > 0
        height, width = alpha.shape
        result = np.zeros_like(alpha)
        if connectivity == 4:
            neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1))
        else:
            neighbors = (
                (1, 0),
                (-1, 0),
                (0, 1),
                (0, -1),
                (1, 1),
                (1, -1),
                (-1, 1),
                (-1, -1),
            )
        for y in range(height):
            for x in range(width):
                if not alpha[y, x]:
                    continue
                for dx, dy in neighbors:
                    nx = x + dx
                    ny = y + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height or not alpha[ny, nx]:
                        result[y, x] = True
                        break
        return result

    def _overlay_mask(self, mask: np.ndarray, overlay_color: RGBA) -> Image.Image:
        overlay = np.zeros_like(self._final)
        overlay[:, :, :] = (0, 0, 0, 0)
        overlay[mask] = overlay_color
        return _array_to_image(overlay)

    def _checkerboard_image(self, size: int) -> Image.Image:
        tile = Image.new("RGBA", (size, size), (200, 200, 200, 255))
        pixels = tile.load()
        step = max(4, size // 8)
        for y in range(size):
            for x in range(size):
                if ((x // step) + (y // step)) % 2 == 0:
                    pixels[x, y] = (225, 225, 225, 255)
        return tile

    def _restore(self, snapshot: ManualEditSnapshot) -> None:
        self._final = snapshot.final_image.copy()
        self._floating_selection = snapshot.floating_selection.copy() if snapshot.floating_selection is not None else None
        self._selection_rect = snapshot.selection_rect
        self._sync_images()

    def _snapshot(self, label: str) -> ManualEditSnapshot:
        return ManualEditSnapshot(
            label=label,
            final_image=_copy_array(self._final),
            floating_selection=_copy_array(self._floating_selection) if self._floating_selection is not None else None,
            selection_rect=self._selection_rect,
        )

    def _push_history(self, label: str) -> None:
        self._undo.append(self._snapshot(label))
        if len(self._undo) > self.history_limit:
            self._undo = self._undo[-self.history_limit :]
        self._redo.clear()

    def _sync_images(self) -> None:
        self.raw_crop = _array_to_image(self._raw)
        self.auto_clean = _array_to_image(self._auto)
        self.final_edited = _array_to_image(self._final)

    def _composite_floating(self) -> None:
        if self._floating_selection is None or self._selection_rect is None:
            return
        x, y, w, h = self._selection_rect
        self._final[y : y + h, x : x + w] = self._floating_selection[:h, :w]

    def _image_array_for_mode(self, mode: str) -> np.ndarray:
        if mode == "raw":
            return self._raw
        if mode == "auto":
            return self._auto
        if mode == "final":
            return self._final
        raise ValueError(f"Unknown mode: {mode}")

    def _in_bounds(self, x: int, y: int, array: np.ndarray) -> bool:
        return 0 <= x < array.shape[1] and 0 <= y < array.shape[0]

    def _rgba_distance(self, a: RGBA, b: RGBA) -> float:
        return rgb_distance(a[:3], b[:3]) + abs(a[3] - b[3])

    def _neighbor_coords(self, x: int, y: int, width: int, height: int, connectivity: int) -> list[tuple[int, int]]:
        if connectivity == 4:
            offsets = ((1, 0), (-1, 0), (0, 1), (0, -1))
        else:
            offsets = (
                (1, 0),
                (-1, 0),
                (0, 1),
                (0, -1),
                (1, 1),
                (1, -1),
                (-1, 1),
                (-1, -1),
            )
        result = []
        for dx, dy in offsets:
            nx = x + dx
            ny = y + dy
            if 0 <= nx < width and 0 <= ny < height:
                result.append((nx, ny))
        return result


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def manual_edit_checksum(image: Image.Image) -> str:
    return _checksum_bytes(image.convert("RGBA").tobytes())


def selection_bounds_to_rect(bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, w, h = bounds
    return max(0, x), max(0, y), max(0, w), max(0, h)


def compute_settings_checksum(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return _checksum_bytes(encoded)


def shadow_candidate_mask(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[:, :, 3] > 0
    rgb = rgba[:, :, :3].astype(np.int32)
    brightness = np.mean(rgb, axis=2)
    saturation = rgb.max(axis=2) - rgb.min(axis=2)
    lower_half = np.zeros(alpha.shape, dtype=bool)
    lower_half[alpha.shape[0] // 2 :, :] = True
    return alpha & lower_half & (brightness < 80) & (saturation < 60)
