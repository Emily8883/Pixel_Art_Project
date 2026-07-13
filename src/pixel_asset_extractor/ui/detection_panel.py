from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..detection import utc_now_iso
from ..detection import DETECTION_PRESETS, CropProposal, DetectionSettingsModel, ProposalStatus, apply_detection_preset


class DetectionWorker(QObject):
    finished = Signal(object, object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, manager, source_sheet_id: str, settings: DetectionSettingsModel, commit: bool = False) -> None:
        super().__init__()
        self._manager = manager
        self._source_sheet_id = source_sheet_id
        self._settings = settings
        self._commit = commit
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        return self._cancelled

    @Slot()
    def run(self) -> None:
        try:
            result, settings = self._manager.preview_sprite_regions(
                self._source_sheet_id,
                self._settings,
                cancel_requested=self._is_cancelled,
            )
            if self._cancelled:
                self.cancelled.emit()
                return
            self.finished.emit(result, settings)
        except Exception as exc:  # pragma: no cover - UI error path
            self.failed.emit(str(exc))


class DetectionPanelWidget(QWidget):
    changed = Signal()
    analyzeRequested = Signal()
    generateRequested = Signal()
    createAssetRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._manager = None
        self._source_sheet_id: str | None = None
        self._project_path = None
        self._canvas = None
        self._last_result = None
        self._thread: QThread | None = None
        self._worker: DetectionWorker | None = None
        self._build_ui()

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _double_spin(self, minimum: float, maximum: float, value: float, step: float = 0.05) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(2)
        spin.setValue(value)
        return spin

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(DETECTION_PRESETS.keys()) + ["Custom"])
        self.methods_background = QCheckBox("Background Difference")
        self.methods_edge = QCheckBox("Edge-Based")
        self.methods_components = QCheckBox("Connected Components")
        self.methods_variance = QCheckBox("Color Variance")
        self.methods_background.setChecked(True)
        self.methods_edge.setChecked(True)
        self.methods_components.setChecked(True)
        self.methods_variance.setChecked(True)
        self.background_tolerance_spin = self._spin(0, 255, 24)
        self.min_width_spin = self._spin(1, 4096, 8)
        self.min_height_spin = self._spin(1, 4096, 8)
        self.max_width_spin = self._spin(1, 8192, 4096)
        self.max_height_spin = self._spin(1, 8192, 4096)
        self.min_area_spin = self._spin(1, 10_000_000, 32)
        self.max_area_spin = self._spin(1, 100_000_000, 1_000_000)
        self.merge_distance_spin = self._spin(0, 128, 4)
        self.close_gap_spin = self._spin(0, 32, 1)
        self.overlap_threshold_spin = self._double_spin(0.0, 1.0, 0.3)
        self.aspect_min_spin = self._double_spin(0.01, 10.0, 0.15)
        self.aspect_max_spin = self._double_spin(0.01, 20.0, 8.0)
        self.min_edge_density_spin = self._double_spin(0.0, 1.0, 0.0)
        self.max_text_likelihood_spin = self._double_spin(0.0, 1.0, 0.75)
        self.connectivity_combo = QComboBox()
        self.connectivity_combo.addItems(["4", "8"])
        self.padding_spin = self._spin(0, 128, 2)
        self.proposal_limit_spin = self._spin(1, 5000, 200)
        self.text_rejection_checkbox = QCheckBox("Enable text rejection")
        self.text_rejection_checkbox.setChecked(True)
        self.ignore_text_checkbox = QCheckBox("Ignore text-heavy regions")
        self.ignore_text_checkbox.setChecked(True)
        self.show_assigned_checkbox = QCheckBox("Show assigned")
        self.show_assigned_checkbox.setChecked(True)
        self.hide_rejected_checkbox = QCheckBox("Hide rejected")
        self.show_numbers_checkbox = QCheckBox("Show numbers")
        self.show_numbers_checkbox.setChecked(True)
        self.show_confidence_checkbox = QCheckBox("Show confidence")
        self.show_confidence_checkbox.setChecked(True)
        self.show_exclusions_checkbox = QCheckBox("Show exclusion zones")
        self.show_exclusions_checkbox.setChecked(True)
        self.analysis_summary = QLabel("No analysis yet")
        self.analysis_summary.setWordWrap(True)
        form.addRow("Preset", self.preset_combo)
        form.addRow("Background Tolerance", self.background_tolerance_spin)
        form.addRow("Minimum Width", self.min_width_spin)
        form.addRow("Minimum Height", self.min_height_spin)
        form.addRow("Maximum Width", self.max_width_spin)
        form.addRow("Maximum Height", self.max_height_spin)
        form.addRow("Minimum Area", self.min_area_spin)
        form.addRow("Maximum Area", self.max_area_spin)
        form.addRow("Merge Distance", self.merge_distance_spin)
        form.addRow("Close Gap Radius", self.close_gap_spin)
        form.addRow("Overlap Threshold", self.overlap_threshold_spin)
        form.addRow("Aspect Ratio Min", self.aspect_min_spin)
        form.addRow("Aspect Ratio Max", self.aspect_max_spin)
        form.addRow("Minimum Edge Density", self.min_edge_density_spin)
        form.addRow("Maximum Text Likelihood", self.max_text_likelihood_spin)
        form.addRow("Connectivity", self.connectivity_combo)
        form.addRow("Padding", self.padding_spin)
        form.addRow("Proposal Limit", self.proposal_limit_spin)
        form.addRow("Text Rejection", self.text_rejection_checkbox)
        form.addRow("Ignore Text Regions", self.ignore_text_checkbox)
        form.addRow("Show Assigned", self.show_assigned_checkbox)
        form.addRow("Hide Rejected", self.hide_rejected_checkbox)
        form.addRow("Show Numbers", self.show_numbers_checkbox)
        form.addRow("Show Confidence", self.show_confidence_checkbox)
        form.addRow("Show Exclusions", self.show_exclusions_checkbox)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        self.analyze_button = QPushButton("Analyze")
        self.preview_mask_button = QPushButton("Preview Mask")
        self.generate_button = QPushButton("Generate Proposals")
        self.reset_button = QPushButton("Reset Settings")
        self.save_preset_button = QPushButton("Save Preset")
        self.load_preset_button = QPushButton("Load Preset")
        button_row.addWidget(self.analyze_button)
        button_row.addWidget(self.preview_mask_button)
        button_row.addWidget(self.generate_button)
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.save_preset_button)
        button_row.addWidget(self.load_preset_button)
        layout.addLayout(button_row)

        self.proposal_list = QListWidget()
        self.proposal_list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.proposal_list, 1)
        layout.addWidget(self.analysis_summary)

        details_form = QFormLayout()
        self.detail_preview = QLabel("No proposal selected")
        self.detail_location = QLabel("-")
        self.detail_size = QLabel("-")
        self.detail_confidence = QLabel("-")
        self.detail_text_likelihood = QLabel("-")
        self.detail_methods = QLabel("-")
        self.detail_components = QLabel("-")
        self.detail_foreground = QLabel("-")
        self.detail_assigned = QLabel("-")
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Proposal notes")
        self.notes_edit.textChanged.connect(self._save_notes)
        details_form.addRow("Preview", self.detail_preview)
        details_form.addRow("Location", self.detail_location)
        details_form.addRow("Size", self.detail_size)
        details_form.addRow("Confidence", self.detail_confidence)
        details_form.addRow("Text Likelihood", self.detail_text_likelihood)
        details_form.addRow("Methods", self.detail_methods)
        details_form.addRow("Components", self.detail_components)
        details_form.addRow("Foreground %", self.detail_foreground)
        details_form.addRow("Assigned Asset", self.detail_assigned)
        details_form.addRow("Notes", self.notes_edit)
        layout.addLayout(details_form)

        action_row = QGridLayout()
        self.accept_button = QPushButton("Accept")
        self.reject_button = QPushButton("Reject")
        self.ignore_button = QPushButton("Ignore")
        self.assign_active_button = QPushButton("Assign to Active Asset")
        self.create_asset_button = QPushButton("Create New Asset")
        self.merge_button = QPushButton("Merge Selected")
        self.split_button = QPushButton("Split Proposal")
        self.undo_button = QPushButton("Undo")
        self.redo_button = QPushButton("Redo")
        for index, button in enumerate(
            [
                self.accept_button,
                self.reject_button,
                self.ignore_button,
                self.assign_active_button,
                self.create_asset_button,
                self.merge_button,
                self.split_button,
                self.undo_button,
                self.redo_button,
            ]
        ):
            action_row.addWidget(button, index // 2, index % 2)
        layout.addLayout(action_row)

        self.analyze_button.clicked.connect(self.analyze)
        self.generate_button.clicked.connect(self.generate_proposals)
        self.preview_mask_button.clicked.connect(self.preview_mask)
        self.reset_button.clicked.connect(self.reset_settings)
        self.save_preset_button.clicked.connect(self.save_preset)
        self.load_preset_button.clicked.connect(self.load_preset)
        self.accept_button.clicked.connect(lambda: self._set_status(ProposalStatus.accepted))
        self.reject_button.clicked.connect(lambda: self._set_status(ProposalStatus.rejected))
        self.ignore_button.clicked.connect(lambda: self._set_status(ProposalStatus.ignored))
        self.assign_active_button.clicked.connect(self.assign_to_active_asset)
        self.create_asset_button.clicked.connect(self.create_new_asset_from_proposal)
        self.merge_button.clicked.connect(self.merge_selected)
        self.split_button.clicked.connect(self.split_selected)
        self.undo_button.clicked.connect(self.undo_last_edit)
        self.redo_button.clicked.connect(self.redo_last_edit)
        self.preset_combo.currentTextChanged.connect(self._preset_changed)

    def set_canvas_view(self, canvas) -> None:
        self._canvas = canvas
        self._refresh_overlay()

    def set_context(self, manager, source_sheet_id: str | None, project_path=None) -> None:
        self._manager = manager
        self._source_sheet_id = source_sheet_id
        self._project_path = project_path
        if source_sheet_id is None:
            self.proposal_list.clear()
            self.analysis_summary.setText("No source sheet selected")
            self._refresh_details()
            return
        self._load_sheet_state()

    def current_settings(self) -> DetectionSettingsModel:
        methods = []
        if self.methods_background.isChecked():
            methods.append("background_difference")
        if self.methods_edge.isChecked():
            methods.append("edge_based")
        if self.methods_components.isChecked():
            methods.append("connected_components")
        if self.methods_variance.isChecked():
            methods.append("color_variance")
        return DetectionSettingsModel(
            preset_name=self.preset_combo.currentText(),
            methods=tuple(methods),
            background_tolerance=self.background_tolerance_spin.value(),
            min_width=self.min_width_spin.value(),
            min_height=self.min_height_spin.value(),
            max_width=self.max_width_spin.value(),
            max_height=self.max_height_spin.value(),
            min_area=self.min_area_spin.value(),
            max_area=self.max_area_spin.value(),
            merge_distance=self.merge_distance_spin.value(),
            close_gap_radius=self.close_gap_spin.value(),
            overlap_merge_threshold=self.overlap_threshold_spin.value(),
            aspect_ratio_min=self.aspect_min_spin.value(),
            aspect_ratio_max=self.aspect_max_spin.value(),
            minimum_edge_density=self.min_edge_density_spin.value(),
            maximum_text_likelihood=self.max_text_likelihood_spin.value(),
            text_rejection_enabled=self.text_rejection_checkbox.isChecked(),
            ignore_text_heavy_regions=self.ignore_text_checkbox.isChecked(),
            connectivity=8 if self.connectivity_combo.currentText() == "8" else 4,
            padding=self.padding_spin.value(),
            proposal_limit=self.proposal_limit_spin.value(),
            show_assigned=self.show_assigned_checkbox.isChecked(),
            hide_rejected=self.hide_rejected_checkbox.isChecked(),
            show_proposal_numbers=self.show_numbers_checkbox.isChecked(),
            show_confidence_labels=self.show_confidence_checkbox.isChecked(),
            show_exclusion_zones=self.show_exclusions_checkbox.isChecked(),
        )

    def _load_sheet_state(self) -> None:
        if self._manager is None or self._source_sheet_id is None:
            return
        sheet = self._manager.source_sheet(self._source_sheet_id)
        settings = sheet.detection_settings
        self._load_settings(settings)
        self._populate_proposals()
        self._refresh_overlay()

    def _load_settings(self, settings: DetectionSettingsModel) -> None:
        self.preset_combo.blockSignals(True)
        self.background_tolerance_spin.setValue(settings.background_tolerance)
        self.min_width_spin.setValue(settings.min_width)
        self.min_height_spin.setValue(settings.min_height)
        self.max_width_spin.setValue(settings.max_width)
        self.max_height_spin.setValue(settings.max_height)
        self.min_area_spin.setValue(settings.min_area)
        self.max_area_spin.setValue(settings.max_area)
        self.merge_distance_spin.setValue(settings.merge_distance)
        self.close_gap_spin.setValue(settings.close_gap_radius)
        self.overlap_threshold_spin.setValue(settings.overlap_merge_threshold)
        self.aspect_min_spin.setValue(settings.aspect_ratio_min)
        self.aspect_max_spin.setValue(settings.aspect_ratio_max)
        self.min_edge_density_spin.setValue(settings.minimum_edge_density)
        self.max_text_likelihood_spin.setValue(settings.maximum_text_likelihood)
        self.connectivity_combo.setCurrentText(str(settings.connectivity))
        self.padding_spin.setValue(settings.padding)
        self.proposal_limit_spin.setValue(settings.proposal_limit)
        self.text_rejection_checkbox.setChecked(settings.text_rejection_enabled)
        self.ignore_text_checkbox.setChecked(settings.ignore_text_heavy_regions)
        self.show_assigned_checkbox.setChecked(settings.show_assigned)
        self.hide_rejected_checkbox.setChecked(settings.hide_rejected)
        self.show_numbers_checkbox.setChecked(settings.show_proposal_numbers)
        self.show_confidence_checkbox.setChecked(settings.show_confidence_labels)
        self.show_exclusions_checkbox.setChecked(settings.show_exclusion_zones)
        if settings.preset_name in DETECTION_PRESETS:
            self.preset_combo.setCurrentText(settings.preset_name)
        else:
            self.preset_combo.setCurrentText("Custom")
        self.preset_combo.blockSignals(False)

    def _populate_proposals(self) -> None:
        self.proposal_list.clear()
        if self._manager is None or self._source_sheet_id is None:
            return
        sheet = self._manager.source_sheet(self._source_sheet_id)
        for proposal in sheet.crop_proposals:
            item = QListWidgetItem(f"{proposal.status.value}: {proposal.rect.x},{proposal.rect.y} {proposal.rect.width}x{proposal.rect.height}")
            item.setData(Qt.ItemDataRole.UserRole, proposal.proposal_uuid)
            self.proposal_list.addItem(item)
        self._refresh_details()

    def _selected_proposal(self) -> CropProposal | None:
        if self._manager is None or self._source_sheet_id is None:
            return None
        item = self.proposal_list.currentItem()
        if item is None:
            return None
        try:
            return self._manager.proposal_by_uuid(self._source_sheet_id, str(item.data(Qt.ItemDataRole.UserRole)))
        except Exception:
            return None

    def _on_selection_changed(self, *_args) -> None:
        self._refresh_details()
        self._refresh_overlay()

    def _refresh_details(self) -> None:
        proposal = self._selected_proposal()
        if proposal is None:
            self.detail_preview.setText("No proposal selected")
            self.detail_location.setText("-")
            self.detail_size.setText("-")
            self.detail_confidence.setText("-")
            self.detail_text_likelihood.setText("-")
            self.detail_methods.setText("-")
            self.detail_components.setText("-")
            self.detail_foreground.setText("-")
            self.detail_assigned.setText("-")
            self.notes_edit.blockSignals(True)
            self.notes_edit.clear()
            self.notes_edit.blockSignals(False)
            return
        self.detail_preview.setText(f"{proposal.rect.x},{proposal.rect.y} {proposal.rect.width}x{proposal.rect.height}")
        self.detail_location.setText(f"{proposal.rect.x}, {proposal.rect.y}")
        self.detail_size.setText(f"{proposal.rect.width} x {proposal.rect.height}")
        self.detail_confidence.setText(f"{proposal.confidence:.3f}")
        self.detail_text_likelihood.setText(f"{proposal.text_likelihood:.3f}")
        self.detail_methods.setText(", ".join(proposal.methods))
        self.detail_components.setText(str(proposal.component_count))
        self.detail_foreground.setText(f"{proposal.foreground_area_percentage:.3f}")
        self.detail_assigned.setText(proposal.assigned_asset_uuid or "-")
        self.notes_edit.blockSignals(True)
        self.notes_edit.setPlainText(proposal.notes)
        self.notes_edit.blockSignals(False)

    def _refresh_overlay(self) -> None:
        if self._canvas is None or self._manager is None or self._source_sheet_id is None:
            return
        sheet = self._manager.source_sheet(self._source_sheet_id)
        self._canvas.set_detection_overlay(
            sheet.crop_proposals,
            sheet.exclusion_zones,
            selected_ids={self._selected_proposal().proposal_uuid} if self._selected_proposal() else set(),
            show_numbers=self.show_numbers_checkbox.isChecked(),
            show_confidence=self.show_confidence_checkbox.isChecked(),
            show_assigned=self.show_assigned_checkbox.isChecked(),
            hide_rejected=self.hide_rejected_checkbox.isChecked(),
            show_exclusion_zones=self.show_exclusions_checkbox.isChecked(),
        )

    def _preset_changed(self, text: str) -> None:
        if self._manager is None or text == "Custom":
            return
        settings = apply_detection_preset(self.current_settings(), text)
        self._load_settings(settings)

    def reset_settings(self) -> None:
        self._load_settings(apply_detection_preset(DetectionSettingsModel(), self.preset_combo.currentText() if self.preset_combo.currentText() != "Custom" else "Broad Search"))

    def save_preset(self) -> None:
        if self._manager is None:
            return
        settings = self.current_settings()
        self._manager.save_detection_preset(settings.preset_name, settings)
        self.analysis_summary.setText(f"Saved preset {settings.preset_name}")

    def load_preset(self) -> None:
        if self._manager is None:
            return
        settings = self._manager.load_detection_preset(self.preset_combo.currentText())
        self._load_settings(settings)

    def _start_worker(self, commit: bool) -> None:
        if self._manager is None or self._source_sheet_id is None:
            return
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(200)
        settings = self.current_settings()
        self._thread = QThread(self)
        self._worker = DetectionWorker(self._manager, self._source_sheet_id, settings, commit=commit)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(lambda result, detected_settings: self._on_worker_finished(result, detected_settings, commit))
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.cancelled.connect(self._on_worker_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.start()

    def _on_worker_finished(self, result, settings, commit: bool) -> None:
        self._last_result = result
        if commit and self._manager is not None and self._source_sheet_id is not None:
            self._manager.apply_detection_result(self._source_sheet_id, result, settings)
        self._populate_proposals()
        self.analysis_summary.setText(f"{len(result.proposals)} proposals found")
        self.changed.emit()
        self._refresh_overlay()

    def _on_worker_failed(self, message: str) -> None:
        self.analysis_summary.setText(message)

    def _on_worker_cancelled(self) -> None:
        self.analysis_summary.setText("Detection cancelled")

    def analyze(self) -> None:
        self._start_worker(commit=False)
        self.analyzeRequested.emit()

    def generate_proposals(self) -> None:
        self._start_worker(commit=True)
        self.generateRequested.emit()

    def preview_mask(self) -> None:
        if self._last_result is None:
            self.analyze()
            return
        combined = self._last_result.combined_mask
        self.analysis_summary.setText(f"Mask coverage: {int(combined.sum())} pixels")

    def _set_status(self, status: ProposalStatus) -> None:
        proposal = self._selected_proposal()
        if proposal is None or self._manager is None or self._source_sheet_id is None:
            return
        proposal.status = status
        proposal.user_modified = True
        proposal.modified_at = utc_now_iso()
        self._manager.project.mark_modified()
        self._populate_proposals()
        self.changed.emit()
        self._refresh_overlay()

    def _save_notes(self) -> None:
        proposal = self._selected_proposal()
        if proposal is None or self._manager is None:
            return
        proposal.notes = self.notes_edit.toPlainText().strip()
        proposal.modified_at = utc_now_iso()
        self._manager.project.mark_modified()

    def assign_to_active_asset(self) -> None:
        proposal = self._selected_proposal()
        if proposal is None or self._manager is None or self._source_sheet_id is None:
            return
        asset = self._manager.active_asset
        if asset is None:
            return
        self._manager.assign_proposal_to_asset(self._source_sheet_id, proposal.proposal_uuid, asset.asset_uuid)
        self._populate_proposals()
        self.changed.emit()

    def create_new_asset_from_proposal(self) -> None:
        proposal = self._selected_proposal()
        if proposal is None:
            return
        self.createAssetRequested.emit(proposal.proposal_uuid)

    def merge_selected(self) -> None:
        if self._manager is None or self._source_sheet_id is None:
            return
        uuids = [item.data(Qt.ItemDataRole.UserRole) for item in self.proposal_list.selectedItems()]
        if len(uuids) < 2:
            return
        self._manager.merge_proposals(self._source_sheet_id, [str(item) for item in uuids], padding=self.padding_spin.value())
        self._populate_proposals()
        self.changed.emit()

    def split_selected(self) -> None:
        proposal = self._selected_proposal()
        if proposal is None or self._manager is None or self._source_sheet_id is None:
            return
        self._manager.split_proposal_vertical(self._source_sheet_id, proposal.proposal_uuid, proposal.rect.x + max(1, proposal.rect.width // 2))
        self._populate_proposals()
        self.changed.emit()

    def undo_last_edit(self) -> None:
        if self._manager is None or self._source_sheet_id is None:
            return
        if self._manager.undo_proposal_edit(self._source_sheet_id):
            self._populate_proposals()
            self.changed.emit()

    def redo_last_edit(self) -> None:
        if self._manager is None or self._source_sheet_id is None:
            return
        if self._manager.redo_proposal_edit(self._source_sheet_id):
            self._populate_proposals()
            self.changed.emit()
