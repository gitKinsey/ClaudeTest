"""Headless test of layers in the app: editor, per-layer upload / read-back, layer actions, pad events, host-action safety,
config migration.   xvfb-run -a python3 tools/layers_test.py"""
import json
import os
import sys
import tempfile
import time

tmp = tempfile.mkdtemp(prefix="dclayers_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402

# ---- config migration: a pre-layers config file keeps its key map as layer 1
legacy = {"os": "win", "map": {"1": {"cat": "Editing", "action": "Cut"}}, "custom": {}, "pushed": {"1": "x"}}
open(os.environ["DESK_COMPANION_CONFIG"], "w").write(json.dumps(legacy))
cfg = m.load_config()
assert len(cfg["layers"]) == 3 and cfg["layers"][0]["1"]["action"] == "Cut", cfg["layers"][0]
assert cfg["layers"][1]["1"] == {"cat": "Media", "action": "Previous Track"} and cfg["layers"][2]["1"]["action"] == "Back"
assert cfg["map"] is cfg["layers"][0] and cfg["pushed"] is cfg["pushed_layers"][0] and cfg["pushed"] == {"1": "x"}
m.save_config(cfg)
saved = json.load(open(os.environ["DESK_COMPANION_CONFIG"]))
assert saved["map"]["1"]["action"] == "Cut" and len(saved["layers"]) == 3 and "edit_layer" not in saved
assert m.load_config()["layers"][0]["1"]["action"] == "Cut"
os.remove(os.environ["DESK_COMPANION_CONFIG"])
print("config migration OK")

app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):                       # generous: CI machines can be slow
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
assert app.dev.info["layers"] == 3 and "layers" in app.dev.info["caps"]

# ---- editing layer 2: the editor, the twin and cfg["map"] all follow
app.tabs.set("Virtual Pad")
app._layer_seg("Layer 2")
assert app.edit_layer == 1 and app.cfg["map"] is app.cfg["layers"][1] and app.pad.layer == 1 and app.layer_seg.get() == "Layer 2"
assert app.rows[1][3].get() == "Previous Track", app.rows[1][3].get()          # Home-page key table follows the layer
app.drop_assign(1, {"cat": "Editing", "action": "Find"})
assert app.cfg["layers"][1]["1"]["action"] == "Find" and app.cfg["layers"][0]["1"]["action"] == "Copy"
assert 1 in app.pending_by_layer[1] and 1 in app.pending_slots
app.upload_slots([1])
assert pump(60, lambda: sim.layers[1].get(1) == {"type": "combo", "val": ["PRIMARY", "f"]}), sim.layers[1]
assert 1 not in sim.layers[0] or sim.layers[0][1] != sim.layers[1][1], "layer 1 must be untouched"
assert pump(30, lambda: 1 not in app.pending_by_layer[1])

# ---- the virtual keys switch layers
app.drop_assign(5, {"cat": "Layers & Pad", "action": "Next Layer"})
app.exec_slot(5)
pump(5)
assert app.edit_layer == 2 and app.pad.layer == 2 and app.layer_seg.get() == "Layer 3", (app.edit_layer, app.pad.layer)
app._layer_seg("Layer 1")
assert app.edit_layer == 0
img = app.pad.render()                                                      # twin renders layer badge / 6th mode / 5-item menu
app.pad.set_layer(1, notify=False); app.pad.set_mode(6)
app.pad.set_info([{"k": "m", "label": "NOW PLAYING", "t": "Song", "a": "Artist", "b": "Album"}], [{"name": "mail", "n": 3}])
img2 = app.pad.render()
assert img.tobytes() != img2.tobytes()
app.pad.click(); app.pad.turn(3); app.pad.click(); app.pad.turn(1); app.pad.click(); app.pad.render()          # menu: LAYER entry
assert app.pad.layer == 2, app.pad.layer
app.pad.set_mode(1); app.pad.set_layer(0, notify=False); app.set_edit_layer(0)

# ---- pad events: the pad changes layer, the app notices
sim.layers[0][5] = {"type": "layer", "val": "next"}
app.dev.request({"cmd": "input", "k": 5})
assert pump(60, lambda: app.pad_layer == 1), app.pad_layer
assert "layer 2" in app.pad_layer_pill.lbl.cget("text")
app.show_layer_on_pad(); assert pump(40, lambda: sim.layer == 0)                # editor is on layer 1 -> pad follows

# ---- host actions: only what is in the configuration runs
opened, popen = [], []
app.hostact.open_url = opened.append
app.hostact.popen = lambda *a, **k: popen.append((a, k))
app.cfg["custom"]["Docs"] = {"type": "host", "val": {"op": "url", "arg": "https://example.org/docs"}}
app.cfg["custom"]["Danger"] = {"type": "host", "val": {"op": "shell", "arg": "echo pwned"}}
app.refresh_action_lists()
assert ("url", "https://example.org/docs") in app._allowed_host()
sim._send({"evt": "host", "op": "url", "arg": "https://example.org/docs"})
assert pump(60, lambda: opened == ["https://example.org/docs"]), opened
sim._send({"evt": "host", "op": "url", "arg": "https://evil.example/steal"})                 # not in any key map -> refused
sim._send({"evt": "host", "op": "shell", "arg": "echo pwned"})                                  # configured, but shell is switched off
pump(30)
assert opened == ["https://example.org/docs"] and not popen, (opened, popen)
log = app.vp_log_box.get("1.0", "end")
assert "refused" in log and "not part of your key configuration" in log and "switched off" in log, log
app.cfg["allow_shell"] = True
sim._send({"evt": "host", "op": "shell", "arg": "echo pwned"})
assert pump(60, lambda: len(popen) == 1) and popen[0][0][0] == "echo pwned"
app.cfg["allow_shell"] = False
# a pad key bound to a host action fires it through the whole chain (key -> pad -> event -> app -> whitelist -> opener)
app.drop_assign(2, {"cat": "Custom", "action": "Docs"})
app.upload_slots([2]); assert pump(60, lambda: sim.layers[0].get(2, {}).get("type") == "host")
opened.clear()
app.dev.request({"cmd": "input", "k": 2})
assert pump(60, lambda: opened == ["https://example.org/docs"]), opened
print("layers, events, host-action safety OK")

# ---- upload everything: three layers, read back
app.upload_all()
assert pump(200, lambda: "Uploaded to the pad: 3 layers" in app.status.cget("text")), app.status.cget("text")
assert "verified" in app.status.cget("text")
assert all(len(sim.layers[n]) == 7 for n in range(3))
sim.layers[2][3] = {"type": "text", "val": "tampered"}                                         # something changed behind the app's back
app.verify_pad_keys()
assert pump(60, lambda: "Key check:" in app.status.cget("text")) and "L3 K3" in app.status.cget("text"), app.status.cget("text")
# reset only the selected layer
app._layer_seg("Layer 2"); app.reset_defaults()
assert pump(60, lambda: not sim.layers[1]) and sim.layers[0], (sim.layers[0], sim.layers[1])
assert app.cfg["layers"][1]["1"]["action"] == "Previous Track"
print("upload / verify / reset OK")
app.destroy()
print("ALL LAYER TESTS PASSED")
