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

from desk_lib import netactions, sysactions, textops

SAFE_URL_SCHEMES = ("http", "https", "mailto")
NEW_OPS = ("appvol", "dnd", "audio_out", "mic", "shot", "translate", "ai", "webhook", "layout", "cliphist")


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
                 type_text=None, read_clipboard=None, counter=None, script_runner=None, sysact=None, layouts=None, layout_store=None, cliphist=None,
                 cfg_get=None, focus_program=None, net_fetch=None):
        self.allowed_fn, self.allow_shell_fn = allowed_fn, allow_shell_fn
        self.type_clipboard, self.notify = type_clipboard, notify
        self.type_text, self.read_clipboard, self.counter = type_text, read_clipboard, counter   # snippets / clipboard transforms
        self.script_runner = script_runner                                                        # callable(name) -> message, for op "script"
        self.sysact, self.layouts, self.layout_store, self.cliphist = sysact, layouts, layout_store, cliphist   # OS actions, window layouts, clipboard history
        self.cfg_get = cfg_get or (lambda key, default=None: default)                              # app settings (AI key, screenshot folder)
        self.focus_program, self.net_fetch = focus_program or (lambda: ""), net_fetch
        self.open_url = opener or webbrowser.open
        self.popen = runner or subprocess.Popen
        self.system = system or platform.system()

    def run(self, op, arg=""):
        """Returns (ok, message)."""
        if (op, arg) not in self.allowed_fn():
            return False, f"refused: '{op}' '{arg[:40]}' is not part of your key configuration"
        return self.run_trusted(op, arg)

    def run_trusted(self, op, arg=""):
        """Like run() but without the whitelist: for things the user's own scripts do. The URL-scheme and shell-switch checks stay."""
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
            if op in NEW_OPS:
                return True, self._new_op(op, arg)
            if op == "script":
                if not self.script_runner:
                    return False, "scripts are not available here"
                return True, self.script_runner(arg)
            if op == "notify":
                if self.notify:
                    self.notify("Desk Companion", arg)
                return True, f"notified: {arg}"
        except Exception as e:                             # noqa: BLE001
            return False, f"{op} failed: {e}"
        return False, f"unknown host action '{op}'"

    def _new_op(self, op, arg):
        sa = self.sysact or sysactions.SysActions()
        if op == "appvol":
            prog, _, amt = arg.rpartition(":") if ":" in arg else ("", "", arg)
            return sa.app_volume(prog.strip() or self.focus_program(), sysactions.parse_volume_arg(amt))
        if op == "dnd":
            return sa.dnd(arg.strip().lower())
        if op == "audio_out":
            return sa.audio_output(arg.strip())
        if op == "mic":
            return sa.microphone(arg.strip().lower())
        if op == "shot":
            folder = "" if arg.strip().lower() in ("", "default", ".") else arg.strip()
            return "screenshot saved: " + str(sa.screenshot(folder or self.cfg_get("shot_dir") or None))
        if op == "translate":
            if not self.type_text:
                raise ValueError("typing text is not available here (pip install pynput)")
            out = netactions.translate(self.read_clipboard() if self.read_clipboard else "", arg.strip(), self.net_fetch)
            self.type_text(out)
            return f"translated and typed {len(out)} characters"
        if op == "ai":
            if not self.type_text:
                raise ValueError("typing text is not available here (pip install pynput)")
            ai = self.cfg_get("ai") or {}
            prompt = textops.expand(arg, {"clipboard": self.read_clipboard() if self.read_clipboard else "", "counter": self.counter})
            out = netactions.ask_ai(prompt, ai.get("key", ""), ai.get("model") or "claude-haiku-4-5-20251001", self.net_fetch)
            self.type_text(out)
            return f"AI answer typed ({len(out)} characters)"
        if op == "webhook":
            return netactions.webhook(textops.expand(arg, {"clipboard": self.read_clipboard() if "{clipboard}" in arg and self.read_clipboard else "", "counter": self.counter}), self.net_fetch)
        if op == "layout":
            lay = self.layouts
            store = self.layout_store() if callable(self.layout_store) else self.layout_store
            if lay is None or store is None:
                raise ValueError("window layouts are not available here")
            word, _, name = arg.partition(" ")
            if word.lower() == "save" and name.strip():
                return f"layout '{name.strip()}' saved ({lay.save(store, name.strip())} windows)"
            return f"layout '{arg.strip()}' restored ({lay.restore(store, arg.strip())} windows moved)"
        if op == "cliphist":
            if not self.cliphist or not self.type_text or not self.cfg_get("cliphist_on"):
                raise ValueError("clipboard history is switched off (Device page -> Clipboard history)")
            if not arg.strip().isdigit():
                raise ValueError("cliphist needs a number: 1 = latest copy, 2 = the one before ...")
            self.type_text(self.cliphist.get(int(arg)))
            return "typed from the clipboard history"
        raise ValueError(f"unknown action '{op}'")

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
