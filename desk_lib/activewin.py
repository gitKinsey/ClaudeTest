"""Which program has the keyboard focus right now?  Used by per-app profiles (pad layer follows the focused program)."""
import os
import platform
import re
import shutil
import subprocess


def _run(args, timeout=2):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


class ActiveWindow:
    """get() -> (process_name_lowercase, window_title) or ("", "") when it cannot be determined.
    Linux (X11): xdotool or xprop. macOS: osascript. Windows: Win32 API via ctypes. All lookups are best-effort."""

    def __init__(self, runner=None, system=None):
        self._run = runner or _run
        self.system = system or platform.system()

    def get(self):
        try:
            if self.system == "Windows":
                return self._windows()
            if self.system == "Darwin":
                return self._mac()
            return self._linux()
        except Exception:                                  # noqa: BLE001 - never let a detection problem kill the profile loop
            return "", ""

    def _linux(self):
        title = (self._run(["xdotool", "getactivewindow", "getwindowname"]) or "").strip()
        proc = ""
        pid = (self._run(["xdotool", "getactivewindow", "getwindowpid"]) or "").strip()
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/comm") as f:
                    proc = os.path.basename(f.read().strip()).lower()
            except OSError:
                proc = ""
        if not title and not proc:                         # no xdotool: fall back to xprop
            root = self._run(["xprop", "-root", "_NET_ACTIVE_WINDOW"]) or ""
            m = re.search(r"(0x[0-9a-fA-F]+)", root)
            if m and m.group(1) != "0x0":
                info = self._run(["xprop", "-id", m.group(1), "WM_CLASS", "_NET_WM_NAME"]) or ""
                c = re.search(r'WM_CLASS\(STRING\) = "[^"]*", "([^"]*)"', info)
                n = re.search(r'_NET_WM_NAME\(\w+\) = "(.*)"', info)
                proc = (c.group(1) if c else "").lower()
                title = n.group(1) if n else ""
        return proc, title

    def _mac(self):
        out = self._run(["osascript", "-e",
                         'tell application "System Events" to set p to first application process whose frontmost is true\n'
                         'set t to ""\ntry\ntell p to set t to name of front window\nend try\n'
                         'return (name of p) & "|" & t'])
        if not out:
            return "", ""
        proc, _, title = out.strip().partition("|")
        return proc.lower(), title

    def _windows(self):
        import ctypes
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        h = u.GetForegroundWindow()
        if not h:
            return "", ""
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(h, buf, 512)
        title = buf.value
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        proc = ""
        hp = k.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
        if hp:
            n = wintypes.DWORD(512)
            if k.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(n)):
                proc = os.path.basename(buf.value).lower()
            k.CloseHandle(hp)
        return proc, title


def rule_matches(rule, proc, title):
    """rule = {"match": "code", "kind": "process"|"title"|"either"}; case-insensitive substring."""
    needle = str(rule.get("match", "")).strip().lower()
    if not needle:
        return False
    kind = rule.get("kind", "either")
    if kind == "process":
        return needle in proc
    if kind == "title":
        return needle in title.lower()
    return needle in proc or needle in title.lower()


def pick_layer(rules, proc, title, default=None):
    """First matching enabled rule wins; returns its layer (0..2) or `default` when nothing matches."""
    for r in rules:
        if r.get("enabled", True) and rule_matches(r, proc, title):
            return int(r.get("layer", 0))
    return default


def have_tools():
    """Names of the helper programs available for detection on this system (for the UI hint)."""
    if platform.system() == "Linux":
        return [t for t in ("xdotool", "xprop") if shutil.which(t)]
    return ["built-in"]
