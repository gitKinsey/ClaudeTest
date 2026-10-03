"""Engine test of the app-level glue: appearance / accent / language settings, undo-redo, plugins, global hotkey, update check, tips, multi-instance flags, auto-heal, share codes, diagnostic zip."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit, isolate    # noqa: E402

tmp = isolate("dcglue_")
import core.base as m   # noqa: E402
from desk_lib import hotkey, i18n, tokens, updates   # noqa: E402

# ---------------------------------------------------------------- command-line flags (several pads on one PC)
assert m._cli_opt("--config", ["--config", "a.json", "--port", "COM7"]) == "a.json"
assert m._cli_opt("--port", ["--config", "a.json", "--port=COM7"]) == "COM7"
assert m._cli_opt("--api-port", ["--config", "a.json"]) is None and m._cli_opt("--config", ["--config"]) is None

# ---------------------------------------------------------------- config normalisation
cfg = m.normalize_config({"appearance": "neon", "accent": "nope", "ui_scale": "huge", "language": "xx"})
assert (cfg["appearance"], cfg["accent"], cfg["ui_scale"], cfg["language"]) == ("dark", "cyan", 1.0, "en")
assert m.normalize_config({"ui_scale": 9})["ui_scale"] == 1.5 and m.normalize_config({"ui_scale": 0.1})["ui_scale"] == 0.8
assert m.normalize_config({"appearance": "system", "accent": "pink"})["accent"] == "pink"

# a saved language is applied at start
Path(os.environ["DESK_COMPANION_CONFIG"]).write_text(json.dumps({"accent": "violet", "language": "de", "ui_scale": 1.1}))
k = Kit("dcglue2_", fresh=False)
app, pump = k.e, k.pump
status = lambda: k.status    # noqa: E731
assert i18n.language() == "de" and i18n.tr("Save") == "Speichern"
assert app.cfg["accent"] == "violet" and app.cfg["accent"] in tokens.ACCENTS and app.cfg["ui_scale"] == 1.1
i18n.set_language("en")

# ---------------------------------------------------------------- appearance settings
seen = []
app.on("appearance", lambda mode: seen.append(mode))
app.on("pref", lambda key, v: seen.append((key, v)))
app.set_appearance("light"); app.pump()
assert app.cfg["appearance"] == "light" and seen[-1] == "light"
app.set_appearance("system"); assert app.cfg["appearance"] == "system"
app.set_appearance("neon"); assert app.cfg["appearance"] == "dark"
app.set_pref("accent", "green"); assert app.cfg["accent"] == "green" and seen[-1] == ("accent", "green")
app.set_pref("language", "fr", restart=True); assert app.cfg["language"] == "fr" and "next time the app starts" in status()
app.set_pref("language", "en")

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
assert "Nothing to undo" in status()
app.edit_redo()
app.edit_redo()
assert app.cfg["layers"][0]["1"]["action"] == "Paste" and app.cfg["map"] is app.cfg["layers"][0]
before3 = dict(app.cfg["map"]["3"])
app.drop_assign(3, {"slot": 1})                                   # swap through drag
assert app.cfg["map"]["3"]["action"] == "Paste" and app.cfg["map"]["1"] == before3
app.edit_undo()
assert app.cfg["map"]["1"]["action"] == "Paste" and app.cfg["map"]["3"] == before3



# ---------------------------------------------------------------- plugins
plug = app.plugins.folder
app.cfg["plugins_on"] = False
ok, msg = app.hostact.run_trusted("plugin", "hello:short")
assert not ok and "switched off" in msg, msg
app.cfg["plugins_on"] = True
app.plugins.install_example()
(plug / "card.py").write_text("NAME = 'card'\ndef run(arg, api):\n    api.set_card('PLUG', arg, 'a', 'b')\n    return 'card set'\n")
app.plugins_reload()
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
app.on("open_palette", lambda: opened.append(1))
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
assert calls[0] == m.APP_VERSION and "available" in status()
updates.check = real_check

# ---------------------------------------------------------------- tips
tipev = []
app.on("tip", lambda t: tipev.append(t))
app.cfg["tips"] = True
app.refresh_tip()
assert app.tip_id is not None and tipev[-1]
first = app.tip_id
app.dismiss_tip()
assert first in app.cfg["tips_dismissed"] and app.tip_id != first
app.cfg["tips"] = False
app.refresh_tip()
assert app.tip_id is None and tipev[-1] is None

# ---------------------------------------------------------------- auto-heal on upload (simulated pad that corrupts the first write)
assert app.dev.connected
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
pump(80, lambda: "Uploaded" in status() or "problems" in status())
txt = status()
assert "automatic re-send" in txt and "problems" not in txt, txt
app.dev.request = real_req

# ---------------------------------------------------------------- live mirror
frames = []
app.mirror_busy_snapshot(lambda img: frames.append(img), lambda: frames.append(None))
pump(120, lambda: frames)
assert frames and frames[0] is not None and frames[0].size == (240, 240), "mirror shows the pad's screen"


# ---------------------------------------------------------------- share codes
from desk_lib import sharecode   # noqa: E402
app.cfg["custom"]["My snippet"] = {"type": "text", "val": "hello"}
app.cfg["layers"][1]["3"] = {"cat": "Custom", "action": "My snippet"}
app.cfg["layers"][1]["1"] = {"cat": "Editing", "action": "Cut"}
app.cfg["gestures"]["1:2:hold"] = {"cat": "Media", "action": "Mute"}
app.cfg["gestures"]["1:10:press"] = {"cat": "Custom", "action": "My snippet"}
code = app.layer_share_code(1)
assert code.startswith("DC1:") and len(code) < 3000 and "\n" not in code
pl = sharecode.decode(code)
assert pl["map"]["1"] == {"cat": "Editing", "action": "Cut"} and pl["custom"] == {"My snippet": {"type": "text", "val": "hello"}} and "2:hold" in pl["gestures"] and "10:press" in pl["gestures"]
# into a different layer of a "fresh" config: everything arrives
orig_custom = dict(app.cfg["custom"])
del app.cfg["custom"]["My snippet"]
for k in [k for k in app.cfg["gestures"] if k.startswith("2:")]:
    del app.cfg["gestures"][k]
prev2 = dict(app.cfg["layers"][2]["1"])
msg = app.import_layer_share_code("  " + code[:40] + "\n" + code[40:] + "  ", layer=2)
assert "layer 3" in msg and app.cfg["layers"][2]["1"] == {"cat": "Editing", "action": "Cut"} and app.cfg["custom"]["My snippet"] == {"type": "text", "val": "hello"}
assert app.cfg["gestures"]["2:2:hold"] == {"cat": "Media", "action": "Mute"} and app.cfg["gestures"]["2:10:press"]["action"] == "My snippet" and "1:2:hold" in app.cfg["gestures"]
assert app.cfg["map"] is app.cfg["layers"][app.edit_layer], "the editor still works on the same dict"
app.edit_undo()
assert app.cfg["layers"][2]["1"] == prev2, "undo brings the replaced layer back"
# hostile / broken codes change nothing
before = json.dumps(app.cfg["layers"], sort_keys=True) + json.dumps(app.cfg["custom"], sort_keys=True)
import base64, zlib   # noqa: E401,E402
def forge(obj): return sharecode.PREFIX + base64.urlsafe_b64encode(zlib.compress(json.dumps(obj).encode())).decode().rstrip("=")   # noqa: E704
good = sharecode.decode(code)
bad_codes = {
    "not a code": "hello", "wrong prefix": "DC2:abcd", "bad characters": "DC1:abc$%&", "truncated": code[:len(code) // 2], "empty": "",
    "bomb": sharecode.PREFIX + base64.urlsafe_b64encode(zlib.compress(b"[" + b"0," * 200000 + b"0]")).decode().rstrip("="),
    "future version": forge(dict(good, v=2)),
    "missing keys": forge(dict(good, map={"1": good["map"]["1"]})),
    "unknown action": forge(dict(good, map=dict(good["map"], **{"4": {"cat": "Editing", "action": "Format C:"}}))),
    "invalid custom spec": forge(dict(good, custom={"Evil": {"type": "banana", "val": 1}}, map=dict(good["map"], **{"4": {"cat": "Custom", "action": "Evil"}}))),
    "custom without definition": forge(dict(good, custom={}, map=dict(good["map"], **{"4": {"cat": "Custom", "action": "Ghost"}}))),
    "bad gesture key": forge(dict(good, gestures={"99:hold": {"cat": "Media", "action": "Mute"}})),
    "gesture to nowhere": forge(dict(good, gestures={"2:hold": {"cat": "Media", "action": "Nope"}})),
    "not an object": sharecode.PREFIX + base64.urlsafe_b64encode(zlib.compress(b"[1,2]")).decode().rstrip("="),
}
for name, bad in bad_codes.items():
    try:
        app.import_layer_share_code(bad, layer=0)
        raise SystemExit(f"accepted a {name} code")
    except ValueError:
        pass
assert json.dumps(app.cfg["layers"], sort_keys=True) + json.dumps(app.cfg["custom"], sort_keys=True) == before, "a refused code changed nothing"
app.cfg["custom"] = orig_custom

# ---------------------------------------------------------------- diagnostic zip
import zipfile   # noqa: E402
app.cfg["ai"]["key"] = "sk-secret-test-key"
zp = os.path.join(tmp, "report.zip")
app.export_zip(zp)
assert pump(60, lambda: os.path.exists(zp)), "zip written"
pump(10)
z = zipfile.ZipFile(zp)
assert {"report.txt", "settings_redacted.json"} <= set(z.namelist()), z.namelist()
alltext = b"".join(z.read(n) for n in z.namelist())
assert b"sk-secret-test-key" not in alltext and b"DeskCompanion diagnostic report" in alltext and b'"layers"' in alltext
assert "Report saved" in status()

# ---------------------------------------------------------------- the card builds and shows values
assert "mA" in app.power_text()
assert "Loaded:" in app.plugins_text()
k.close()
print("ENGINE APP GLUE OK")
