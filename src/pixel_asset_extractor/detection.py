from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

from .models import CropRect


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProposalStatus(str, Enum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    modified = "modified"
    assigned = "assigned"
    ignored = "ignored"


@dataclass(slots=True)
class BackgroundSample:
    sample_uuid: str = field(default_factory=lambda: str(uuid4()))
    rgba: tuple[int, int, int, int] = (0, 0, 0, 255)
    source: str = "manual"
    label: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_uuid": self.sample_uuid,
            "rgba": list(self.rgba),
            "source": self.source,
            "label": self.label,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BackgroundSample":
        rgba = payload.get("rgba", (0, 0, 0, 255))
        return cls(
            sample_uuid=str(payload.get("sample_uuid", str(uuid4()))),
            rgba=tuple(int(v) for v in rgba) if isinstance(rgba, (list, tuple)) and len(rgba) == 4 else (0, 0, 0, 255),
            source=str(payload.get("source", "manual")),
            label=str(payload.get("label", "")),
            created_at=str(payload.get("created_at", utc_now_iso())),
            modified_at=str(payload.get("modified_at", utc_now_iso())),
        )


@dataclass(slots=True)
class ExclusionZone:
    zone_uuid: str = field(default_factory=lambda: str(uuid4()))
    source_sheet_uuid: str = ""
    rect: CropRect = field(default_factory=lambda: CropRect(0, 0, 1, 1))
    zone_type: str = "manual_rectangle"
    name: str = "Exclusion Zone"
    enabled: bool = True
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_uuid": self.zone_uuid,
            "source_sheet_uuid": self.source_sheet_uuid,
            "rect": self.rect.to_dict(),
            "zone_type": self.zone_type,
            "name": self.name,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExclusionZone":
        rect_payload = payload.get("rect", {})
        return cls(
            zone_uuid=str(payload.get("zone_uuid", str(uuid4()))),
            source_sheet_uuid=str(payload.get("source_sheet_uuid", "")),
            rect=CropRect.from_dict(rect_payload) if isinstance(rect_payload, dict) else CropRect(0, 0, 1, 1),
            zone_type=str(payload.get("zone_type", "manual_rectangle")),
            name=str(payload.get("name", "Exclusion Zone")),
            enabled=bool(payload.get("enabled", True)),
            created_at=str(payload.get("created_at", utc_now_iso())),
            modified_at=str(payload.get("modified_at", utc_now_iso())),
        )


@dataclass(slots=True)
class DetectionSettingsModel:
    preset_name: str = "Broad Search"
    methods: tuple[str, ...] = ("background_difference", "edge_based", "connected_components", "color_variance")
    analysis_mode: str = "full_source_sheet"
    analysis_region: CropRect | None = None
    background_tolerance: int = 24
    ignore_top_title_strip: bool = False
    ignore_outer_border: bool = False
    ignore_text_heavy_regions: bool = True
    ignore_below_min_size: bool = True
    ignore_above_max_size: bool = True
    ignore_near_full_width_panels: bool = True
    min_width: int = 8
    min_height: int = 8
    max_width: int = 4096
    max_height: int = 4096
    min_area: int = 32
    max_area: int = 1_000_000
    merge_distance: int = 4
    close_gap_radius: int = 1
    overlap_merge_threshold: float = 0.3
    aspect_ratio_min: float = 0.15
    aspect_ratio_max: float = 8.0
    minimum_edge_density: float = 0.0
    maximum_text_likelihood: float = 0.75
    include_disconnected_nearby_pieces: bool = True
    connectivity: int = 8
    padding: int = 2
    proposal_limit: int = 200
    text_rejection_enabled: bool = True
    duplicate_iou_threshold: float = 0.7
    duplicate_containment_threshold: float = 0.92
    merge_overlapping: bool = True
    keep_all_candidates: bool = False
    show_proposals: bool = True
    hide_rejected: bool = False
    show_assigned: bool = True
    show_confidence_labels: bool = True
    show_proposal_numbers: bool = True
    show_exclusion_zones: bool = True
    background_samples: list[BackgroundSample] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "preset_name": self.preset_name,
            "methods": list(self.methods),
            "analysis_mode": self.analysis_mode,
            "analysis_region": self.analysis_region.to_dict() if self.analysis_region else None,
            "background_tolerance": self.background_tolerance,
            "ignore_top_title_strip": self.ignore_top_title_strip,
            "ignore_outer_border": self.ignore_outer_border,
            "ignore_text_heavy_regions": self.ignore_text_heavy_regions,
            "ignore_below_min_size": self.ignore_below_min_size,
            "ignore_above_max_size": self.ignore_above_max_size,
            "ignore_near_full_width_panels": self.ignore_near_full_width_panels,
            "min_width": self.min_width,
            "min_height": self.min_height,
            "max_width": self.max_width,
            "max_height": self.max_height,
            "min_area": self.min_area,
            "max_area": self.max_area,
            "merge_distance": self.merge_distance,
            "close_gap_radius": self.close_gap_radius,
            "overlap_merge_threshold": self.overlap_merge_threshold,
            "aspect_ratio_min": self.aspect_ratio_min,
            "aspect_ratio_max": self.aspect_ratio_max,
            "minimum_edge_density": self.minimum_edge_density,
            "maximum_text_likelihood": self.maximum_text_likelihood,
            "include_disconnected_nearby_pieces": self.include_disconnected_nearby_pieces,
            "connectivity": self.connectivity,
            "padding": self.padding,
            "proposal_limit": self.proposal_limit,
            "text_rejection_enabled": self.text_rejection_enabled,
            "duplicate_iou_threshold": self.duplicate_iou_threshold,
            "duplicate_containment_threshold": self.duplicate_containment_threshold,
            "merge_overlapping": self.merge_overlapping,
            "keep_all_candidates": self.keep_all_candidates,
            "show_proposals": self.show_proposals,
            "hide_rejected": self.hide_rejected,
            "show_assigned": self.show_assigned,
            "show_confidence_labels": self.show_confidence_labels,
            "show_proposal_numbers": self.show_proposal_numbers,
            "show_exclusion_zones": self.show_exclusion_zones,
            "background_samples": [sample.to_dict() for sample in self.background_samples],
        }
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "DetectionSettingsModel":
        payload = payload or {}
        methods = payload.get("methods", ())
        analysis_region = payload.get("analysis_region")
        background_samples = payload.get("background_samples", [])
        return cls(
            preset_name=str(payload.get("preset_name", "Broad Search")),
            methods=tuple(str(item) for item in methods) if isinstance(methods, (list, tuple)) else ("background_difference",),
            analysis_mode=str(payload.get("analysis_mode", "full_source_sheet")),
            analysis_region=CropRect.from_dict(analysis_region) if isinstance(analysis_region, dict) else None,
            background_tolerance=int(payload.get("background_tolerance", 24)),
            ignore_top_title_strip=bool(payload.get("ignore_top_title_strip", False)),
            ignore_outer_border=bool(payload.get("ignore_outer_border", False)),
            ignore_text_heavy_regions=bool(payload.get("ignore_text_heavy_regions", True)),
            ignore_below_min_size=bool(payload.get("ignore_below_min_size", True)),
            ignore_above_max_size=bool(payload.get("ignore_above_max_size", True)),
            ignore_near_full_width_panels=bool(payload.get("ignore_near_full_width_panels", True)),
            min_width=int(payload.get("min_width", 8)),
            min_height=int(payload.get("min_height", 8)),
            max_width=int(payload.get("max_width", 4096)),
            max_height=int(payload.get("max_height", 4096)),
            min_area=int(payload.get("min_area", 32)),
            max_area=int(payload.get("max_area", 1_000_000)),
            merge_distance=int(payload.get("merge_distance", 4)),
            close_gap_radius=int(payload.get("close_gap_radius", 1)),
            overlap_merge_threshold=float(payload.get("overlap_merge_threshold", 0.3)),
            aspect_ratio_min=float(payload.get("aspect_ratio_min", 0.15)),
            aspect_ratio_max=float(payload.get("aspect_ratio_max", 8.0)),
            minimum_edge_density=float(payload.get("minimum_edge_density", 0.0)),
            maximum_text_likelihood=float(payload.get("maximum_text_likelihood", 0.75)),
            include_disconnected_nearby_pieces=bool(payload.get("include_disconnected_nearby_pieces", True)),
            connectivity=8 if int(payload.get("connectivity", 8)) == 8 else 4,
            padding=int(payload.get("padding", 2)),
            proposal_limit=int(payload.get("proposal_limit", 200)),
            text_rejection_enabled=bool(payload.get("text_rejection_enabled", True)),
            duplicate_iou_threshold=float(payload.get("duplicate_iou_threshold", 0.7)),
            duplicate_containment_threshold=float(payload.get("duplicate_containment_threshold", 0.92)),
            merge_overlapping=bool(payload.get("merge_overlapping", True)),
            keep_all_candidates=bool(payload.get("keep_all_candidates", False)),
            show_proposals=bool(payload.get("show_proposals", True)),
            hide_rejected=bool(payload.get("hide_rejected", False)),
            show_assigned=bool(payload.get("show_assigned", True)),
            show_confidence_labels=bool(payload.get("show_confidence_labels", True)),
            show_proposal_numbers=bool(payload.get("show_proposal_numbers", True)),
            show_exclusion_zones=bool(payload.get("show_exclusion_zones", True)),
            background_samples=[BackgroundSample.from_dict(sample) for sample in background_samples if isinstance(sample, dict)],
        )

    def checksum(self) -> str:
        payload = json_dumps_stable(self.to_dict())
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class CropProposal:
    proposal_uuid: str = field(default_factory=lambda: str(uuid4()))
    source_sheet_uuid: str = ""
    rect: CropRect = field(default_factory=lambda: CropRect(0, 0, 1, 1))
    padded_rect: CropRect = field(default_factory=lambda: CropRect(0, 0, 1, 1))
    methods: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    text_likelihood: float = 0.0
    foreground_area_percentage: float = 0.0
    edge_density: float = 0.0
    component_count: int = 0
    width: int = 0
    height: int = 0
    status: ProposalStatus = ProposalStatus.proposed
    assigned_asset_uuid: str | None = None
    notes: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)
    parent_uuid: str | None = None
    child_uuids: list[str] = field(default_factory=list)
    user_modified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_uuid": self.proposal_uuid,
            "source_sheet_uuid": self.source_sheet_uuid,
            "rect": self.rect.to_dict(),
            "padded_rect": self.padded_rect.to_dict(),
            "methods": list(self.methods),
            "confidence": self.confidence,
            "text_likelihood": self.text_likelihood,
            "foreground_area_percentage": self.foreground_area_percentage,
            "edge_density": self.edge_density,
            "component_count": self.component_count,
            "width": self.width,
            "height": self.height,
            "status": self.status.value,
            "assigned_asset_uuid": self.assigned_asset_uuid,
            "notes": self.notes,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "parent_uuid": self.parent_uuid,
            "child_uuids": list(self.child_uuids),
            "user_modified": self.user_modified,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CropProposal":
        rect_payload = payload.get("rect", {})
        padded_payload = payload.get("padded_rect", rect_payload)
        methods = payload.get("methods", ())
        status = payload.get("status", ProposalStatus.proposed.value)
        return cls(
            proposal_uuid=str(payload.get("proposal_uuid", str(uuid4()))),
            source_sheet_uuid=str(payload.get("source_sheet_uuid", "")),
            rect=CropRect.from_dict(rect_payload) if isinstance(rect_payload, dict) else CropRect(0, 0, 1, 1),
            padded_rect=CropRect.from_dict(padded_payload) if isinstance(padded_payload, dict) else CropRect(0, 0, 1, 1),
            methods=tuple(str(item) for item in methods) if isinstance(methods, (list, tuple)) else (),
            confidence=float(payload.get("confidence", 0.0)),
            text_likelihood=float(payload.get("text_likelihood", 0.0)),
            foreground_area_percentage=float(payload.get("foreground_area_percentage", 0.0)),
            edge_density=float(payload.get("edge_density", 0.0)),
            component_count=int(payload.get("component_count", 0)),
            width=int(payload.get("width", 0)),
            height=int(payload.get("height", 0)),
            status=ProposalStatus(str(status)),
            assigned_asset_uuid=payload.get("assigned_asset_uuid"),
            notes=str(payload.get("notes", "")),
            created_at=str(payload.get("created_at", utc_now_iso())),
            modified_at=str(payload.get("modified_at", utc_now_iso())),
            parent_uuid=payload.get("parent_uuid"),
            child_uuids=[str(item) for item in payload.get("child_uuids", []) if item],
            user_modified=bool(payload.get("user_modified", False)),
        )


