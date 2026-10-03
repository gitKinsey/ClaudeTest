"""Plugins: small Python files in the 'plugins' folder that add key actions (host op "plugin", argument "name:argument").

A plugin file defines   NAME = "hello"   and   def run(arg, api): ...  returning a short message.
`api` offers  api.type_text(s)  api.notify(title, text)  api.clipboard()  api.set_card(label, title, a, b)  (those that the app provides).
SAFETY: a plugin is ordinary Python code that runs with your rights, exactly like a script you download. Plugins load only when
you switch 'Plugins' on, only from the folder you can see, and the pad can only trigger one that is on one of YOUR keys."""
import importlib.util
import re
from pathlib import Path

NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,23}")

EXAMPLE = '''"""Example plugin: types the current time. Put it on a key as  Computer action -> plugin -> hello:short"""
import time

NAME = "hello"


def run(arg, api):
    text = time.strftime("%H:%M" if arg == "short" else "%Y-%m-%d %H:%M:%S")
    api.type_text(text)
    return "typed " + text
'''


class PluginHost:
    def __init__(self, folder, api=None):
        self.folder, self.api = Path(folder), api
        self.mods, self.errors = {}, {}

    def load(self):
        """(Re)load every *.py in the folder. Returns the list of loaded names; problems land in self.errors."""
        self.mods, self.errors = {}, {}
        if not self.folder.is_dir():
            return []
        for f in sorted(self.folder.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location("dc_plugin_" + f.stem, f)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                name = str(getattr(mod, "NAME", f.stem))
                if not NAME_RE.fullmatch(name):
                    raise ValueError(f"NAME '{name}' must be lower-case letters, digits and _")
                if not callable(getattr(mod, "run", None)):
                    raise ValueError("no run(arg, api) function")
                if name in self.mods:
                    raise ValueError(f"name '{name}' is already used by another plugin")
                self.mods[name] = mod
            except Exception as e:                                       # noqa: BLE001
                self.errors[f.name] = f"{type(e).__name__}: {e}"
        return sorted(self.mods)

    def run(self, spec):
        """spec = 'name' or 'name:argument'."""
        name, _, arg = str(spec).partition(":")
        mod = self.mods.get(name.strip())
        if mod is None:
            raise ValueError(f"plugin '{name.strip()}' is not loaded (Device page -> Plugins)")
        out = mod.run(arg, self.api)
        return str(out if out is not None else f"plugin {name.strip()} ran")[:200]

    def install_example(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self.folder / "hello.py"
        if not p.exists():
            p.write_text(EXAMPLE, encoding="utf-8")
        return p
