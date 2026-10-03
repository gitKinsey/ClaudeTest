"""Engine test: computer / mouse / layer actions, the recorder, firmware status, Wi-Fi gating, recovery, backup / restore, macro sharing."""
import json
import os
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402
from core.engine import NullFrontend    # noqa: E402


class FE(NullFrontend):
    save_path = ""
    open_path = ""

    def ask_save(self, title="", initial="", filters="", suffix=""):
        return self.save_path

    def ask_open(self, title="", filters=""):
        return self.open_path


fe = FE()
k = Kit("dcfeat_", frontend=fe)
import core.base as m    # noqa: E402
app, sim, pump = k.e, k.sim, k.pump
tmp = k.tmp
status = lambda: k.status    # noqa: E731
app.hostact.open_url = lambda u: None

# ---------------------------------------------------------------- computer / mouse / layer actions
def act(kind, arg="", tf=""):
    return app.action_spec(kind, arg, tf)
assert m.describe_spec(("host", {"op": "clip", "arg": "snake"})) == "clipboard: snake_case" and m.describe_spec(("host", {"op": "snippet", "arg": "Hi"})) == "type snippet: Hi"
assert m.spec_ok({"type": "host", "val": {"op": "snippet", "arg": "x"}}) and not m.spec_ok({"type": "host", "val": {"op": "snippet", "arg": ""}})
assert app._next_counter("t") == 1 and app._next_counter("t") == 2 and app.cfg["counters"]["t"] == 2
for bad in (("Open website", ""), ("Mouse click", "banana"), ("Mouse scroll", "0"), ("Mouse scroll", "99"), ("Mouse scroll", "x"), ("Switch layer", "7")):
    try:
        act(*bad)
        raise SystemExit(f"accepted bad action {bad}")
    except ValueError:
        pass
try:
    act("Run shell command", "ls")
    raise SystemExit("shell accepted while switched off")
except ValueError as e:
    assert "switched off" in str(e)
app.cfg["allow_shell"] = True
assert act("Run shell command", "echo hi") == ("host", {"op": "shell", "arg": "echo hi"})
app.cfg["allow_shell"] = False
for slot, (kind, arg) in enumerate((("Open website", "example.org"), ("Mouse scroll", "3"), ("Switch layer", "3"), ("Mouse double click", ""), ("Type the clipboard", "")), start=1):
    app.set_target_slot(slot)
    s = act(kind, arg)
    app.assign_spec(s, m.describe_spec(s)[:40])
    app.upload_slots([slot])
assert pump(80, lambda: all(sim.layers[0].get(i, {}).get("type") in ("host", "mouse", "layer") for i in range(1, 6))), sim.layers[0]
assert sim.layers[0][3] == {"type": "layer", "val": 2} and sim.layers[0][2] == {"type": "mouse", "val": {"wheel": 3}}
app.seq_clear()
for kind, arg in (("Open website", "example.org"), ("Mouse click", ""), ("Switch layer", "next")):
    app.seq_add(app.action_step(act(kind, arg)))
app.seq_add_delay("200")
lines = app.seq_lines()
assert lines[0].startswith("COMPUTER") and lines[1].startswith("MOUSE") and lines[2].startswith("LAYER"), lines
app.set_target_slot(5)
app.assign_spec(app.seq_spec(), "Mixed")
app.upload_slots([5])
assert pump(60, lambda: sim.layers[0].get(5, {}).get("type") == "macro" and len(sim.layers[0][5]["val"]) == 4), sim.layers[0].get(5)
print("computer / mouse / layer actions OK")

# ---------------------------------------------------------------- recorder with a fake listener
class FakeListener:
    def __init__(self, on_press, on_release):
        self.press, self.release, self.started, self.stopped = on_press, on_release, False, False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


holder = {}


def factory(on_press, on_release):
    holder["l"] = FakeListener(on_press, on_release)
    return holder["l"]


app.seq_clear()
rec_events = []
app.on("recording", lambda on: rec_events.append(on))
app.rec_toggle(listener_factory=factory)
assert holder["l"].started and app.recording and rec_events == [True]
for n in ("ctrl_l", "c"):
    holder["l"].press(n)