@dataclass(slots=True)
class DetectionResult:
    image_size: tuple[int, int]
    proposals: list[CropProposal]
    foreground_mask: np.ndarray
    edge_mask: np.ndarray
    variance_mask: np.ndarray
    combined_mask: np.ndarray
    warnings: list[str] = field(default_factory=list)


DETECTION_PRESETS: dict[str, dict[str, Any]] = {
    "Character Frames": {
        "min_width": 12,
        "min_height": 12,
        "aspect_ratio_min": 0.25,
        "aspect_ratio_max": 4.0,
        "close_gap_radius": 2,
        "ignore_text_heavy_regions": True,
        "maximum_text_likelihood": 0.6,
    },
    "Boss Figures": {
        "min_width": 24,
        "min_height": 24,
        "max_width": 2048,
        "max_height": 2048,
        "aspect_ratio_max": 8.0,
        "close_gap_radius": 4,
    },
    "Effects and Projectiles": {
        "min_width": 4,
        "min_height": 4,
        "min_area": 8,
        "merge_distance": 8,
        "close_gap_radius": 1,
        "ignore_text_heavy_regions": False,
        "text_rejection_enabled": False,
    },
    "Small Item Icons": {
        "min_width": 4,
        "min_height": 4,
        "min_area": 8,
        "aspect_ratio_max": 3.0,
        "maximum_text_likelihood": 0.55,
    },
    "Portraits": {
        "min_width": 24,
        "min_height": 24,
        "aspect_ratio_min": 0.6,
        "aspect_ratio_max": 1.6,
        "min_area": 64,
    },
    "Broad Search": {
        "min_width": 2,
        "min_height": 2,
        "min_area": 4,
        "text_rejection_enabled": False,
        "ignore_text_heavy_regions": False,
    },
}


