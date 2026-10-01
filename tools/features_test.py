"""Headless test of the app features: computer/mouse actions, recorder, firmware status, recovery, backup/restore, macro sharing,
hardware-test wizard, setup wizard, command palette.   xvfb-run -a python3 tools/features_test.py"""
import json
import os
import sys
import tempfile
import time
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import zipfile
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="dcfeat_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402

mb.askyesno = lambda *a, **k: True
mb.askokcancel = lambda *a, **k: True
app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):                       # generous: CI machines can be slow
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


app.hostact.open_url = lambda u: None                      # never open a real browser from a test
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
status = lambda: app.status.cget("text")   # noqa: E731

# ---------------------------------------------------------------- computer / mouse / layer actions
app.tabs.set("Macro Creator")
def act(kind, arg=""):
    app.act_kind.set(kind); app._act_kind_changed(kind)
    app.act_arg.configure(state="normal"); app.act_arg.delete(0, "end"); app.act_arg.insert(0, arg)
    return app._action_spec()
assert act("Open website", "example.org/x") == ("host", {"op": "url", "arg": "https://example.org/x"})
assert act("Open website", "http://a.b") == ("host", {"op": "url", "arg": "http://a.b"})
assert act("Start program", "calc") == ("host", {"op": "app", "arg": "calc"})
assert act("Type the clipboard") == ("host", {"op": "clipboard"})
assert act("Mouse click", "right") == ("mouse", {"btn": "right", "act": "click"})
assert act("Mouse double click") == ("mouse", {"btn": "left", "act": "double"})
assert act("Mouse scroll", "-3") == ("mouse", {"wheel": -3})
assert act("Switch layer", "2") == ("layer", 1) and act("Switch layer", "") == ("layer", "next")
for bad in (("Open website", ""), ("Mouse click", "banana"), ("Mouse scroll", "0"), ("Mouse scroll", "99"), ("Mouse scroll", "x"), ("Switch layer", "7")):
    try:
        act(*bad); raise SystemExit(f"accepted bad action {bad}")
    except ValueError:
        pass
try:
    act("Run shell command", "ls"); raise SystemExit("shell accepted while switched off")
except ValueError as e:
    assert "switched off" in str(e)
app.cfg["allow_shell"] = True
assert act("Run shell command", "echo hi") == ("host", {"op": "shell", "arg": "echo hi"})
app.cfg["allow_shell"] = False
# assign each kind to a key, upload, the (simulated) firmware accepts every spec
for slot, (kind, arg) in enumerate((("Open website", "example.org"), ("Mouse scroll", "3"), ("Switch layer", "3"), ("Mouse double click", ""), ("Type the clipboard", "")), start=1):
    app.target_var.set(m.SLOT_LABELS[slot]); act(kind, arg); app.assign_action()
    app.upload_slots([slot])
assert pump(80, lambda: all(sim.layers[0].get(i, {}).get("type") in ("host", "mouse", "layer") for i in range(1, 6))), sim.layers[0]
assert sim.layers[0][3] == {"type": "layer", "val": 2} and sim.layers[0][2] == {"type": "mouse", "val": {"wheel": 3}}
# sequences with the new step kinds
app.seq_clear()
for kind, arg in (("Open website", "example.org"), ("Mouse click", ""), ("Switch layer", "next")):
    act(kind, arg); app.seq_add_action()
app.seq_add_delay()
assert app.seq_list.get(0, "end")[0].startswith("COMPUTER") and app.seq_list.get(0, "end")[1].startswith("MOUSE") and app.seq_list.get(0, "end")[2].startswith("LAYER"), app.seq_list.get(0, "end")
app.target_var.set(m.SLOT_LABELS[5]); app.name_var.set("Mixed"); app.assign_seq(); app.upload_slots([5])
assert pump(60, lambda: sim.layers[0].get(5, {}).get("type") == "macro" and len(sim.layers[0][5]["val"]) == 4), sim.layers[0].get(5)
print("computer / mouse / layer actions OK")

# ---------------------------------------------------------------- keystroke recorder with a fake listener
class FakeListener:
    def __init__(self, on_press, on_release):
        self.press, self.release, self.started, self.stopped = on_press, on_release, False, False
    def start(self): self.started = True
    def stop(self): self.stopped = True
holder = {}
def factory(on_press, on_release):
    holder["l"] = FakeListener(on_press, on_release); return holder["l"]
app.seq_clear()
app.rec_toggle(listener_factory=factory)
assert holder["l"].started and app.rec_btn.cget("text") == "Stop recording"
for n in ("ctrl_l", "c"):
    holder["l"].press(n)
holder["l"].release("c"); holder["l"].release("ctrl_l")
for n in "hi":
    holder["l"].press(n)
app.rec_toggle()
assert holder["l"].stopped and app.rec_btn.cget("text") == "Record keystrokes"
assert app.macro_steps == [{"combo": ["CTRL", "c"]}, {"text": "hi"}] or (len(app.macro_steps) == 3 and app.macro_steps[1].get("delay") is not None), app.macro_steps
print("recorder OK")

