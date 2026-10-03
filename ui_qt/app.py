"""Builds the Qt application: engine, theme, shell, pages, dialogs, tray."""
import sys

from PySide6.QtWidgets import QApplication

from core.engine import Engine
from ui_qt.frontend import QtFrontend
from ui_qt.shell import Shell
from ui_qt.theme import theme


def make_pages(shell):
    from ui_qt.pages.keys import KeysPage
    from ui_qt.pages.overview import OverviewPage
    from ui_qt.pages.stubs import StubPage
    return [OverviewPage(shell), KeysPage(shell), StubPage(shell, "display", "Display", "GIFs, info cards, screens and look", "display"),
            StubPage(shell, "rules", "Rules", "Programs, schedules and computer actions", "rules"),
            StubPage(shell, "scripts", "Scripts", "Loops, conditions and variables for your keys", "scripts", advanced=True),
            StubPage(shell, "padapp", "Pad & App", "Behaviour, firmware, backup and this app", "padapp")]


def create(argv=None, start_threads=True, frontend=None):
    """Returns (app, engine, shell). The caller runs app.exec()."""
    app = QApplication.instance() or QApplication(argv or sys.argv)
    fe = frontend or QtFrontend()
    engine = Engine(fe, start_threads=start_threads)
    cfg = engine.cfg
    theme.configure(cfg.get("appearance", "dark"), cfg.get("accent", "cyan"), cfg.get("ui_scale", 1.0), cfg.get("reduce_motion", False))
    theme.apply(app)
    shell = Shell(engine, make_pages)
    fe.parent = shell
    return app, engine, shell
