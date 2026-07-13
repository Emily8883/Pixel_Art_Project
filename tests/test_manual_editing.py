from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_asset_extractor.manual_editing import ManualEditDocument, compute_settings_checksum, manual_edit_checksum
from pixel_asset_extractor.manual_storage import (
    manual_edit_sidecar_path,
    save_sidecar_png,
    validate_manual_sidecar,
)
from pixel_asset_extractor.project_manager import ProjectManager
from pixel_asset_extractor.project_model import BackgroundRemovalSettingsModel, ProjectRecord, SpriteProject
from pixel_asset_extractor.project_store import load_project, save_project


def make_document(size=(6, 6), raw_color=(0, 0, 0, 0), auto_color=(0, 0, 0, 0)) -> ManualEditDocument:
    raw = Image.new("RGBA", size, raw_color)
    auto = Image.new("RGBA", size, auto_color)
    return ManualEditDocument(raw_crop=raw, auto_clean=auto)


def as_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGBA"))


def test_raw_crop_remains_immutable():
    doc = make_document()
    before = as_array(doc.raw_image())
    doc.apply_pencil([(2, 2)], (255, 0, 0, 255))

    assert np.array_equal(before, as_array(doc.raw_image()))


def test_auto_clean_remains_reproducible():
    doc = make_document(auto_color=(10, 20, 30, 255))
    before = as_array(doc.auto_image())
    doc.apply_pencil([(2, 2)], (255, 0, 0, 255))

    assert np.array_equal(before, as_array(doc.auto_image()))


def test_final_initializes_from_auto_clean():
    doc = make_document(auto_color=(10, 20, 30, 255))

    assert np.array_equal(as_array(doc.auto_image()), as_array(doc.final_image()))


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5])
def test_pencil_brush_sizes(size):
    doc = make_document()
    doc.apply_pencil([(3, 3)], (255, 0, 0, 255), brush_size=size)
    alpha = as_array(doc.final_image())[:, :, 3]

    assert int(np.count_nonzero(alpha)) == size * size


def test_pencil_changes_exact_intended_pixels():
    doc = make_document(size=(5, 5))
    doc.apply_pencil([(0, 0), (4, 0)], (255, 0, 0, 255), brush_size=1)
    pixels = as_array(doc.final_image())

    assert list(map(tuple, pixels[0, :, :])) == [
        (255, 0, 0, 255),
        (255, 0, 0, 255),
        (255, 0, 0, 255),
        (255, 0, 0, 255),
        (255, 0, 0, 255),
    ]


def test_pencil_interpolation_creates_no_gaps():
    doc = make_document(size=(7, 1))
    doc.apply_pencil([(0, 0), (6, 0)], (255, 0, 0, 255), brush_size=1)

    assert np.count_nonzero(as_array(doc.final_image())[:, :, 3]) == 7


def test_eraser_produces_exact_rgba_zero():
    doc = make_document(auto_color=(255, 0, 0, 255))
    doc.apply_eraser([(2, 2)], brush_size=1)

    assert tuple(as_array(doc.final_image())[2, 2]) == (0, 0, 0, 0)


def test_eraser_interpolation_creates_no_gaps():
    doc = make_document(size=(7, 1), auto_color=(255, 0, 0, 255))
    doc.apply_eraser([(0, 0), (6, 0)], brush_size=1)

    assert np.count_nonzero(as_array(doc.final_image())[:, :, 3]) == 0


def test_color_picker_returns_exact_rgba():
    doc = make_document(size=(3, 3), auto_color=(10, 20, 30, 40))
    picked = doc.pick_color(1, 1, "auto")

    assert picked == (10, 20, 30, 40)


