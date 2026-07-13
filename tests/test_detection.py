from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from pixel_asset_extractor.detection import (
    BackgroundSample,
    CropProposal,
    DetectionSettingsModel,
    ExclusionZone,
    ProposalStatus,
    background_difference_mask,
    common_color_suggestions,
    confidence_score,
    connected_components_mask,
    containment_ratio,
    color_variance_mask,
    default_detection_settings_for_preset,
    deduplicate_proposals,
    detect_crop_proposals,
    edge_based_mask,
    merge_proposals,
    proposal_cache_key,
    rect_iou,
    settings_checksum,
    split_proposal_horizontal,
    split_proposal_vertical,
    text_likelihood_score,
)
from pixel_asset_extractor.models import CropRect
from pixel_asset_extractor.project_manager import ProjectManager
from pixel_asset_extractor.project_store import load_project, save_project
from pixel_asset_extractor.ui.detection_panel import DetectionPanelWidget


def make_sheet(path: Path) -> Path:
    image = Image.new("RGBA", (96, 64), (128, 128, 128, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((6, 8, 18, 20), fill=(220, 40, 40, 255))
    draw.rectangle((24, 8, 38, 22), fill=(40, 220, 40, 255))
    draw.rectangle((42, 8, 58, 24), fill=(40, 40, 220, 255))
    draw.rectangle((68, 8, 88, 24), fill=(220, 220, 40, 255))
    draw.rectangle((0, 0, 95, 3), fill=(90, 90, 90, 255))
    for x in range(6, 90, 8):
        draw.rectangle((x, 48, x + 2, 53), fill=(20, 20, 20, 255))
    for offset in range(4):
        draw.point((10 + offset, 34 + offset), fill=(240, 240, 240, 255))
    image.save(path)
    return path


def make_manager(tmp_path: Path) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager()
    sheet_path = make_sheet(tmp_path / "sheet.png")
    sheet = manager.add_source_sheet(sheet_path)
    asset = manager.add_asset(
        "detected_asset",
        source_sheet_id=sheet.source_sheet_id,
        source_sheet_path=str(sheet_path),
        character_group="freya",
        category="idle",
        action="idle",
        direction="front",
    )
    asset.crop_rect = CropRect(6, 8, 12, 12)
    return manager, sheet.source_sheet_id, str(sheet_path)


def test_background_difference_mask(tmp_path):
    sheet = Image.new("RGBA", (8, 8), (128, 128, 128, 255))
    draw = ImageDraw.Draw(sheet)
    draw.rectangle((2, 2, 4, 4), fill=(255, 0, 0, 255))
    mask = background_difference_mask(sheet, [BackgroundSample(rgba=(128, 128, 128, 255))], tolerance=8)

    assert mask[3, 3]
    assert not mask[0, 0]


def test_edge_based_mask(tmp_path):
    sheet = Image.new("RGBA", (16, 16), (128, 128, 128, 255))
    draw = ImageDraw.Draw(sheet)
    draw.rectangle((4, 4, 11, 11), outline=(255, 255, 255, 255), fill=(255, 255, 255, 255))
    mask = edge_based_mask(sheet, close_gap_radius=1)

    assert mask.any()


def test_connected_components_and_connectivity(tmp_path):
    image = Image.new("RGBA", (8, 8), (128, 128, 128, 255))
    draw = ImageDraw.Draw(image)
    draw.point((2, 2), fill=(255, 0, 0, 255))
    draw.point((3, 3), fill=(255, 0, 0, 255))
    background = [BackgroundSample(rgba=(128, 128, 128, 255))]
    mask = connected_components_mask(image, background, tolerance=4, connectivity=4)
    proposals_4 = detect_crop_proposals(image, "sheet", DetectionSettingsModel(methods=("connected_components",), background_samples=background, connectivity=4, min_width=1, min_height=1, min_area=1))
    proposals_8 = detect_crop_proposals(image, "sheet", DetectionSettingsModel(methods=("connected_components",), background_samples=background, connectivity=8, min_width=1, min_height=1, min_area=1))

    assert mask.any()
    assert len(proposals_8.proposals) <= len(proposals_4.proposals)


def test_color_variance_detection(tmp_path):
    sheet = Image.new("RGBA", (16, 16), (128, 128, 128, 255))
    draw = ImageDraw.Draw(sheet)
    for x in range(4, 12):
        for y in range(4, 12):
            draw.point((x, y), fill=(40 + (x * y) % 200, 40, 40, 255))
    mask = color_variance_mask(sheet, window=5, threshold=8.0)

    assert mask.any()


def test_detection_combined_and_filters(tmp_path):
    sheet_path = make_sheet(tmp_path / "sheet.png")
    image = Image.open(sheet_path)
    settings = DetectionSettingsModel(
        methods=("background_difference", "edge_based", "connected_components", "color_variance"),
        background_samples=[BackgroundSample(rgba=(128, 128, 128, 255))],
        min_width=4,
        min_height=4,
        min_area=8,
        padding=2,
    )
    result = detect_crop_proposals(image, "sheet", settings)

    assert result.proposals
    assert all(proposal.padded_rect.width >= proposal.rect.width for proposal in result.proposals)


def test_text_likelihood_scoring(tmp_path):
    sheet = Image.new("RGBA", (64, 32), (128, 128, 128, 255))
    draw = ImageDraw.Draw(sheet)
    for x in range(2, 60, 5):
        draw.rectangle((x, 6, x + 1, 10), fill=(20, 20, 20, 255))
    strip_score = text_likelihood_score(sheet, CropRect(0, 0, 64, 16))
    block_score = text_likelihood_score(sheet, CropRect(8, 20, 12, 8))

    assert 0 <= strip_score <= 1
    assert strip_score >= block_score


def test_confidence_and_overlap_helpers():
    proposal_a = CropProposal(rect=CropRect(0, 0, 10, 10), padded_rect=CropRect(0, 0, 10, 10), width=10, height=10)
    proposal_b = CropProposal(rect=CropRect(5, 5, 10, 10), padded_rect=CropRect(5, 5, 10, 10), width=10, height=10)

    assert 0 < rect_iou(proposal_a.rect, proposal_b.rect) < 1
    assert 0 < containment_ratio(proposal_a.rect, proposal_b.rect) < 1
    assert 0 <= confidence_score(method_count=2, selected_method_count=4, foreground_area_percentage=0.2, edge_density=0.1, text_likelihood=0.2, aspect_ratio=1.0) <= 1


def test_duplicate_detection_and_manual_proposals():
    proposal_a = CropProposal(rect=CropRect(0, 0, 10, 10), padded_rect=CropRect(0, 0, 10, 10), width=10, height=10, confidence=0.2)
    proposal_b = CropProposal(rect=CropRect(1, 1, 10, 10), padded_rect=CropRect(1, 1, 10, 10), width=10, height=10, confidence=0.9)
    manual = CropProposal(rect=CropRect(1, 1, 10, 10), padded_rect=CropRect(1, 1, 10, 10), width=10, height=10, confidence=0.1, user_modified=True)

    deduped, groups = deduplicate_proposals([proposal_a, proposal_b, manual], iou_threshold=0.3, containment_threshold=0.7)

    assert any(group for group in groups)
    assert any(item.user_modified for item in deduped)
    assert any(item.confidence == 0.9 for item in deduped)


def test_merge_and_split_helpers():
    a = CropProposal(proposal_uuid="a", source_sheet_uuid="sheet", rect=CropRect(0, 0, 10, 10), padded_rect=CropRect(0, 0, 10, 10), methods=("edge_based",), confidence=0.2, width=10, height=10)
    b = CropProposal(proposal_uuid="b", source_sheet_uuid="sheet", rect=CropRect(12, 0, 8, 10), padded_rect=CropRect(12, 0, 8, 10), methods=("connected_components",), confidence=0.8, width=8, height=10)
    merged = merge_proposals([a, b], source_sheet_uuid="sheet", padding=2)
    left, right = split_proposal_vertical(merged, 8)
    top, bottom = split_proposal_horizontal(merged, 5)

    assert merged.status == ProposalStatus.modified
    assert left.rect.width > 0 and right.rect.width > 0
    assert top.rect.height > 0 and bottom.rect.height > 0


def test_manager_detection_round_trip_and_uuid_stability(tmp_path):
    manager, sheet_id, _ = make_manager(tmp_path)
    result = manager.detect_sprite_regions(sheet_id)
    project_path = tmp_path / "project.json"
    save_project(manager.project, project_path)
    loaded = load_project(project_path)

    assert result.proposals
    assert loaded.project.project_version == 6
    assert [proposal.proposal_uuid for proposal in loaded.source_sheets[0].crop_proposals] == [proposal.proposal_uuid for proposal in manager.source_sheet(sheet_id).crop_proposals]


def test_manager_move_resize_merge_split_undo_redo(tmp_path):
    manager, sheet_id, _ = make_manager(tmp_path)
    manager.detect_sprite_regions(sheet_id)
    proposals = manager.source_sheet(sheet_id).crop_proposals
    proposal = proposals[0]
    other = proposals[1] if len(proposals) > 1 else proposal
    original_rect = proposal.rect
    moved = manager.move_proposal(sheet_id, proposal.proposal_uuid, 2, 3)
    resized = manager.resize_proposal(sheet_id, proposal.proposal_uuid, 20, 22)
    merged = manager.merge_proposals(sheet_id, [proposal.proposal_uuid, other.proposal_uuid], padding=1)
    assert merged.width >= original_rect.width
    assert manager.undo_proposal_edit(sheet_id) is True
    assert manager.redo_proposal_edit(sheet_id) is True
    assert resized.width == 20


def test_assignment_and_asset_creation_from_proposal(tmp_path):
    manager, sheet_id, _ = make_manager(tmp_path)
    manager.detect_sprite_regions(sheet_id)
    proposal = manager.source_sheet(sheet_id).crop_proposals[0]
    asset = manager.add_asset("target", source_sheet_id=sheet_id, source_sheet_path=manager.source_sheet(sheet_id).path, character_group="freya", category="idle", action="idle", direction="front")
    asset.manual_edit_sidecar = "temp.png"
    assigned = manager.assign_proposal_to_asset(sheet_id, proposal.proposal_uuid, asset.asset_uuid)
    created = manager.create_asset_from_proposal(sheet_id, proposal.proposal_uuid, display_name="detected_region_01", category="other")

    assert assigned.crop_rect == proposal.rect
    assert created.crop_rect == proposal.rect
    assert proposal.status == ProposalStatus.assigned
    assert assigned.manual_edit_sidecar == ""


def test_background_samples_and_preset_round_trip(tmp_path):
    manager, sheet_id, _ = make_manager(tmp_path)
    sample = manager.add_background_sample(sheet_id, (128, 128, 128, 255), label="bg")
    settings = default_detection_settings_for_preset("Character Frames")
    key_before = proposal_cache_key("abc", settings)
    assert sample.rgba == (128, 128, 128, 255)
    assert settings_checksum(settings)
    manager.save_detection_preset("custom", settings)
    loaded = manager.load_detection_preset("custom")
    key_after = proposal_cache_key("abc", loaded)

    assert key_before == key_after
    assert loaded.min_width == settings.min_width


def test_common_color_suggestions_and_exclusion_filtering(tmp_path):
    manager, sheet_id, sheet_path = make_manager(tmp_path)
    sheet = Image.open(sheet_path)
    suggestions = common_color_suggestions(sheet, limit=3)
    manager.add_exclusion_zone(sheet_id, CropRect(6, 8, 20, 20))
    result = manager.detect_sprite_regions(sheet_id, DetectionSettingsModel(background_samples=suggestions, methods=("background_difference", "edge_based", "connected_components", "color_variance")))

    assert suggestions
    assert result.proposals


def test_failed_detection_leaves_project_unchanged(tmp_path):
    manager = ProjectManager()
    sheet = manager.add_source_sheet(tmp_path / "missing.png")
    before = json.dumps(manager.project.to_dict(), sort_keys=True)
    try:
        manager.detect_sprite_regions(sheet.source_sheet_id)
    except Exception:
        pass
    after = json.dumps(manager.project.to_dict(), sort_keys=True)

    assert before == after


def test_detection_panel_gui_smoke(qapp, tmp_path):
    manager, sheet_id, _ = make_manager(tmp_path)
    widget = DetectionPanelWidget()
    widget.set_context(manager, sheet_id, tmp_path / "project.json")

    assert widget.analyze_button.text() == "Analyze"
    assert widget.generate_button.text() == "Generate Proposals"
