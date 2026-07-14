from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..batch_export import (
    BatchExportSettings,
    ExportScope,
    ExportVariant,
    OverwritePolicy,
    ValidationIssue,
    ValidationSeverity,
    export_report_csv,
    export_report_html,
    export_report_json,
)


class BatchExportWorker(QObject):
    finished = Signal(object, object)
    failed = Signal(str)

    def __init__(self, manager, settings: BatchExportSettings) -> None:
        super().__init__()
        self._manager = manager
        self._settings = settings
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        return self._cancelled

    @Slot()
    def run(self) -> None:
        try:
            result = self._manager.run_batch_export(self._settings, cancel_requested=self._is_cancelled)
            self.finished.emit(*result)
        except Exception as exc:  # pragma: no cover - UI error path
            self.failed.emit(str(exc))


class BatchExportDialog(QDialog):
    def __init__(self, manager, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._thread: QThread | None = None
        self._worker: BatchExportWorker | None = None
        self._preview_entries = []
        self._build_ui()
        self._refresh_preview()

    def _build_ui(self) -> None:
        self.setWindowTitle("Batch Export")
        self.resize(1300, 900)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        self.scope_combo = QComboBox()
        self.scope_combo.addItems([item.value for item in ExportScope])
        self.asset_filter_combo = QComboBox()
        self.asset_filter_combo.addItems(["All assets", "Selected assets", "Filtered assets"])
        self.character_group_edit = QLineEdit()
        self.category_edit = QLineEdit()
        self.alignment_group_edit = QLineEdit()
        self.output_root_edit = QLineEdit()
        self.output_root_edit.setText(str(Path.cwd() / "output"))
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self._browse_output_root)
        self.project_relative_checkbox = QCheckBox("Project-relative output")
        self.project_relative_checkbox.setChecked(True)
        self.flat_output_checkbox = QCheckBox("Flat output mode")
        self.flat_output_checkbox.setChecked(False)
        self.raw_checkbox = QCheckBox("Raw Crop")
        self.auto_clean_checkbox = QCheckBox("Auto Clean")
        self.final_checkbox = QCheckBox("Final Edited")
        self.normalized_checkbox = QCheckBox("Normalized Output")
        self.normalized_checkbox.setChecked(True)
        self.include_raw_backup_checkbox = QCheckBox("Include Raw Reference Crop")
        self.include_auto_backup_checkbox = QCheckBox("Include Auto Clean Backup")
        self.include_final_backup_checkbox = QCheckBox("Include Final Edited Backup")
        self.overwrite_combo = QComboBox()
        self.overwrite_combo.addItems([item.value for item in OverwritePolicy])
        self.overwrite_combo.setCurrentText(OverwritePolicy.compare_checksum_skip_identical.value)
        self.directory_template_edit = QLineEdit("Characters/{character}/{category}/{direction}")
        self.filename_template_edit = QLineEdit("{character}_{action}_{direction}_{frame}_{width}x{height}.png")
        self.frame_padding_combo = QComboBox()
        self.frame_padding_combo.addItems(["2", "3", "4"])
        self.frame_padding_combo.setCurrentText("2")
        self.manifest_check = QCheckBox("Generate manifest")
        self.manifest_check.setChecked(True)
        self.csv_manifest_check = QCheckBox("CSV")
        self.csv_manifest_check.setChecked(True)
        self.json_manifest_check = QCheckBox("JSON")
        self.json_manifest_check.setChecked(True)
        self.html_manifest_check = QCheckBox("HTML")
        self.html_manifest_check.setChecked(True)
        self.validation_summary = QLabel("No preview yet")
        self.validation_summary.setWordWrap(True)
        self.preview_table = QTableWidget(0, 8)
        self.preview_table.setHorizontalHeaderLabels(
            ["Asset", "Variant", "Path", "Size", "Validation", "Overwrite", "Existing", "Checksum"]
        )
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)

        scope_form = QFormLayout()
        scope_form.addRow("Asset Scope", self.scope_combo)
        scope_form.addRow("Selection Filter", self.asset_filter_combo)
        scope_form.addRow("Character Group", self.character_group_edit)
        scope_form.addRow("Category", self.category_edit)
        scope_form.addRow("Alignment Group", self.alignment_group_edit)
        scope_form.addRow("Output Root", self.output_root_edit)
        scope_row = QHBoxLayout()
        scope_row.addWidget(browse_button)
        scope_row.addWidget(self.project_relative_checkbox)
        scope_row.addWidget(self.flat_output_checkbox)
        scope_form.addRow(scope_row)
        scope_widget = QWidget()
        scope_widget.setLayout(scope_form)
        tabs.addTab(scope_widget, "Asset Scope")

        variant_widget = QWidget()
        variant_form = QFormLayout(variant_widget)
        variant_form.addRow("Raw Crop", self.raw_checkbox)
        variant_form.addRow("Auto Clean", self.auto_clean_checkbox)
        variant_form.addRow("Final Edited", self.final_checkbox)
        variant_form.addRow("Normalized Output", self.normalized_checkbox)
        variant_form.addRow("Raw Backup", self.include_raw_backup_checkbox)
        variant_form.addRow("Auto Clean Backup", self.include_auto_backup_checkbox)
        variant_form.addRow("Final Backup", self.include_final_backup_checkbox)
        tabs.addTab(variant_widget, "Variants")

        naming_widget = QWidget()
        naming_form = QFormLayout(naming_widget)
        naming_form.addRow("Directory Template", self.directory_template_edit)
        naming_form.addRow("Filename Template", self.filename_template_edit)
        naming_form.addRow("Frame Padding", self.frame_padding_combo)
        naming_form.addRow("Overwrite Policy", self.overwrite_combo)
        tabs.addTab(naming_widget, "Naming")

        manifest_widget = QWidget()
        manifest_form = QFormLayout(manifest_widget)
        manifest_form.addRow("Generate Manifest", self.manifest_check)
        manifest_form.addRow("CSV", self.csv_manifest_check)
        manifest_form.addRow("JSON", self.json_manifest_check)
        manifest_form.addRow("HTML", self.html_manifest_check)
        manifest_form.addRow("Validation", self.validation_summary)
        tabs.addTab(manifest_widget, "Manifest")

        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.addWidget(self.preview_table)
        tabs.addTab(preview_widget, "Preview")

        progress_widget = QWidget()
        progress_layout = QVBoxLayout(progress_widget)
        progress_layout.addWidget(self.progress)
        tabs.addTab(progress_widget, "Progress")

        controls = QHBoxLayout()
        self.preview_button = QPushButton("Refresh Preview")
        self.export_button = QPushButton("Start Export")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        controls.addWidget(self.preview_button)
        controls.addWidget(self.export_button)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)

        self.preview_button.clicked.connect(self._refresh_preview)
        self.export_button.clicked.connect(self._start_export)
        self.cancel_button.clicked.connect(self._cancel_export)

    def _browse_output_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Root", self.output_root_edit.text() or str(Path.cwd() / "output"))
        if folder:
            self.output_root_edit.setText(folder)
            self._refresh_preview()

    def _settings(self) -> BatchExportSettings:
        variants = []
        if self.raw_checkbox.isChecked():
            variants.append(ExportVariant.raw)
        if self.auto_clean_checkbox.isChecked():
            variants.append(ExportVariant.auto_clean)
        if self.final_checkbox.isChecked():
            variants.append(ExportVariant.final_edited)
        if self.normalized_checkbox.isChecked():
            variants.append(ExportVariant.normalized)
        return BatchExportSettings(
            scope=ExportScope(self.scope_combo.currentText()),
            selected_asset_uuids=[],
            variants=variants or [ExportVariant.normalized],
            include_final_backup=self.include_final_backup_checkbox.isChecked(),
            include_auto_clean_backup=self.include_auto_backup_checkbox.isChecked(),
            include_raw_reference_crop=self.include_raw_backup_checkbox.isChecked(),
            output_root=self.output_root_edit.text().strip(),
            project_relative=self.project_relative_checkbox.isChecked(),
            flat_output=self.flat_output_checkbox.isChecked(),
            directory_template=self.directory_template_edit.text().strip(),
            filename_template=self.filename_template_edit.text().strip(),
            frame_padding=int(self.frame_padding_combo.currentText()),
            overwrite_policy=OverwritePolicy(self.overwrite_combo.currentText()),
            generate_manifest=self.manifest_check.isChecked(),
            generate_csv_manifest=self.csv_manifest_check.isChecked(),
            generate_json_manifest=self.json_manifest_check.isChecked(),
            generate_html_manifest=self.html_manifest_check.isChecked(),
            selected_character_group=self.character_group_edit.text().strip(),
            selected_category=self.category_edit.text().strip(),
            selected_alignment_group=self.alignment_group_edit.text().strip(),
        )

    def _refresh_preview(self) -> None:
        settings = self._settings()
        entries = self._manager.batch_export_preview(settings)
        self._preview_entries = entries
        self.preview_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                entry.asset_name,
                entry.variant.value,
                entry.destination_path,
                f"{entry.output_width}x{entry.output_height}",
                entry.validation_state,
                entry.overwrite_action,
                entry.existing_file_state,
                entry.checksum_compare_result,
            ]
            for col, value in enumerate(values):
                self.preview_table.setItem(row, col, QTableWidgetItem(str(value)))
        summary = self._manager.validate_project()
        blocked = sum(1 for issue in summary if issue.severity == ValidationSeverity.blocked)
        warnings = sum(1 for issue in summary if issue.severity == ValidationSeverity.warning)
        self.validation_summary.setText(f"{len(entries)} preview items, {blocked} blocked issues, {warnings} warnings")

    def _start_export(self) -> None:
        self.progress.setValue(0)
        self.export_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(100)
        self._thread = QThread(self)
        self._worker = BatchExportWorker(self._manager, self._settings())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_export_finished)
        self._worker.failed.connect(self._on_export_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _cancel_export(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.validation_summary.setText("Cancellation requested")

    def _on_export_finished(self, manifest, state) -> None:
        self.progress.setValue(100)
        self.export_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.validation_summary.setText(
            f"Export complete: {len(manifest.entries)} items, {len(state.exported_files)} files written"
        )
        self.accept()

    def _on_export_failed(self, message: str) -> None:
        self.export_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        QMessageBox.critical(self, "Batch Export Failed", message)


class ProjectValidationDialog(QDialog):
    def __init__(self, manager, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._issues: list[ValidationIssue] = []
        self.setWindowTitle("Validate Project")
        self.resize(1250, 800)
        layout = QVBoxLayout(self)
        filter_row = QHBoxLayout()
        self.severity_combo = QComboBox()
        self.severity_combo.addItems(["All", "blocked", "warning", "info"])
        self.character_edit = QLineEdit()
        self.category_edit = QLineEdit()
        self.issue_type_edit = QLineEdit()
        self.only_blocked = QCheckBox("Only Blocked")
        self.only_warnings = QCheckBox("Only Warnings")
        self.only_autofix = QCheckBox("Only Auto-fixable")
        for widget in (self.severity_combo, self.character_edit, self.category_edit, self.issue_type_edit, self.only_blocked, self.only_warnings, self.only_autofix):
            filter_row.addWidget(widget)
        layout.addLayout(filter_row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Severity", "Asset", "Category", "Issue Code", "Message", "Suggested Fix", "Auto-fix"])
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Revalidate")
        self.export_csv_button = QPushButton("Export CSV")
        self.export_json_button = QPushButton("Export JSON")
        self.export_html_button = QPushButton("Export HTML")
        self.close_button = QPushButton("Close")
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.export_csv_button)
        buttons.addWidget(self.export_json_button)
        buttons.addWidget(self.export_html_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.refresh_button.clicked.connect(self.refresh_issues)
        self.export_csv_button.clicked.connect(self.export_csv)
        self.export_json_button.clicked.connect(self.export_json)
        self.export_html_button.clicked.connect(self.export_html)
        self.close_button.clicked.connect(self.reject)
        for widget in (self.severity_combo, self.character_edit, self.category_edit, self.issue_type_edit, self.only_blocked, self.only_warnings, self.only_autofix):
            if hasattr(widget, "currentTextChanged"):
                widget.currentTextChanged.connect(self._apply_filters)
            if hasattr(widget, "textChanged"):
                widget.textChanged.connect(self._apply_filters)
            if hasattr(widget, "toggled"):
                widget.toggled.connect(self._apply_filters)
        self.refresh_issues()

    def refresh_issues(self) -> None:
        self._issues = self._manager.validate_project()
        self._apply_filters()

    def _apply_filters(self, *_args) -> None:
        issues = list(self._issues)
        severity = self.severity_combo.currentText()
        if severity != "All":
            issues = [issue for issue in issues if issue.severity.value == severity]
        if self.only_blocked.isChecked():
            issues = [issue for issue in issues if issue.severity == ValidationSeverity.blocked]
        if self.only_warnings.isChecked():
            issues = [issue for issue in issues if issue.severity == ValidationSeverity.warning]
        if self.only_autofix.isChecked():
            issues = [issue for issue in issues if issue.auto_fix_available]
        text = self.character_edit.text().strip().lower()
        if text:
            issues = [issue for issue in issues if text in issue.asset_name.lower()]
        category = self.category_edit.text().strip().lower()
        if category:
            issues = [issue for issue in issues if category in issue.category.lower()]
        issue_type = self.issue_type_edit.text().strip().lower()
        if issue_type:
            issues = [issue for issue in issues if issue_type in issue.code.lower()]
        self.table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            values = [
                issue.severity.value,
                issue.asset_name,
                issue.category,
                issue.code,
                issue.message,
                issue.suggested_fix,
                "yes" if issue.auto_fix_available else "no",
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Validation CSV", str(Path.cwd() / "validation_report.csv"), "CSV Files (*.csv)")
        if path:
            export_report_csv(self._issues, path)

    def export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Validation JSON", str(Path.cwd() / "validation_report.json"), "JSON Files (*.json)")
        if path:
            export_report_json(self._issues, path)

    def export_html(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Validation HTML", str(Path.cwd() / "validation_report.html"), "HTML Files (*.html)")
        if path:
            export_report_html(self._issues, path)