def test_exact_color_flood_fill():
    raw = Image.new("RGBA", (3, 3), (0, 255, 0, 255))
    auto = raw.copy()
    doc = ManualEditDocument(raw_crop=raw, auto_clean=auto)
    final = doc.final_image()
    final.putpixel((1, 1), (0, 0, 255, 255))
    doc.set_final_image(final)
    doc.flood_fill(0, 0, (255, 0, 0, 255), exact_color=True, connectivity=4)
    pixels = as_array(doc.final_image())

    assert tuple(pixels[0, 0]) == (255, 0, 0, 255)
    assert tuple(pixels[1, 1]) == (0, 0, 255, 255)


def test_tolerance_flood_fill():
    array = np.array(
        [
            [[10, 10, 10, 255], [12, 10, 10, 255], [200, 200, 200, 255]],
            [[10, 12, 10, 255], [11, 11, 10, 255], [200, 200, 200, 255]],
            [[200, 200, 200, 255], [200, 200, 200, 255], [200, 200, 200, 255]],
        ],
        dtype=np.uint8,
    )
    image = Image.fromarray(array, mode="RGBA")
    doc = ManualEditDocument(raw_crop=image, auto_clean=image)
    doc.flood_fill(0, 0, (255, 0, 0, 255), exact_color=False, tolerance_ui=3, connectivity=4)

    assert tuple(as_array(doc.final_image())[0, 1]) == (255, 0, 0, 255)
    assert tuple(as_array(doc.final_image())[0, 2]) == (200, 200, 200, 255)


def test_four_way_fill_behavior():
    array = np.array(
        [
            [[1, 1, 1, 255], [9, 9, 9, 255]],
            [[9, 9, 9, 255], [1, 1, 1, 255]],
        ],
        dtype=np.uint8,
    )
    image = Image.fromarray(array, mode="RGBA")
    doc = ManualEditDocument(raw_crop=image, auto_clean=image)
    doc.flood_fill(0, 0, (255, 0, 0, 255), exact_color=True, connectivity=4)

    assert tuple(as_array(doc.final_image())[1, 1]) == (1, 1, 1, 255)


def test_eight_way_fill_behavior():
    array = np.array(
        [
            [[1, 1, 1, 255], [9, 9, 9, 255]],
            [[9, 9, 9, 255], [1, 1, 1, 255]],
        ],
        dtype=np.uint8,
    )
    image = Image.fromarray(array, mode="RGBA")
    doc = ManualEditDocument(raw_crop=image, auto_clean=image)
    doc.flood_fill(0, 0, (255, 0, 0, 255), exact_color=True, connectivity=8)

    assert tuple(as_array(doc.final_image())[1, 1]) == (255, 0, 0, 255)


def test_noop_fill_does_not_add_history():
    doc = make_document(auto_color=(5, 5, 5, 255))
    count = doc.flood_fill(0, 0, (5, 5, 5, 255), exact_color=True)

    assert count == 0
    assert doc.undo_count == 0


def test_rectangular_selection_bounds():
    doc = make_document(size=(4, 4))
    rect = doc.select_rect(-2, -1, 5, 5)

    assert rect == (0, 0, 3, 4)


def test_selection_delete():
    doc = make_document(auto_color=(0, 0, 0, 0))
    doc.apply_pencil([(1, 1)], (255, 0, 0, 255))
    doc.select_rect(1, 1, 1, 1)
    doc.delete_selection()

    assert tuple(as_array(doc.final_image())[1, 1]) == (0, 0, 0, 0)


def test_cut_and_paste_pixel_identity():
    doc = make_document()
    doc.apply_pencil([(1, 1), (2, 1)], (255, 0, 0, 255))
    doc.select_rect(1, 1, 2, 1)
    doc.cut_selection()
    doc.paste_clipboard(2, 2)
    doc.commit_floating_selection()
    pixels = as_array(doc.final_image())

    assert tuple(pixels[2, 2]) == (255, 0, 0, 255)
    assert tuple(pixels[2, 3]) == (255, 0, 0, 255)
    assert tuple(pixels[1, 1]) == (0, 0, 0, 0)


def test_selection_movement():
    doc = make_document()
    doc.apply_pencil([(1, 1)], (255, 0, 0, 255))
    doc.select_rect(1, 1, 1, 1)
    doc.move_selection(1, 1)

    assert tuple(as_array(doc.final_image())[2, 2]) == (255, 0, 0, 255)


