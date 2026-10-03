#!/usr/bin/env python3
"""Desk Companion (Qt edition) - the desktop app for the ESP32-S3 macro pad.

    pip install PySide6 pyserial psutil pillow pynput      (pynput is optional: live test on this PC)
    python companion_qt.py
"""
import sys

from core.base import APP_DIR, APP_NAME


def _flash_helper(argv):
    """`DeskCompanion --flash-helper --image full ...`: run firmware/flash.py inside this (possibly frozen) interpreter."""
    sys.path.insert(0, str(APP_DIR / "firmware"))
    import flash                                          # firmware/flash.py
    return flash.main(argv)


def main():
    if "--flash-helper" in sys.argv:
        return _flash_helper([a for a in sys.argv[1:] if a != "--flash-helper"])
    from ui_qt.app import create
    app, engine, shell = create(sys.argv)
    app.setApplicationName(APP_NAME)
    from desk_lib import autostart
    if autostart.wants_minimized():                           # started by "Start with my computer": go straight to the tray / taskbar
        if engine.cfg.get("tray") and not engine.tray_enable(True):
            shell.hide()
        else:
            shell.showMinimized()
    else:
        if engine.cfg.get("tray"):
            engine.tray_enable(True)
        shell.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
