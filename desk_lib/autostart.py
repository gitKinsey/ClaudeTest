"""Start the app when the user logs in (Windows: Run registry key, macOS: LaunchAgent, Linux: XDG autostart). Paths / the registry are
injectable so every branch is testable on any system."""
import platform
import sys
from pathlib import Path

NAME = "DeskCompanion"


def launch_command(frozen=None, exe=None, script=None):
    """argv that starts the app minimised: the packaged app itself, or python + companion_qt.py."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = exe or sys.executable
    if frozen:
        return [exe, "--minimized"]
    return [exe, str(script or (Path(__file__).resolve().parent.parent / "companion_qt.py")), "--minimized"]


def _quote(argv):
    return " ".join(f'"{a}"' if (" " in a or not a) else a for a in argv)


def _desktop_path(home):
    return Path(home) / ".config" / "autostart" / "deskcompanion.desktop"


def _plist_path(home):
    return Path(home) / "Library" / "LaunchAgents" / "com.deskcompanion.app.plist"


def _winkey(winreg):
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)


def is_enabled(system=None, home=None, winreg=None):
    system, home = system or platform.system(), home or Path.home()
    if system == "Windows":
        try:
            winreg = winreg or __import__("winreg")
            with _winkey(winreg) as k:
                winreg.QueryValueEx(k, NAME)
            return True
        except OSError:
            return False
    return (_plist_path(home) if system == "Darwin" else _desktop_path(home)).is_file()


def enable(argv, system=None, home=None, winreg=None):
    system, home = system or platform.system(), home or Path.home()
    if system == "Windows":
        winreg = winreg or __import__("winreg")
        with _winkey(winreg) as k:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _quote(argv))
        return "registry"
    if system == "Darwin":
        p = _plist_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"<string>{_xml(a)}</string>" for a in argv)
        p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                     f'<plist version="1.0"><dict><key>Label</key><string>com.deskcompanion.app</string><key>ProgramArguments</key><array>{args}</array>'
                     '<key>RunAtLoad</key><true/></dict></plist>\n', encoding="utf-8")
        return str(p)
    p = _desktop_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"[Desktop Entry]\nType=Application\nName=Desk Companion\nComment=Macro pad companion\nExec={_quote(argv)}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
                 encoding="utf-8")
    return str(p)


def disable(system=None, home=None, winreg=None):
    system, home = system or platform.system(), home or Path.home()
    if system == "Windows":
        try:
            winreg = winreg or __import__("winreg")
            with _winkey(winreg) as k:
                winreg.DeleteValue(k, NAME)
        except OSError:
            pass
        return
    p = _plist_path(home) if system == "Darwin" else _desktop_path(home)
    try:
        p.unlink()
    except OSError:
        pass


def _xml(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wants_minimized(argv=None):
    return "--minimized" in (sys.argv if argv is None else argv)