def test_undo_and_redo_pencil_stroke():
    doc = make_document()
    doc.apply_pencil([(1, 1)], (255, 0, 0, 255))
    doc.undo()
    assert tuple(as_array(doc.final_image())[1, 1]) == (0, 0, 0, 0)
    doc.redo()
    assert tuple(as_array(doc.final_image())[1, 1]) == (255, 0, 0, 255)


def test_undo_and_redo_eraser_stroke():
    doc = make_document(auto_color=(255, 0, 0, 255))
    doc.apply_eraser([(1, 1)], 1)
    doc.undo()
    assert tuple(as_array(doc.final_image())[1, 1]) == (255, 0, 0, 255)
    doc.redo()
    assert tuple(as_array(doc.final_image())[1, 1]) == (0, 0, 0, 0)


def test_undo_and_redo_flood_fill():
    doc = make_document(auto_color=(10, 10, 10, 255))
    doc.flood_fill(0, 0, (255, 0, 0, 255), exact_color=True)
    doc.undo()
    assert tuple(as_array(doc.final_image())[0, 0]) == (10, 10, 10, 255)
    doc.redo()
    assert tuple(as_array(doc.final_image())[0, 0]) == (255, 0, 0, 255)


def test_undo_and_redo_selection_move():
    doc = make_document()
    doc.apply_pencil([(1, 1)], (255, 0, 0, 255))
    doc.select_rect(1, 1, 1, 1)
    doc.move_selection(1, 0)
    doc.undo()
    assert tuple(as_array(doc.final_image())[1, 1]) == (255, 0, 0, 255)
    doc.redo()
    assert tuple(as_array(doc.final_image())[1, 2]) == (255, 0, 0, 255)


def test_redo_clearing_after_new_edit():
    doc = make_document()
    doc.apply_pencil([(1, 1)], (255, 0, 0, 255))
    doc.undo()
    doc.apply_pencil([(2, 2)], (0, 255, 0, 255))

    assert doc.redo_count == 0


def test_per_asset_history_isolation():
    first = make_document()
    second = make_document()
    first.apply_pencil([(1, 1)], (255, 0, 0, 255))

    assert tuple(as_array(first.final_image())[1, 1]) == (255, 0, 0, 255)
    assert tuple(as_array(second.final_image())[1, 1]) == (0, 0, 0, 0)


def test_reset_manual_edits():
    doc = make_document(auto_color=(10, 20, 30, 255))
    doc.apply_pencil([(1, 1)], (255, 0, 0, 255))
    doc.reset_manual_edits()

    assert np.array_equal(as_array(doc.auto_image()), as_array(doc.final_image()))


def test_alpha_preview_output():
    array = np.zeros((2, 2, 4), dtype=np.uint8)
    array[0, 0, 3] = 0
    array[0, 1, 3] = 128
    array[1, 0, 3] = 255
    image = Image.fromarray(array, mode="RGBA")
    doc = ManualEditDocument(raw_crop=image, auto_clean=image)
    preview = as_array(doc.alpha_preview())

    assert tuple(preview[0, 0])[:3] == (0, 0, 0)
    assert preview[0, 1, 0] == preview[0, 1, 1] == preview[0, 1, 2]
    assert preview[1, 0, 0] == 255


def test_edge_detection_with_4_neighbors():
    array = np.zeros((3, 3, 4), dtype=np.uint8)
    array[1, 1] = (255, 255, 255, 255)
    doc = ManualEditDocument(raw_crop=Image.fromarray(array), auto_clean=Image.fromarray(array))
    mask = np.asarray(doc.edge_highlight(4))[:, :, 3] > 0

    assert mask[1, 1]