def default_detection_settings_for_preset(name: str) -> DetectionSettingsModel:
    settings = DetectionSettingsModel(preset_name=name)
    values = DETECTION_PRESETS.get(name, {})
    for key, value in values.items():
        setattr(settings, key, value)
    return settings


def apply_detection_preset(settings: DetectionSettingsModel, name: str) -> DetectionSettingsModel:
    updated = deepcopy(settings)
    updated.preset_name = name
    preset = DETECTION_PRESETS.get(name, {})
    for key, value in preset.items():
        setattr(updated, key, value)
    return updated


def json_dumps_stable(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def croprect_from_tuple(rect: tuple[int, int, int, int]) -> CropRect:
    return CropRect(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))


def clamp_rect(rect: CropRect, bounds: tuple[int, int], padding: int = 0) -> CropRect:
    width, height = bounds
    left = max(0, rect.x - padding)
    top = max(0, rect.y - padding)
    right = min(width, rect.x + rect.width + padding)
    bottom = min(height, rect.y + rect.height + padding)
    return CropRect(left, top, max(1, right - left), max(1, bottom - top))


def rect_iou(a: CropRect, b: CropRect) -> float:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.width, b.x + b.width)
    y2 = min(a.y + a.height, b.y + b.height)
    inter_w = max(0, x2 - x1)
    inter_h = max(0, y2 - y1)
    inter = inter_w * inter_h
    if inter == 0:
        return 0.0
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union else 0.0