holder["l"].release("c")
holder["l"].release("ctrl_l")
for n in "hi":
    holder["l"].press(n)
app.rec_toggle()
assert holder["l"].stopped and not app.recording and rec_events == [True, False]
assert app.macro_steps == [{"combo": ["CTRL", "c"]}, {"text": "hi"}] or (len(app.macro_steps) == 3 and app.macro_steps[1].get("delay") is not None), app.macro_steps


class FakeMouse:
    def __init__(self, move, button, scroll):
        self.move, self.button, self.scroll, self.started, self.stopped = move, button, scroll, False, False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


mh = {}


def mfactory(move, button, scroll):
    mh["l"] = FakeMouse(move, button, scroll)
    return mh["l"]


app.seq_clear()
app.rec_toggle(listener_factory=factory, mouse_factory=mfactory, with_mouse=True)
assert mh["l"].started
mh["l"].move(10, 10); mh["l"].move(40, 10); mh["l"].button("Button.left", True); time.sleep(0.02); mh["l"].button("Button.left", False); mh["l"].scroll(2)
app.rec_toggle()
assert mh["l"].stopped and app.macro_steps[0] == {"mouse": {"move": [30, 0]}} and {"mouse": {"btn": "left", "act": "click"}} in app.macro_steps and {"mouse": {"wheel": 2}} in app.macro_steps, app.macro_steps
print("recorder OK")

# ---------------------------------------------------------------- firmware status
fw_events = []
app.on("fw_status", lambda kind, text, banner: fw_events.append((kind, banner)))
assert app.fw_status()[0] == "ok", app.fw_status()
app.dev.info["fw"] = "1.1.0"; app.refresh_fw_status()
assert app.fw_status()[0] == "old" and fw_events[-1][0] == "old" and "older" in fw_events[-1][1]
app.dev.info["fw"] = "9.0.0"; app.refresh_fw_status(); assert app.fw_status()[0] == "newer"
app.dev.info["fw"] = "weird"; app.refresh_fw_status(); assert app.fw_status()[0] == "unknown"
app.dev.info["fw"] = m.FW_BUNDLED; app.refresh_fw_status()
assert app.fw_status()[0] == "ok" and fw_events[-1] == ("ok", None)
print("firmware status OK")

# ---------------------------------------------------------------- Wi-Fi is an optional build extra
assert "wifi" not in app.dev.info["caps"] and not app.wifi_supported() and "cable-only" in app.wifi_note()
try:
    app.dev.request({"cmd": "ota", "val": True})
    raise SystemExit("OTA accepted by a cable-only pad")
except m.DeviceError as e:
    assert "wifi_disabled" in str(e) or "cable-only" in str(e), e
app.dev.info["caps"] = app.dev.info["caps"] + ["wifi", "ota"]
assert app.wifi_supported() and "cable-only" not in app.wifi_note()
app.dev.info["caps"] = [c for c in app.dev.info["caps"] if c not in ("wifi", "ota")]
del app.dev.info["caps"]
assert app.wifi_supported(), "firmware 1.1 (no caps) always had Wi-Fi"
app.dev.info["caps"] = ["layers", "mouse", "host", "info", "gifslots", "factory"]
print("wifi gating OK")

# ---------------------------------------------------------------- recovery
sim.crashes = 3
app.recovery("safe_retry"); assert pump(40, lambda: sim.crashes == 0)
app.recovery("nodisp"); assert pump(40, lambda: sim.nodisp is True); app.recovery("disp"); assert pump(40, lambda: sim.nodisp is False)
app.upload_all(); assert pump(200, lambda: "Uploaded to the pad" in status())
assert all(len(sim.layers[n]) == 7 for n in range(3))
app.recovery("keys"); assert pump(40, lambda: all(not sim.layers[n] for n in range(3)))
assert pump(40, lambda: all(not app.cfg["pushed_layers"][n] for n in range(3))), "after a pad reset nothing is known to be on it"
sim.gifs = {0: 100, 1: 200}; app.recovery("gifs"); assert pump(40, lambda: sim.gifs == {0: 60000}), sim.gifs
texts = []
app.on("safe_text", lambda t, kind: texts.append((t, kind)))
app.recovery_check(); assert pump(40, lambda: texts and "normal mode" in texts[-1][0]), texts
print("recovery OK")