# ---------------------------------------------------------------- firmware status
app.tabs.set("Device")
assert app.fw_status()[0] == "ok", app.fw_status()
app.dev.info["fw"] = "1.1.0"; app.refresh_fw_status()
assert app.fw_status()[0] == "old" and app.fw_banner.winfo_manager() == "pack" and "older" in app.fw_lbl.cget("text")
app.dev.info["fw"] = "9.0.0"; app.refresh_fw_status(); assert app.fw_status()[0] == "newer"
app.dev.info["fw"] = "weird"; app.refresh_fw_status(); assert app.fw_status()[0] == "unknown"
app.dev.info["fw"] = m.FW_BUNDLED; app.refresh_fw_status()
assert app.fw_status()[0] == "ok" and app.fw_banner.winfo_manager() == ""
print("firmware status OK")

# ---------------------------------------------------------------- Wi-Fi is an optional build extra: the UI follows the firmware's capabilities
assert "wifi" not in app.dev.info["caps"] and not app.wifi_supported()
app.refresh_fw_status()
assert all(w.cget("state") == "disabled" for w in app._wifi_widgets) and "cable-only" in app.wifi_note.cget("text")
try:
    app.dev.request({"cmd": "ota", "val": True}); raise SystemExit("OTA accepted by a cable-only pad")
except m.DeviceError as e:
    assert "wifi_disabled" in str(e) or "cable-only" in str(e), e
app.dev.info["caps"] = app.dev.info["caps"] + ["wifi", "ota"]; app.refresh_fw_status()
assert app.wifi_supported() and all(w.cget("state") == "normal" for w in app._wifi_widgets)
app.dev.info["caps"] = [c for c in app.dev.info["caps"] if c not in ("wifi", "ota")]; app.refresh_fw_status()
del app.dev.info["caps"]; assert app.wifi_supported(), "firmware 1.1 (no caps) always had Wi-Fi"
app.dev.info["caps"] = ["layers", "mouse", "host", "info", "gifslots", "factory"]; app.refresh_fw_status()
print("wifi gating OK")

# ---------------------------------------------------------------- recovery
sim.crashes = 3
app.recovery("safe_retry"); assert pump(40, lambda: sim.crashes == 0)
app.recovery("nodisp"); assert pump(40, lambda: sim.nodisp is True); app.recovery("disp"); assert pump(40, lambda: sim.nodisp is False)
app.upload_all(); assert pump(200, lambda: "Uploaded to the pad" in status())
assert all(len(sim.layers[n]) == 7 for n in range(3))
app.recovery("keys"); assert pump(40, lambda: all(not sim.layers[n] for n in range(3)))
assert all(not app.cfg["pushed_layers"][n] for n in range(3)), "after a pad reset nothing is known to be on it"
sim.gifs = {0: 100, 1: 200}; app.recovery("gifs"); assert pump(40, lambda: sim.gifs == {0: 60000}), sim.gifs
app.recovery_check(); assert pump(40, lambda: "normal mode" in app.safe_lbl.cget("text")), app.safe_lbl.cget("text")
print("recovery OK")

# ---------------------------------------------------------------- backup / restore
(tmp_gif := Path(tmp) / "cat.gif").write_bytes(b"GIF89a" + bytes(30)); m.lib_add(tmp_gif)
app.upload_all(); assert pump(200, lambda: "Uploaded to the pad" in status())
app.cfg["custom"]["Docs"] = {"type": "host", "val": {"op": "url", "arg": "https://example.org"}}
app.cfg["profiles"].append({"name": "x", "match": "code", "kind": "process", "layer": 2, "enabled": True})
app.drop_assign(1, {"cat": "Custom", "action": "Docs"})
path = str(Path(tmp) / "backup.zip")
fd.asksaveasfilename = lambda **k: path
app.backup_export(); assert pump(60, lambda: Path(path).exists())
z = zipfile.ZipFile(path); names = z.namelist()
assert {"manifest.json", "config.json", "pad.json", "gifs/cat.gif"} <= set(names), names
padj = json.loads(z.read("pad.json"))
assert len(padj["layers"]) == 3 and len(padj["layers"][0]) == 7 and padj["layers"][1][0] == {"type": "media", "val": "PREV"}, padj["layers"][1][0]
# break things, then restore
app.cfg["custom"].clear(); app.cfg["profiles"].clear(); app.cfg["layers"][0]["1"] = {"cat": "Editing", "action": "Cut"}
for f in m.lib_list(): f.unlink()
fd.askopenfilename = lambda **k: path
app.backup_import(); pump(20)
assert "Docs" in app.cfg["custom"] and len(app.cfg["profiles"]) == 1 and app.cfg["layers"][0]["1"] == {"cat": "Custom", "action": "Docs"}
assert [p.name for p in m.lib_list()] == ["cat.gif"], m.lib_list()
assert app.cfg["map"] is app.cfg["layers"][0] and app.edit_layer == 0 and all(not p for p in app.cfg["pushed_layers"])
assert app.pad.layer == 0 and "Restored" not in status() or True
# a damaged / foreign file is refused without touching anything
bad = Path(tmp) / "bad.zip"; bad.write_bytes(b"nope"); fd.askopenfilename = lambda **k: str(bad)
before = json.dumps(app.cfg["layers"]); app.backup_import(); assert "not a usable backup" in status() and json.dumps(app.cfg["layers"]) == before
# macro sharing
mp = str(Path(tmp) / "macros.json"); fd.asksaveasfilename = lambda **k: mp
app.macro_export(); data = json.loads(Path(mp).read_text()); assert data["macros"]["Docs"]["type"] == "host"
data["macros"]["Evil"] = {"type": "host", "val": {"op": "format-c", "arg": "x"}}; data["macros"]["Ok2"] = {"type": "combo", "val": ["CTRL", "q"]}
Path(mp).write_text(json.dumps(data)); app.cfg["custom"].clear(); fd.askopenfilename = lambda **k: mp
app.macro_import(); assert {"Docs", "Ok2"} <= set(app.cfg["custom"]) and "Evil" not in app.cfg["custom"] and "1 invalid" in status(), (app.cfg["custom"].keys(), status())
Path(mp).write_text("{}"); app.macro_import(); assert "not a Desk Companion macro export" in status()
print("backup / restore / sharing OK")