def containment_ratio(inner: CropRect, outer: CropRect) -> float:
    x1 = max(inner.x, outer.x)
    y1 = max(inner.y, outer.y)
    x2 = min(inner.x + inner.width, outer.x + outer.width)
    y2 = min(inner.y + inner.height, outer.y + outer.height)
    inter_w = max(0, x2 - x1)
    inter_h = max(0, y2 - y1)
    inter = inter_w * inter_h
    return inter / (inner.width * inner.height) if inner.width and inner.height else 0.0


def center_distance(a: CropRect, b: CropRect) -> float:
    ax = a.x + a.width / 2.0
    ay = a.y + a.height / 2.0
    bx = b.x + b.width / 2.0
    by = b.y + b.height / 2.0
    return float(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5)


def union_rect(rects: Iterable[CropRect]) -> CropRect:
    rects = list(rects)
    left = min(rect.x for rect in rects)
    top = min(rect.y for rect in rects)
    right = max(rect.x + rect.width for rect in rects)
    bottom = max(rect.y + rect.height for rect in rects)
    return CropRect(left, top, right - left, bottom - top)


def expand_rect(rect: CropRect, padding: int, bounds: tuple[int, int]) -> CropRect:
    return clamp_rect(rect, bounds, padding=padding)


def _image_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGBA"), dtype=np.uint8)


def common_color_suggestions(image: Image.Image, limit: int = 5) -> list[BackgroundSample]:
    array = _image_array(image)
    flat = array.reshape((-1, 4))
    colors, counts = np.unique(flat, axis=0, return_counts=True)
    order = np.argsort(counts)[::-1]
    suggestions: list[BackgroundSample] = []
    for index in order[:limit]:
        rgba = tuple(int(v) for v in colors[index])
        suggestions.append(BackgroundSample(rgba=rgba, source="suggested", label=f"#{len(suggestions) + 1}"))
    corners = [
        tuple(int(v) for v in array[0, 0]),
        tuple(int(v) for v in array[0, -1]),
        tuple(int(v) for v in array[-1, 0]),
        tuple(int(v) for v in array[-1, -1]),
    ]
    for corner in corners:
        if all(sample.rgba != corner for sample in suggestions):
            suggestions.append(BackgroundSample(rgba=corner, source="corner", label="corner"))
        if len(suggestions) >= limit:
            break
    return suggestions[:limit]


def background_difference_mask(image: Image.Image, background_samples: Iterable[BackgroundSample], tolerance: int) -> np.ndarray:
    array = _image_array(image).astype(np.int16)
    if not background_samples:
        return np.zeros(array.shape[:2], dtype=bool)
    diffs = []
    for sample in background_samples:
        sample_array = np.array(sample.rgba, dtype=np.int16).reshape((1, 1, 4))
        diff = np.abs(array - sample_array).max(axis=2)
        diffs.append(diff > tolerance)
    return np.logical_and.reduce(diffs)


