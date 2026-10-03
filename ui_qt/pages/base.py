"""Base class of the pages."""
from PySide6.QtWidgets import QWidget

from ui_qt.layouts import vbox


class Page(QWidget):
    id = ""
    title = ""
    subtitle = ""
    icon = "dot"
    advanced = False

    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.engine = shell.engine
        self.root = vbox(self, spacing=8)
        self.grid = None

    def on_show(self):
        """Called whenever the page becomes visible."""

    def grid_state(self):
        return self.grid.state() if self.grid is not None else {}

    def restore_grid(self, st):
        if self.grid is not None:
            self.grid.restore(st)

    def show_section(self, section):
        """Jump to a panel / tab (palette, banners)."""
        if self.grid is not None and section in self.grid.panels:
            self.grid.show_panel(section)
