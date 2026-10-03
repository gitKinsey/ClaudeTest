"""Builds the Qt application: engine, theme, shell, pages, dialogs, tray."""
import sys

from PySide6.QtWidgets import QApplication

from core.engine import Engine
from ui_qt.frontend import QtFrontend
from ui_qt.shell import Shell
from ui_qt.theme import theme


def make_pages(shell):
    from ui_qt.pages.display import DisplayPage
    from ui_qt.pages.keys import KeysPage
    from ui_qt.pages.overview import OverviewPage
    from ui_qt.pages.padapp import PadAppPage
    from ui_qt.pages.rules import RulesPage
    from ui_qt.pages.scripts import ScriptsPage
    return [OverviewPage(shell), KeysPage(shell), DisplayPage(shell),
            RulesPage(shell),
            ScriptsPage(shell),
            PadAppPage(shell)]


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
    wire_windows(engine, shell)
    return app, engine, shell


def wire_windows(engine, shell):
    from ui_qt import dialogs
    state = {"mini": None}

    def show(dlg):
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        state.setdefault("open", []).append(dlg)

    def mini():
        m = state["mini"]
        if m is not None and m.isVisible():
            m.raise_()
            return
        state["mini"] = dialogs.MiniPad(shell)
        state["mini"].show()
    engine.on("open_wizard", lambda: show(dialogs.SetupWizard(shell)))
    engine.on("open_hwtest", lambda: show(dialogs.HardwareTest(shell)))
    engine.on("open_palette", lambda: show(dialogs.CommandPalette(shell, dialogs.palette_commands(shell))))
    engine.on("open_autobackup", lambda: show(dialogs.AutoBackupDialog(shell)))
    engine.on("open_mini_window", mini)
