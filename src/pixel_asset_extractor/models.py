from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .processing import ui_tolerance_to_distance


@dataclass(frozen=True, slots=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int

    def to_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height

    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0

    def to_dict(self) -> dict[str, int]:
        return dict(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CropRect":
        return cls(
            x=int(payload["x"]),
            y=int(payload["y"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
        )


@dataclass(frozen=True, slots=True)
class CropConfig:
    source_image: str
    crop_rect: CropRect
    background_rgba: tuple[int, int, int, int] | None = None
    tolerance_ui: int = 5
    tolerance_threshold: float = field(default_factory=lambda: ui_tolerance_to_distance(5))
    connected_background_only: bool = True
    connectivity: int = 4
    output_raw_filename: str = ""
    output_clean_filename: str = ""
    export_directory: str | None = None
    config_version: int = 2

    def to_dict(self) -> dict[str, object]:
        return {
            "config_version": self.config_version,
            "source_image": self.source_image,
            "crop_rect": self.crop_rect.to_dict(),
            "background_rgba": list(self.background_rgba) if self.background_rgba is not None else None,
            "tolerance_ui": self.tolerance_ui,
            "tolerance_threshold": self.tolerance_threshold,
            "connected_background_only": self.connected_background_only,
            "connectivity": self.connectivity,
            "output_raw_filename": self.output_raw_filename,
            "output_clean_filename": self.output_clean_filename,
            "export_directory": self.export_directory,
        }

    @property
    def source_path(self) -> Path:
        return Path(self.source_image)
