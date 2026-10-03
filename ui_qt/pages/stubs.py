"""Placeholder page used while a page is still being ported."""
from ui_qt.pages.base import Page
from ui_qt.widgets import label


class StubPage(Page):
    def __init__(self, shell, pid, title, subtitle, icon, advanced=False):
        super().__init__(shell)
        self.id, self.title, self.subtitle, self.icon, self.advanced = pid, title, subtitle, icon, advanced
        self.root.addWidget(label("This page is being ported.", "muted"))
        self.root.addStretch(1)