def edge_based_mask(image: Image.Image, close_gap_radius: int = 1) -> np.ndarray:
    gray = cv2.cvtColor(_image_array(image), cv2.COLOR_RGBA2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    if close_gap_radius > 0:
        kernel_size = close_gap_radius * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    return edges > 0


def connected_components_mask(image: Image.Image, background_samples: Iterable[BackgroundSample], tolerance: int, connectivity: int = 8) -> np.ndarray:
    foreground = background_difference_mask(image, background_samples, tolerance)
    if not np.any(foreground):
        alpha = _image_array(image)[:, :, 3]
        foreground = alpha > 0 if np.any(alpha < 255) else np.ones(image.size[::-1], dtype=bool)
    return _binary_close(foreground, 1, connectivity)


def color_variance_mask(image: Image.Image, window: int = 7, threshold: float = 18.0) -> np.ndarray:
    gray = cv2.cvtColor(_image_array(image), cv2.COLOR_RGBA2GRAY).astype(np.float32)
    window = max(3, int(window) | 1)
    mean = cv2.blur(gray, (window, window))
    sq_mean = cv2.blur(gray * gray, (window, window))
    variance = np.maximum(0.0, sq_mean - mean * mean)
    stddev = np.sqrt(variance)
    return stddev > threshold


def _binary_close(mask: np.ndarray, radius: int, connectivity: int = 8) -> np.ndarray:
    if radius <= 0:
        return mask.astype(bool)
    kernel_size = radius * 2 + 1
    shape = cv2.MORPH_ELLIPSE if connectivity == 8 else cv2.MORPH_RECT
    kernel = cv2.getStructuringElement(shape, (kernel_size, kernel_size))
    closed = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel)
    return closed > 0


def apply_exclusion_zones(mask: np.ndarray, zones: Iterable[ExclusionZone], bounds: tuple[int, int] | None = None) -> np.ndarray:
    filtered = mask.copy()
    for zone in zones:
        if not zone.enabled:
            continue
        rect = zone.rect
        x1 = max(0, rect.x)
        y1 = max(0, rect.y)
        x2 = min(filtered.shape[1], rect.x + rect.width)
        y2 = min(filtered.shape[0], rect.y + rect.height)
        if x2 > x1 and y2 > y1:
            filtered[y1:y2, x1:x2] = False
    return filtered