def test_edge_detection_with_8_neighbors():
    array = np.zeros((3, 3, 4), dtype=np.uint8)
    array[1, 1] = (255, 255, 255, 255)
    doc = ManualEditDocument(raw_crop=Image.fromarray(array), auto_clean=Image.fromarray(array))
    mask = np.asarray(doc.edge_highlight(8))[:, :, 3] > 0

    assert mask[1, 1]


def test_suspected_halo_detection():
    array = np.full((2, 2, 4), (10, 10, 10, 255), dtype=np.uint8)
    doc = ManualEditDocument(raw_crop=Image.fromarray(array), auto_clean=Image.fromarray(array), background_rgba=(12, 12, 12, 255))
    mask = doc.suspected_halo_mask((12, 12, 12, 255), tolerance_ui=20)

    assert mask[0, 0]


def test_isolated_pixel_detection():
    array = np.zeros((3, 3, 4), dtype=np.uint8)
    array[1, 1] = (255, 0, 0, 255)
    doc = ManualEditDocument(raw_crop=Image.fromarray(array), auto_clean=Image.fromarray(array))

    assert doc.isolated_pixel_mask()[1, 1]


def test_semi_transparent_pixel_detection():
    array = np.zeros((2, 2, 4), dtype=np.uint8)
    array[0, 0] = (255, 0, 0, 1)
    doc = ManualEditDocument(raw_crop=Image.fromarray(array), auto_clean=Image.fromarray(array))

    assert doc.semi_transparent_mask()[0, 0]


def test_inspection_overlays_do_not_modify_pixels():
    doc = make_document(auto_color=(10, 20, 30, 255))
    before = as_array(doc.final_image())
    _ = doc.alpha_preview()
    _ = doc.edge_highlight()
    _ = doc.suspected_halo_highlight((10, 20, 30, 255))

    assert np.array_equal(before, as_array(doc.final_image()))


def test_inspection_overlays_are_excluded_from_exports(tmp_path):
    doc = make_document(auto_color=(10, 20, 30, 255))
    _ = doc.alpha_preview()
    destination = tmp_path / "final.png"
    doc.export_final(destination)

    with Image.open(destination) as saved:
        assert np.array_equal(as_array(saved), as_array(doc.final_image()))


def test_final_edited_export_is_rgba(tmp_path):
    doc = make_document(auto_color=(10, 20, 30, 255))
    destination = tmp_path / "final.png"
    doc.export_final(destination)

    with Image.open(destination) as saved:
        assert saved.mode == "RGBA"


def test_final_edited_export_is_pixel_identical_to_editor_state(tmp_path):
    doc = make_document(auto_color=(10, 20, 30, 255))
    doc.apply_pencil([(1, 1)], (255, 0, 0, 255))
    destination = tmp_path / "final.png"
    doc.export_final(destination)

    with Image.open(destination) as saved:
        assert np.array_equal(as_array(saved), as_array(doc.final_image()))


def test_v3_migration_to_v4(tmp_path):
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (2, 2), (0, 0, 0, 255)).save(source)
    payload = {
        "config_version": 3,
        "project": {
            "project_name": "Legacy",
            "project_root_directory": str(tmp_path),
            "project_version": 3,
        },
        "source_sheets": [],
        "assets": [],
        "activity_log": [],
    }
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    project = load_project(path)
    assert project.project.project_version == 6


def test_v4_project_round_trip(tmp_path):
    project = SpriteProject(
        project=ProjectRecord(project_name="Round Trip", project_root_directory=str(tmp_path), project_version=4),
    )
    path = tmp_path / "project.json"
    save_project(project, path)
    loaded = load_project(path)

    assert loaded.project.project_version == 6


def test_sidecar_png_atomic_save(tmp_path):
    doc = make_document(auto_color=(1, 2, 3, 255))
    destination = tmp_path / "edit.png"
    save_sidecar_png(doc, destination)

    assert destination.exists()


def test_sidecar_checksum_validation(tmp_path):
    doc = make_document(auto_color=(1, 2, 3, 255))
    destination = tmp_path / "edit.png"
    save_sidecar_png(doc, destination)
    validation = validate_manual_sidecar(destination, expected_width=6, expected_height=6, expected_checksum="0" * 64)

    assert not validation.valid
    assert validation.sidecar_path == destination


