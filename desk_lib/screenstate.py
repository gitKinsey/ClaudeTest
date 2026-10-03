"""What the screen is doing: is the PC locked, is the focused window fullscreen - so the pad can dim itself. Best effort per OS,
runners injectable. Every function returns None when it cannot tell (the app then does nothing)."""
import os
import platform
import re
import subprocess


def _run(args, timeout=3):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


class ScreenState:
    def __init__(self, runner=None, system=None, env=None, win_locked=None, win_fullscreen=None):
        self.run, self.system, self.env = runner or _run, system or platform.system(), os.environ if env is None else env
        self._win_locked, self._win_fullscreen = win_locked, win_fullscreen          # callables for the Windows branch (ctypes), injectable for tests

    def locked(self):
        """True / False / None (unknown)."""
        if self.system == "Linux":
            sid = self.env.get("XDG_SESSION_ID")
            out = self.run(["loginctl", "show-session", sid, "-p", "LockedHint"]) if sid else self.run(["loginctl", "show-session", "self", "-p", "LockedHint"])
            if out and "LockedHint=" in out:
                return "LockedHint=yes" in out
            out = self.run(["gnome-screensaver-command", "-q"])
            return None if out is None else "is active" in out
        if self.system == "Darwin":
            out = self.run(["ioreg", "-n", "Root", "-d1"])
            return None if out is None else bool(re.search(r'"CGSSessionScreenIsLocked"\s*=\s*(Yes|1)', out))
        if self.system == "Windows":
            return (self._win_locked or _windows_locked)()
        return None

    def fullscreen(self):
        """True when the window in front covers the whole screen (a video, a game, a presentation)."""
        if self.system == "Linux":
            wid = (self.run(["xdotool", "getactivewindow"]) or "").strip()
            if not wid:
                return None
            out = self.run(["xprop", "-id", wid, "_NET_WM_STATE"])
            return None if out is None else "_NET_WM_STATE_FULLSCREEN" in out
        if self.system == "Darwin":
            out = self.run(["osascript", "-e", 'tell application "System Events" to get value of attribute "AXFullScreen" of window 1 of (first process whose frontmost is true)'])
            return None if out is None else out.strip().lower() == "true"
        if self.system == "Windows":
            return (self._win_fullscreen or _windows_fullscreen)()
        return None


def _windows_locked():
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.OpenInputDesktop(0, False, 0x0100)         # DESKTOP_SWITCHDESKTOP: fails while the secure (lock screen) desktop is active
        if not h:
            return True
        u.CloseDesktop(h)
        return False
    except Exception:                                    # noqa: BLE001
        return None


def _windows_fullscreen():
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        if not h:
            return None
        r = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        return (r.right - r.left) >= sw and (r.bottom - r.top) >= sh and r.left <= 0 and r.top <= 0
    except Exception:                                    # noqa: BLE001
        return None