def text_likelihood_score(image: Image.Image, rect: CropRect, mask: np.ndarray | None = None) -> float:
    array = _image_array(image)
    x1 = max(0, rect.x)
    y1 = max(0, rect.y)
    x2 = min(array.shape[1], rect.x + rect.width)
    y2 = min(array.shape[0], rect.y + rect.height)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    region = array[y1:y2, x1:x2]
    gray = cv2.cvtColor(region, cv2.COLOR_RGBA2GRAY)
    w = max(1, x2 - x1)
    h = max(1, y2 - y1)
    aspect = w / h
    binary = (gray > np.median(gray)).astype(np.uint8)
    component_count = int(cv2.connectedComponents(binary, 8)[0] - 1)
    edge_density = float(np.count_nonzero(cv2.Canny(gray, 40, 120))) / max(1, w * h)
    vertical_std = float(np.std(np.mean(gray, axis=1)))
    score = 0.0
    if aspect > 3.0:
        score += 0.35
    if component_count >= 4:
        score += min(0.25, component_count * 0.04)
    if edge_density > 0.05:
        score += min(0.2, edge_density * 1.5)
    if vertical_std < 20.0:
        score += 0.15
    if h <= max(12, w // 2):
        score += 0.15
    return max(0.0, min(1.0, score))


def confidence_score(
    *,
    method_count: int,
    selected_method_count: int,
    foreground_area_percentage: float,
    edge_density: float,
    text_likelihood: float,
    aspect_ratio: float,
    border_proximity: float = 0.0,
) -> float:
    agreement = method_count / max(1, selected_method_count)
    area_score = min(1.0, foreground_area_percentage * 4.0)
    edge_score = min(1.0, edge_density * 6.0)
    aspect_score = 1.0 - min(1.0, abs(np.log(max(aspect_ratio, 0.01))) / 3.0)
    text_score = 1.0 - text_likelihood
    border_score = 1.0 - min(1.0, border_proximity)
    score = (
        0.32 * agreement
        + 0.18 * area_score
        + 0.15 * edge_score
        + 0.12 * aspect_score
        + 0.15 * text_score
        + 0.08 * border_score
    )
    return max(0.0, min(1.0, score))


def _rect_border_proximity(rect: CropRect, bounds: tuple[int, int]) -> float:
    width, height = bounds
    if width <= 0 or height <= 0:
        return 0.0
    distances = [rect.x, rect.y, width - (rect.x + rect.width), height - (rect.y + rect.height)]
    closest = max(0, min(distances))
    scale = max(1.0, min(width, height) / 4.0)
    return max(0.0, min(1.0, 1.0 - closest / scale))


def _analysis_region_rect(image: Image.Image, settings: DetectionSettingsModel) -> CropRect:
    if settings.analysis_region is not None:
        return settings.analysis_region
    return CropRect(0, 0, image.width, image.height)


def _prepare_masks(image: Image.Image, settings: DetectionSettingsModel) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    background_samples = settings.background_samples or common_color_suggestions(image, limit=3)
    background_mask = background_difference_mask(image, background_samples, settings.background_tolerance)
    edge_mask = edge_based_mask(image, settings.close_gap_radius)
    connected_mask = connected_components_mask(image, background_samples, settings.background_tolerance, settings.connectivity)
    variance_mask = color_variance_mask(image)
    return background_mask, edge_mask, connected_mask, variance_mask


def _combine_selected_masks(background_mask: np.ndarray, edge_mask: np.ndarray, connected_mask: np.ndarray, variance_mask: np.ndarray, methods: Iterable[str]) -> np.ndarray:
    mask = np.zeros_like(background_mask, dtype=bool)
    for method in methods:
        if method == "background_difference":
            mask |= background_mask
        elif method == "edge_based":
            mask |= edge_mask
        elif method == "connected_components":
            mask |= connected_mask
        elif method == "color_variance":
            mask |= variance_mask
    return mask


def _region_proposals(
    image: Image.Image,
    mask: np.ndarray,
    settings: DetectionSettingsModel,
    source_sheet_uuid: str,
    methods: tuple[str, ...],
    edge_mask: np.ndarray,
    cancel_requested: Callable[[], bool] | None = None,
) -> list[CropProposal]:
    labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=settings.connectivity)
    proposals: list[CropProposal] = []
    bounds = (image.width, image.height)
    for label in range(1, labels_count):
        if cancel_requested is not None and cancel_requested():
            break
        x, y, w, h, area = stats[label]
        if area <= 0:
            continue
        rect = CropRect(int(x), int(y), int(w), int(h))
        if rect.width <= 0 or rect.height <= 0:
            continue
        if settings.ignore_below_min_size and (rect.width < settings.min_width or rect.height < settings.min_height or rect.width * rect.height < settings.min_area):
            continue
        if settings.ignore_above_max_size and (rect.width > settings.max_width or rect.height > settings.max_height or rect.width * rect.height > settings.max_area):
            continue
        aspect = rect.width / max(1, rect.height)
        if aspect < settings.aspect_ratio_min or aspect > settings.aspect_ratio_max:
            continue
        region_mask = mask[y : y + h, x : x + w]
        edge_region = edge_mask[y : y + h, x : x + w]
        component_count = int(cv2.connectedComponents(region_mask.astype(np.uint8), connectivity=settings.connectivity)[0] - 1)
        foreground_area_percentage = float(np.count_nonzero(region_mask)) / max(1, rect.width * rect.height)
        edge_density = float(np.count_nonzero(edge_region)) / max(1, rect.width * rect.height)
        text_score = text_likelihood_score(image, rect, mask)
        if settings.ignore_text_heavy_regions and settings.text_rejection_enabled and settings.preset_name != "Broad Search":
            if text_score > settings.maximum_text_likelihood:
                continue
        if edge_density < settings.minimum_edge_density:
            continue
        if settings.ignore_near_full_width_panels and rect.width >= int(bounds[0] * 0.9) and rect.height < int(bounds[1] * 0.35):
            continue
        padded = expand_rect(rect, settings.padding, bounds)
        methods_used = tuple(sorted(methods))
        confidence = confidence_score(
            method_count=max(1, len(methods_used)),
            selected_method_count=max(1, len(settings.methods)),
            foreground_area_percentage=foreground_area_percentage,
            edge_density=edge_density,
            text_likelihood=text_score,
            aspect_ratio=aspect,
            border_proximity=_rect_border_proximity(rect, bounds),
        )
        proposals.append(
            CropProposal(
                source_sheet_uuid=source_sheet_uuid,
                rect=rect,
                padded_rect=padded,
                methods=methods_used,
                confidence=confidence,
                text_likelihood=text_score,
                foreground_area_percentage=foreground_area_percentage,
                edge_density=edge_density,
                component_count=component_count,
                width=rect.width,
                height=rect.height,
            )
        )
        if len(proposals) >= settings.proposal_limit:
            break
    return proposals


