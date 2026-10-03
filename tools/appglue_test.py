"""Headless test of the app-level glue for: appearance / accent / scale / language, undo-redo, plugins, global hotkey, update check, tips, multi-instance flags, auto-heal.
xvfb-run -a python3 tools/appglue_test.py"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="dcglue_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402
from desk_lib import hotkey, i18n, ui, updates   # noqa: E402

# ---------------------------------------------------------------- command-line flags (several pads on one PC)
assert m._cli_opt("--config", ["--config", "a.json", "--port", "COM7"]) == "a.json"
assert m._cli_opt("--port", ["--config", "a.json", "--port=COM7"]) == "COM7"
assert m._cli_opt("--api-port", ["--config", "a.json"]) is None and m._cli_opt("--config", ["--config"]) is None

# ---------------------------------------------------------------- config normalisation
cfg = m.normalize_config({"appearance": "neon", "accent": "nope", "ui_scale": "huge", "language": "xx"})
assert (cfg["appearance"], cfg["accent"], cfg["ui_scale"], cfg["language"]) == ("dark", "cyan", 1.0, "en")
assert m.normalize_config({"ui_scale": 9})["ui_scale"] == 1.5 and m.normalize_config({"ui_scale": 0.1})["ui_scale"] == 0.8
assert m.normalize_config({"appearance": "system", "accent": "pink"})["accent"] == "pink"

# a saved accent / language is applied at start
Path(os.environ["DESK_COMPANION_CONFIG"]).write_text(json.dumps({"accent": "violet", "language": "de", "ui_scale": 1.1}))
app = m.App()
app.update()
assert ui.ACCENT == ui.ACCENTS["violet"][0] and m.ACCENT == ui.ACCENT, "accent colour applied"
assert i18n.language() == "de" and i18n.tr("Save") == "Speichern"
theme = json.loads((Path(tempfile.gettempdir()) / "deskcompanion_theme.json").read_text())
assert theme["CTkButton"]["fg_color"] == list(ui.ACCENTS["violet"][1]), "theme file carries the accent"


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


# language: widgets created with known English text are translated
found = []


def walk(w):
    for c in w.winfo_children():
        try:
            found.append(c.cget("text"))
        except Exception:                                      # noqa: BLE001
            pass
        walk(c)


walk(app)
assert "Speichern" in found or "Gerät" in found or "Übersicht" in found, found[:30]
i18n.set_language("en")

# ---------------------------------------------------------------- appearance: dark / light / system
app.set_appearance("light")
assert app.cfg["appearance"] == "light" and app.theme_var.get() is True
app.set_appearance("dark")
assert app.theme_var.get() is False
app.set_appearance("system")
assert app.cfg["appearance"] == "system"
app.set_appearance("dark")
app.app_card.set_restart("accent", "green")
assert app.cfg["accent"] == "green"
app.app_card.set_language("Français")
assert app.cfg["language"] == "fr"
app.app_card.set_language("English")

# ---------------------------------------------------------------- undo / redo of key assignments
lm = app.cfg["layers"][0]
orig = dict(lm["1"])
app.drop_assign(1, {"cat": "Editing", "action": "Cut"})
app.drop_assign(1, {"cat": "Editing", "action": "Paste"})
assert lm["1"]["action"] == "Paste"
app.edit_undo()
assert app.cfg["layers"][0]["1"]["action"] == "Cut", app.cfg["layers"][0]["1"]
app.edit_undo()
assert app.cfg["layers"][0]["1"] == orig
app.edit_undo()
assert "Nothing to undo" in app.status.cget("text")
app.edit_redo()
app.edit_redo()
assert app.cfg["layers"][0]["1"]["action"] == "Paste" and app.cfg["map"] is app.cfg["layers"][0]
before3 = dict(app.cfg["map"]["3"])
app.drop_assign(3, {"slot": 1})                                   # swap through drag
assert app.cfg["map"]["3"]["action"] == "Paste" and app.cfg["map"]["1"] == before3
app.edit_undo()
assert app.cfg["map"]["1"]["action"] == "Paste" and app.cfg["map"]["3"] == before3


class Ev:                                                         # Ctrl+Z inside a text field must not undo key assignments
    class widget:                                                 # noqa: N801
        @staticmethod
        def winfo_class():
            return "Entry"


assert app._undo_key(Ev, False) is None

# ---------------------------------------------------------------- plugins
plug = app.plugins.folder
app.cfg["plugins_on"] = False
ok, msg = app.hostact.run_trusted("plugin", "hello:short")
assert not ok and "switched off" in msg, msg
app.cfg["plugins_on"] = True
app.plugins.install_example()
(plug / "card.py").write_text("NAME = 'card'\ndef run(arg, api):\n    api.set_card('PLUG', arg, 'a', 'b')\n    return 'card set'\n")
app.app_card.pl_reload()
assert sorted(app.plugins.mods) == ["card", "hello"], (app.plugins.mods, app.plugins.errors)
typed = []
app._type_text = typed.append
ok, msg = app.hostact.run_trusted("plugin", "hello:short")
assert ok and typed and len(typed[0]) == 5, (ok, msg, typed)
ok, msg = app.hostact.run_trusted("plugin", "card:Hello")
assert ok and app.cfg["info"]["c_t"] == "Hello" and app.cfg["info"]["c_label"] == "PLUG"
ok, msg = app.hostact.run_trusted("plugin", "ghost")
assert not ok and "not loaded" in msg
# the pad can only trigger a plugin that is on one of the user's keys
ok, msg = app.hostact.run("plugin", "card:EVIL")
assert not ok and "refused" in msg

# ---------------------------------------------------------------- global hotkey (with a fake backend)
made = []


class FakeHK:
    def __init__(self, mapping):
        self.mapping, self.started = mapping, False
        made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


orig_factory = hotkey._default_factory
hotkey._default_factory = FakeHK
assert app.hotkey_set(True, "ctrl+alt+k") == "" and made[-1].started
opened = []
app.open_palette = lambda: opened.append(1)
made[-1].mapping["<ctrl>+<alt>+k"]()
pump(5)
assert opened == [1], "hotkey opens the palette"
assert "modifier" in app.hotkey_set(True, "k") or "use a modifier" in app.hotkey_set(True, "k")
assert app.hotkey_set(False, "") == "" and not made[-1].started
hotkey._default_factory = orig_factory

# ---------------------------------------------------------------- update check
calls = []
real_check = updates.check
updates.check = lambda cur, **kw: calls.append(cur) or {"message": "version v9.0.0 is available", "newer": True, "url": "https://x", "latest": "v9.0.0", "notes": "", "current": cur}
app.notify = lambda *a, **k: calls.append(a)
app._startup_update_check()
pump(10, lambda: len(calls) >= 3)
assert calls[0] == m.APP_VERSION and "available" in app.status.cget("text")
updates.check = real_check

# ---------------------------------------------------------------- tips
app.cfg["tips"] = True
app.refresh_tip()
assert app._tip_id is not None and app.tip_card.winfo_manager() == "pack"
first = app._tip_id
app.dismiss_tip()
assert first in app.cfg["tips_dismissed"] and app._tip_id != first
app.cfg["tips"] = False
app.refresh_tip()
assert app._tip_id is None and app.tip_card.winfo_manager() == ""

# ---------------------------------------------------------------- auto-heal on upload (simulated pad that corrupts the first write)
app.toggle_simulate()
assert pump(60, lambda: app.dev.connected)
sim = app.dev.impl if hasattr(app.dev, "impl") else None
real_req = app.dev.request
state = {"bad": 1}


def flaky(msg, timeout=2.0):
    r = real_req(msg, timeout)
    if msg.get("cmd") == "remap" and state["bad"] and msg.get("key") == 1 and not msg.get("layer"):
        state["bad"] = 0
        real_req(dict(msg, type="text", val="corrupted"), timeout)          # the pad ends up with different data
    return r


app.dev.request = flaky
app.upload_all()
pump(80, lambda: "Uploaded" in app.status.cget("text") or "problems" in app.status.cget("text"))
txt = app.status.cget("text")
assert "automatic re-send" in txt and "problems" not in txt, txt
app.dev.request = real_req

# ---------------------------------------------------------------- live mirror
app.devtab.mirror_var.set(True)
app.devtab.mirror_toggle()
pump(120, lambda: getattr(app.devtab, "_snap_img", None) is not None)
assert getattr(app.devtab, "_snap_img", None) is not None, "mirror shows the pad's screen"
app.devtab.mirror_var.set(False)

# ---------------------------------------------------------------- the card builds and shows values
assert "mA" in app.app_card.power_lbl.cget("text")
app.app_card.pl_refresh()
assert "Loaded:" in app.app_card.pl_note.cget("text")
print("APP GLUE OK")
os._exit(0)
