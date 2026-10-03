"""Run the 'host' actions the pad asks for ({"evt":"host","op":...,"arg":...}).

SAFETY: the pad is a USB device. Anything that claims to be the pad could send fake events, so an event is only
executed when the very same (op, arg) is part of YOUR configuration (a key map or a saved macro). Shell commands
additionally need the 'allow shell' switch. Unknown or unlisted requests are refused with a reason."""
import os
import platform
import shlex
import subprocess
import webbrowser
from urllib.parse import urlparse

from desk_lib import textops

SAFE_URL_SCHEMES = ("http", "https", "mailto")


def collect_allowed(layer_maps, customs, resolve):
    """Every (op, arg) the user has configured. layer_maps: list of {slot: {cat, action}}; customs: cfg["custom"];
    resolve(cat, action) -> (type, val) | None (the app's resolve_spec for this OS)."""
    allowed = set()

    def scan(spec):
        if not spec:
            return
        t, v = spec
        if t == "host" and isinstance(v, dict):
            allowed.add((v.get("op"), v.get("arg", "")))
        elif t == "macro" and isinstance(v, list):
            for st in v:
                if isinstance(st, dict) and isinstance(st.get("host"), dict):
                    allowed.add((st["host"].get("op"), st["host"].get("arg", "")))
    for lm in layer_maps:
        for m in lm.values():
            try:
                scan(resolve(m["cat"], m["action"]))
            except Exception:                              # noqa: BLE001
                pass
    for c in customs.values():
        scan((c.get("type"), c.get("val")))
    return allowed


class HostActions:
    def __init__(self, allowed_fn, allow_shell_fn, type_clipboard=None, notify=None, opener=None, runner=None, system=None,
                 type_text=None, read_clipboard=None, counter=None):
        self.allowed_fn, self.allow_shell_fn = allowed_fn, allow_shell_fn
        self.type_clipboard, self.notify = type_clipboard, notify
        self.type_text, self.read_clipboard, self.counter = type_text, read_clipboard, counter   # snippets / clipboard transforms
        self.open_url = opener or webbrowser.open
        self.popen = runner or subprocess.Popen
        self.system = system or platform.system()

    def run(self, op, arg=""):
        """Returns (ok, message)."""
        if (op, arg) not in self.allowed_fn():
            return False, f"refused: '{op}' '{arg[:40]}' is not part of your key configuration"
        try:
            if op == "url":
                u = urlparse(arg)
                if u.scheme.lower() not in SAFE_URL_SCHEMES:
                    return False, f"refused: only {', '.join(SAFE_URL_SCHEMES)} links are opened"
                self.open_url(arg)
                return True, f"opened {arg}"
            if op == "app":
                self._launch(arg)
                return True, f"started {arg}"
            if op == "file":
                self._open_file(arg)
                return True, f"opened {arg}"
            if op == "shell":
                if not self.allow_shell_fn():
                    return False, "refused: shell commands are switched off (Device tab -> 'Allow the pad to run shell commands')"
                self.popen(arg, shell=True)                # noqa: S602 - explicitly enabled by the user, argument whitelisted
                return True, f"ran: {arg}"
            if op == "clipboard":
                if not self.type_clipboard:
                    return False, "typing the clipboard is not available here"
                self.type_clipboard()
                return True, "typed the clipboard"
            if op in ("snippet", "clip"):
                if not self.type_text:
                    return False, "typing text is not available here (pip install pynput)"
                if op == "snippet":
                    ctx = {"counter": self.counter}
                    if "{clipboard}" in arg and self.read_clipboard:
                        ctx["clipboard"] = self.read_clipboard()
                    out = textops.expand(arg, ctx)
                else:
                    out = textops.transform(arg, self.read_clipboard() if self.read_clipboard else "")
                if out:
                    self.type_text(out)
                return True, f"typed {len(out)} characters"
            if op == "notify":
                if self.notify:
                    self.notify("Desk Companion", arg)
                return True, f"notified: {arg}"
        except Exception as e:                             # noqa: BLE001
            return False, f"{op} failed: {e}"
        return False, f"unknown host action '{op}'"

    def _launch(self, arg):
        if self.system == "Windows":
            os.startfile(arg)                              # noqa: S606 - whitelisted program / document
        elif self.system == "Darwin":
            self.popen(["open", "-a", arg] if not os.path.exists(arg) else ["open", arg])
        else:
            self.popen(shlex.split(arg))

    def _open_file(self, path):
        if self.system == "Windows":
            os.startfile(path)                             # noqa: S606
        elif self.system == "Darwin":
            self.popen(["open", path])
        else:
            self.popen(["xdg-open", path])