def test_sidecar_dimension_validation(tmp_path):
    doc = make_document(size=(4, 4))
    destination = tmp_path / "edit.png"
    save_sidecar_png(doc, destination)
    validation = validate_manual_sidecar(destination, expected_width=5, expected_height=4)

    assert not validation.valid
    assert not validation.dimensions_match


def test_cleanup_settings_checksum_validation(tmp_path):
    doc = make_document()
    destination = tmp_path / "edit.png"
    save_sidecar_png(doc, destination)
    validation = validate_manual_sidecar(
        destination,
        expected_width=6,
        expected_height=6,
        expected_settings_checksum="bad",
        actual_settings_checksum=compute_settings_checksum({"value": 1}),
    )

    assert not validation.valid


def test_missing_sidecar_warning_state(tmp_path):
    missing = tmp_path / "missing.png"
    validation = validate_manual_sidecar(missing, expected_width=1, expected_height=1)

    assert not validation.valid
    assert "missing" in validation.messages[0].lower()


def test_thumbnail_generation_uses_nearest_neighbor():
    array = np.array(
        [
            [[255, 0, 0, 255], [0, 0, 255, 255]],
        ],
        dtype=np.uint8,
    )
    image = Image.fromarray(array, mode="RGBA")
    doc = ManualEditDocument(raw_crop=image, auto_clean=image)
    thumb = as_array(doc.thumbnail(64))

    assert tuple(thumb[16, 16])[:3] in {(255, 0, 0), (0, 0, 255)}


def test_thumbnail_generation_preserves_aspect_ratio():
    array = np.array(
        [
            [[255, 0, 0, 255], [0, 0, 255, 255]],
        ],
        dtype=np.uint8,
    )
    image = Image.fromarray(array, mode="RGBA")
    doc = ManualEditDocument(raw_crop=image, auto_clean=image)
    thumb = as_array(doc.thumbnail(64))
    color_mask = np.logical_or(
        np.all(thumb[:, :, :3] == (255, 0, 0), axis=2),
        np.all(thumb[:, :, :3] == (0, 0, 255), axis=2),
    )
    ys, xs = np.where(color_mask)

    assert (xs.max() - xs.min() + 1) == 64
    assert (ys.max() - ys.min() + 1) < 64


def test_dirty_state_transitions():
    doc = make_document()
    assert not doc.dirty
    doc.apply_pencil([(1, 1)], (255, 0, 0, 255))
    assert doc.dirty
    doc.mark_clean()
    assert not doc.dirty


def test_manual_edit_activity_log_entry(tmp_path):
    manager = ProjectManager()
    asset = manager.add_asset("asset")
    doc = make_document()
    manager.project.path = tmp_path / "project.json"
    saved = manager.save_manual_edit_document(asset.asset_uuid, doc, manager.project.path)

    assert saved.exists()
    assert any(entry.event_type == "manual_edit_saved" for entry in manager.project.activity_log)


def test_asset_switching_preserves_edits():
    first = make_document()
    second = make_document()
    first.apply_pencil([(1, 1)], (255, 0, 0, 255))
    second.apply_pencil([(2, 2)], (0, 255, 0, 255))

    assert tuple(as_array(first.final_image())[1, 1]) == (255, 0, 0, 255)
    assert tuple(as_array(second.final_image())[2, 2]) == (0, 255, 0, 255)


def test_invalidated_edits_warning_condition(tmp_path):
    manager = ProjectManager()
    asset = manager.add_asset("asset")
    asset.manual_edit_sidecar = "manual.png"
    asset.manual_edit_checksum = "0" * 64
    from pixel_asset_extractor.models import CropRect

    manager.edit_asset(asset.asset_uuid, crop_rect=CropRect(0, 0, 1, 1))

    assert asset.manual_edit_sidecar == ""
    assert asset.manual_edit_checksum is None