# ---------------------------------------------------------------- backup / restore
(Path(tmp) / "cat.gif").write_bytes(b"GIF89a" + bytes(30)); m.lib_add(Path(tmp) / "cat.gif")
app.upload_all(); assert pump(200, lambda: "Uploaded to the pad" in status())
app.cfg["custom"]["Docs"] = {"type": "host", "val": {"op": "url", "arg": "https://example.org"}}
app.cfg["profiles"].append({"name": "x", "match": "code", "kind": "process", "layer": 2, "enabled": True})
app.drop_assign(1, {"cat": "Custom", "action": "Docs"})
path = str(Path(tmp) / "backup.zip")
fe.save_path = path
app.backup_export(); assert pump(60, lambda: Path(path).exists())
z = zipfile.ZipFile(path); names = z.namelist()
assert {"manifest.json", "config.json", "pad.json", "gifs/cat.gif"} <= set(names), names
padj = json.loads(z.read("pad.json"))
assert len(padj["layers"]) == 3 and len(padj["layers"][0]) == 7 and padj["layers"][1][0] == {"type": "media", "val": "PREV"}, padj["layers"][1][0]
app.cfg["custom"].clear(); app.cfg["profiles"].clear(); app.cfg["layers"][0]["1"] = {"cat": "Editing", "action": "Cut"}
for f in m.lib_list():
    f.unlink()
fe.open_path = path
app.backup_import(); pump(20)
assert "Docs" in app.cfg["custom"] and len(app.cfg["profiles"]) == 1 and app.cfg["layers"][0]["1"] == {"cat": "Custom", "action": "Docs"}
assert [p.name for p in m.lib_list()] == ["cat.gif"], m.lib_list()
assert app.cfg["map"] is app.cfg["layers"][0] and app.edit_layer == 0 and all(not p for p in app.cfg["pushed_layers"])
bad = Path(tmp) / "bad.zip"; bad.write_bytes(b"nope"); fe.open_path = str(bad)
before = json.dumps(app.cfg["layers"]); app.backup_import(); assert "not a usable backup" in status() and json.dumps(app.cfg["layers"]) == before
mp = str(Path(tmp) / "macros.json"); fe.save_path = mp
app.macro_export(); data = json.loads(Path(mp).read_text()); assert data["macros"]["Docs"]["type"] == "host"
data["macros"]["Evil"] = {"type": "host", "val": {"op": "format-c", "arg": "x"}}; data["macros"]["Ok2"] = {"type": "combo", "val": ["CTRL", "q"]}
Path(mp).write_text(json.dumps(data)); app.cfg["custom"].clear(); fe.open_path = mp
app.macro_import(); assert {"Docs", "Ok2"} <= set(app.cfg["custom"]) and "Evil" not in app.cfg["custom"] and "1 invalid" in status(), (app.cfg["custom"].keys(), status())
Path(mp).write_text("{}"); app.macro_import(); assert "not a Desk Companion macro export" in status()
# automatic backups
app.cfg["custom"]["Auto"] = {"type": "combo", "val": ["CTRL", "z"]}
assert app.autobackup_now() is not None and app.autobackup_list()
newest = app.autobackup_list()[0][0]
app.cfg["custom"].clear()
assert app.autobackup_restore(newest) and "Auto" in app.cfg["custom"]
print("backup / restore / sharing OK")

# ---------------------------------------------------------------- palette commands exist for the engine paths they call
assert callable(app.edit_undo) and callable(app.edit_redo) and callable(app.verify_pad_keys) and callable(app.recovery_check)
app.set_edit_layer(2)
assert app.edit_layer == 2
k.close()
print("ALL ENGINE FEATURE TESTS PASSED")
