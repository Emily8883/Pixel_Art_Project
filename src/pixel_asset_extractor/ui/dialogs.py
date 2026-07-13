from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..project_model import AssetRecord, ActivityEntry


class AssetDialog(QDialog):
    def __init__(self, parent=None, source_sheets: list[tuple[str, str]] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Asset")
        self.setModal(True)
        self._source_sheets = source_sheets or []

        self.character_edit = QComboBox()
        self.character_edit.setEditable(True)
        self.category_edit = QComboBox()
        self.category_edit.setEditable(True)
        self.direction_edit = QComboBox()
        self.direction_edit.setEditable(True)
        self.action_edit = QComboBox()
        self.action_edit.setEditable(True)
        self.frame_edit = QLineEdit()
        self.variant_edit = QLineEdit()
        self.display_name_edit = QLineEdit()
        self.output_folder_edit = QLineEdit()
        self.notes_edit = QTextEdit()
        self.source_sheet_combo = QComboBox()

        self.category_edit.addItems(["idle", "walk", "attack", "effect", "item", "portrait", "environment", "ui", "other"])
        self.direction_edit.addItems(["front", "back", "left", "right", "none"])

        for sheet_id, label in self._source_sheets:
            self.source_sheet_combo.addItem(label, sheet_id)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Character/Group", self.character_edit)
        form.addRow("Category", self.category_edit)
        form.addRow("Action", self.action_edit)
        form.addRow("Direction", self.direction_edit)
        form.addRow("Frame Number", self.frame_edit)
        form.addRow("Variant", self.variant_edit)
        form.addRow("Display Name", self.display_name_edit)
        form.addRow("Source Sheet", self.source_sheet_combo)
        form.addRow("Output Folder", self.output_folder_edit)
        layout.addLayout(form)
        layout.addWidget(QLabel("Notes"))
        layout.addWidget(self.notes_edit)

        buttons_row = QHBoxLayout()
        choose_output = QPushButton("Choose Output Folder")
        choose_output.clicked.connect(self._choose_output_folder)
        buttons_row.addWidget(choose_output)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

    def _choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder", str(Path.cwd() / "output"))
        if folder:
            self.output_folder_edit.setText(folder)

    def payload(self) -> dict[str, object]:
        frame = self.frame_edit.text().strip()
        frame_number = int(frame) if frame.isdigit() else None
        return {
            "character_group": self.character_edit.currentText().strip(),
            "category": self.category_edit.currentText().strip(),
            "action": self.action_edit.currentText().strip(),
            "direction": self.direction_edit.currentText().strip(),
            "frame_number": frame_number,
            "variant": self.variant_edit.text().strip(),
            "display_name": self.display_name_edit.text().strip(),
            "source_sheet_id": self.source_sheet_combo.currentData() or "",
            "output_folder": self.output_folder_edit.text().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
        }


class ActivityLogDialog(QDialog):
    def __init__(self, entries: list[ActivityEntry], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Activity Log")
        self.resize(800, 500)
        layout = QVBoxLayout(self)
        view = QTextEdit()
        view.setReadOnly(True)
        lines = []
        for entry in entries[-1000:]:
            asset = f" [{entry.asset_uuid}]" if entry.asset_uuid else ""
            lines.append(f"{entry.timestamp} {entry.event_type}{asset} - {entry.message}")
        view.setPlainText("\n".join(lines))
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

