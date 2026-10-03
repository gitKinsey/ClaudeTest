"""What the engine needs from the Qt front end: questions, file dialogs, clipboard, focus information."""
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from core.engine import NullFrontend


class QtFrontend(NullFrontend):
    def __init__(self):
        super().__init__()
        self.parent = None
        self.sandbox = None

    def confirm(self, title, text):
        return QMessageBox.question(self.parent, title, text) == QMessageBox.StandardButton.Yes

    def ask_open(self, title="", filters=""):
        return QFileDialog.getOpenFileName(self.parent, title, "", filters or "All files (*)")[0]

    def ask_open_many(self, title="", filters=""):
        return QFileDialog.getOpenFileNames(self.parent, title, "", filters or "All files (*)")[0]

    def ask_save(self, title="", initial="", filters="", suffix=""):
        path = QFileDialog.getSaveFileName(self.parent, title, initial, filters or "All files (*)")[0]
        if path and suffix and not path.lower().endswith(suffix):
            path += suffix
        return path

    def clipboard_get(self):
        return QApplication.clipboard().text()

    def clipboard_set(self, text):
        QApplication.clipboard().setText(text)

    def sandbox_has_focus(self):
        return bool(self.sandbox is not None and self.sandbox.hasFocus())