def detect_crop_proposals(
    image: Image.Image,
    source_sheet_uuid: str,
    settings: DetectionSettingsModel,
    exclusion_zones: Iterable[ExclusionZone] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> DetectionResult:
    if cancel_requested is not None and cancel_requested():
        return DetectionResult(
            image_size=image.size,
            proposals=[],
            foreground_mask=np.zeros((image.height, image.width), dtype=bool),
            edge_mask=np.zeros((image.height, image.width), dtype=bool),
            variance_mask=np.zeros((image.height, image.width), dtype=bool),
            combined_mask=np.zeros((image.height, image.width), dtype=bool),
            warnings=["Detection cancelled"],
        )
    region = _analysis_region_rect(image, settings)
    crop = image.crop((region.x, region.y, region.x + region.width, region.y + region.height))
    background_mask, edge_mask, connected_mask, variance_mask = _prepare_masks(crop, settings)
    selected_mask = _combine_selected_masks(background_mask, edge_mask, connected_mask, variance_mask, settings.methods)
    if settings.close_gap_radius > 0:
        selected_mask = _binary_close(selected_mask, settings.close_gap_radius, settings.connectivity)
    if exclusion_zones:
        offset_zones = []
        for zone in exclusion_zones:
            if zone.enabled:
                shifted = deepcopy(zone)
                shifted.rect = CropRect(
                    max(0, zone.rect.x - region.x),
                    max(0, zone.rect.y - region.y),
                    zone.rect.width,
                    zone.rect.height,
                )
                offset_zones.append(shifted)
        selected_mask = apply_exclusion_zones(selected_mask, offset_zones)
    proposals = _region_proposals(crop, selected_mask, settings, source_sheet_uuid, settings.methods, edge_mask, cancel_requested=cancel_requested)
    proposals, _groups = deduplicate_proposals(
        proposals,
        iou_threshold=settings.duplicate_iou_threshold,
        containment_threshold=settings.duplicate_containment_threshold,
        keep_all_candidates=settings.keep_all_candidates,
    )
    for proposal in proposals:
        proposal.rect = CropRect(proposal.rect.x + region.x, proposal.rect.y + region.y, proposal.rect.width, proposal.rect.height)
        proposal.padded_rect = CropRect(
            proposal.padded_rect.x + region.x,
            proposal.padded_rect.y + region.y,
            proposal.padded_rect.width,
            proposal.padded_rect.height,
        )
    warnings: list[str] = []
    if not proposals:
        warnings.append("No crop proposals detected")
    return DetectionResult(
        image_size=image.size,
        proposals=proposals,
        foreground_mask=background_mask,
        edge_mask=edge_mask,
        variance_mask=variance_mask,
        combined_mask=selected_mask,
        warnings=warnings,
    )


def deduplicate_proposals(
    proposals: Iterable[CropProposal],
    *,
    iou_threshold: float = 0.7,
    containment_threshold: float = 0.92,
    keep_all_candidates: bool = False,
) -> tuple[list[CropProposal], list[list[str]]]:
    ordered = sorted(list(proposals), key=lambda item: (not item.user_modified, -item.confidence, item.proposal_uuid))
    kept: list[CropProposal] = []
    groups: list[list[str]] = []
    for proposal in ordered:
        duplicate_index = None
        for index, existing in enumerate(kept):
            iou = rect_iou(proposal.rect, existing.rect)
            containment = max(containment_ratio(proposal.rect, existing.rect), containment_ratio(existing.rect, proposal.rect))
            close = center_distance(proposal.rect, existing.rect) <= max(proposal.rect.width, proposal.rect.height, existing.rect.width, existing.rect.height)
            if iou >= iou_threshold or containment >= containment_threshold or close and iou > 0.1:
                duplicate_index = index
                break
        if duplicate_index is None:
            kept.append(proposal)
            groups.append([proposal.proposal_uuid])
            continue
        existing = kept[duplicate_index]
        groups[duplicate_index].append(proposal.proposal_uuid)
        if proposal.user_modified and not existing.user_modified:
            kept.append(proposal)
            groups.append([proposal.proposal_uuid])
            continue
        if existing.user_modified and not proposal.user_modified:
            if keep_all_candidates or proposal.confidence >= existing.confidence:
                kept.append(proposal)
                groups.append([proposal.proposal_uuid])
            continue
        if keep_all_candidates:
            kept.append(proposal)
            groups.append([proposal.proposal_uuid])
            continue
        if proposal.confidence > existing.confidence and not existing.user_modified:
            kept[duplicate_index] = proposal
    return kept, groups


def merge_proposals(proposals: Iterable[CropProposal], source_sheet_uuid: str | None = None, padding: int = 0) -> CropProposal:
    selected = list(proposals)
    if not selected:
        raise ValueError("At least one proposal is required")
    merged_rect = union_rect(item.rect for item in selected)
    merged_padded = union_rect(item.padded_rect for item in selected)
    if padding:
        merged_padded = CropRect(
            merged_padded.x - padding,
            merged_padded.y - padding,
            merged_padded.width + padding * 2,
            merged_padded.height + padding * 2,
        )
    methods = tuple(sorted({method for proposal in selected for method in proposal.methods}))
    return CropProposal(
        source_sheet_uuid=source_sheet_uuid or selected[0].source_sheet_uuid,
        rect=merged_rect,
        padded_rect=merged_padded,
        methods=methods,
        confidence=max(item.confidence for item in selected),
        text_likelihood=max(item.text_likelihood for item in selected),
        foreground_area_percentage=max(item.foreground_area_percentage for item in selected),
        edge_density=max(item.edge_density for item in selected),
        component_count=sum(item.component_count for item in selected),
        width=merged_rect.width,
        height=merged_rect.height,
        status=ProposalStatus.modified,
        parent_uuid=None,
        child_uuids=[item.proposal_uuid for item in selected],
        user_modified=True,
    )


def split_proposal_vertical(proposal: CropProposal, split_x: int) -> tuple[CropProposal, CropProposal]:
    split_x = max(proposal.rect.x + 1, min(proposal.rect.x + proposal.rect.width - 1, split_x))
    left = CropRect(proposal.rect.x, proposal.rect.y, split_x - proposal.rect.x, proposal.rect.height)
    right = CropRect(split_x, proposal.rect.y, proposal.rect.x + proposal.rect.width - split_x, proposal.rect.height)
    return (
        CropProposal(
            source_sheet_uuid=proposal.source_sheet_uuid,
            rect=left,
            padded_rect=left,
            methods=proposal.methods,
            confidence=proposal.confidence,
            text_likelihood=proposal.text_likelihood,
            foreground_area_percentage=proposal.foreground_area_percentage / 2.0,
            edge_density=proposal.edge_density,
            component_count=max(1, proposal.component_count // 2),
            width=left.width,
            height=left.height,
            status=ProposalStatus.modified,
            parent_uuid=proposal.proposal_uuid,
            user_modified=True,
        ),
        CropProposal(
            source_sheet_uuid=proposal.source_sheet_uuid,
            rect=right,
            padded_rect=right,
            methods=proposal.methods,
            confidence=proposal.confidence,
            text_likelihood=proposal.text_likelihood,
            foreground_area_percentage=proposal.foreground_area_percentage / 2.0,
            edge_density=proposal.edge_density,
            component_count=max(1, proposal.component_count // 2),
            width=right.width,
            height=right.height,
            status=ProposalStatus.modified,
            parent_uuid=proposal.proposal_uuid,
            user_modified=True,
        ),
    )


def split_proposal_horizontal(proposal: CropProposal, split_y: int) -> tuple[CropProposal, CropProposal]:
    split_y = max(proposal.rect.y + 1, min(proposal.rect.y + proposal.rect.height - 1, split_y))
    top = CropRect(proposal.rect.x, proposal.rect.y, proposal.rect.width, split_y - proposal.rect.y)
    bottom = CropRect(proposal.rect.x, split_y, proposal.rect.width, proposal.rect.y + proposal.rect.height - split_y)
    return (
        CropProposal(source_sheet_uuid=proposal.source_sheet_uuid, rect=top, padded_rect=top, methods=proposal.methods, confidence=proposal.confidence, text_likelihood=proposal.text_likelihood, foreground_area_percentage=proposal.foreground_area_percentage / 2.0, edge_density=proposal.edge_density, component_count=max(1, proposal.component_count // 2), width=top.width, height=top.height, status=ProposalStatus.modified, parent_uuid=proposal.proposal_uuid, user_modified=True),
        CropProposal(source_sheet_uuid=proposal.source_sheet_uuid, rect=bottom, padded_rect=bottom, methods=proposal.methods, confidence=proposal.confidence, text_likelihood=proposal.text_likelihood, foreground_area_percentage=proposal.foreground_area_percentage / 2.0, edge_density=proposal.edge_density, component_count=max(1, proposal.component_count // 2), width=bottom.width, height=bottom.height, status=ProposalStatus.modified, parent_uuid=proposal.proposal_uuid, user_modified=True),
    )


def settings_checksum(settings: DetectionSettingsModel) -> str:
    return settings.checksum()


def proposal_cache_key(source_checksum: str | None, settings: DetectionSettingsModel) -> str:
    digest = sha256()
    digest.update((source_checksum or "").encode("utf-8"))
    digest.update(settings_checksum(settings).encode("utf-8"))
    return digest.hexdigest()