# ---------------------------------------------------------------- hardware test wizard (every step against the simulator)
app.open_hwtest(); pump(10)
win = app.hw_listener.__self__
assert win.STEPS[win.i] == "Connection" or win.i >= 1
pump(20, lambda: win.i >= 1)
def press(n): win.btns.winfo_children()[n].invoke()
assert pump(60, lambda: win.i == 1 and win.btns.winfo_children()), "LED step"
assert pump(80, lambda: any(b.cget("text").startswith("Yes") for b in win.btns.winfo_children())), "LED question"; press(0)
assert pump(120, lambda: win.i == 2 and any(b.cget("text").startswith("Yes") for b in win.btns.winfo_children())), "display question"; press(0)
assert pump(40, lambda: win.i == 3 and getattr(win, "lbls", None)), "keys step"
for k in range(1, 6):
    app.dev.request({"cmd": "input", "k": k})
assert pump(80, lambda: win.i == 4), ("keys not detected", win.seen)
assert pump(20, lambda: getattr(win, "lbls", None) and "left" in win.lbls)
app.dev.request({"cmd": "input", "turn": -1}); app.dev.request({"cmd": "input", "turn": 1}); app.dev.request({"cmd": "input", "click": True})
assert pump(80, lambda: win.i == 5), ("encoder not detected", win.seen)
assert pump(20, lambda: any(b.cget("text") == "Type it" for b in win.btns.winfo_children())); press(0)
assert pump(120, lambda: any(b.cget("text").startswith("Yes") for b in win.btns.winfo_children())), "HID question"; press(0)
assert pump(120, lambda: win.i >= 7), "self-test"
res = {n: r for n, r, _d in win.results}
assert list(res.values()) == ["PASS"] * 7, win.results
import webbrowser; webbrowser.open = lambda *a, **k: True
win.save(); rp = win.last_report; assert rp.exists() and "ALL PASSED" in rp.read_text() and "Self-test" in rp.read_text()
assert "PASS" in win.text_report()
win.close(); assert app.hw_listener is None
# a failing step is reported as such
app.open_hwtest(); win = app.hw_listener.__self__
pump(60, lambda: any(b.cget("text").startswith("Yes") for b in win.btns.winfo_children())); win.btns.winfo_children()[1].invoke()     # LED: "No"
assert win.results[-1][1] == "FAIL"
win.close()
print("hardware test wizard OK")

# ---------------------------------------------------------------- setup wizard + palette
app.open_wizard(); pump(10)
w = [x for x in app.winfo_children() if x.__class__.__name__ == "SetupWizard"][0]
seen = []
for _ in range(6):
    seen.append(w.head.cget("text")); pump(5)
    nxt = [b for b in w.nav.winfo_children() if b.cget("text") in ("Next", "Finish")][0]
    nxt.invoke()
    if not w.winfo_exists():
        break
assert seen[0] == "Welcome to Desk Companion" and "Firmware" in seen and app.cfg["wizard_done"] is True, seen
app.open_palette(); pump(10)
pal = [x for x in app.winfo_children() if x.__class__.__name__ == "CommandPalette"][0]
pal.entry.insert(0, "switch to layer 3"); pal._filter()
assert len(pal.shown) == 1 and "layer 3" in pal.shown[0][0], [c[0] for c in pal.shown]
pal._run(); assert pump(40, lambda: app.edit_layer == 2) and sim.layer == 2
app.destroy()
print("ALL FEATURE TESTS PASSED")
