from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .logging_utils import configure_logging, install_excepthook
from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    configure_logging()
    window = MainWindow()
    sys.excepthook = install_excepthook(window._show_error if hasattr(window, "_show_error") else None)
    window.show()
    return app.exec()
