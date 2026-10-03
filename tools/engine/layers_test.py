"""Engine test: layers - editor state, per-layer upload / read-back, layer actions, pad events, host-action safety, config migration.
    python3 tools/engine/layers_test.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit, isolate    # noqa: E402

tmp = isolate("dclayers_")
import core.base as m    # noqa: E402

legacy = {"os": "win", "map": {"1": {"cat": "Editing", "action": "Cut"}}, "custom": {}, "pushed": {"1": "x"}}
open(os.environ["DESK_COMPANION_CONFIG"], "w").write(json.dumps(legacy))
cfg = m.load_config()
assert len(cfg["layers"]) == 3 and cfg["layers"][0]["1"]["action"] == "Cut", cfg["layers"][0]
assert cfg["layers"][1]["1"] == {"cat": "Media", "action": "Previous Track"} and cfg["layers"][2]["1"]["action"] == "Back"
assert cfg["map"] is cfg["layers"][0] and cfg["pushed"] is cfg["pushed_layers"][0] and cfg["pushed"] == {"1": "x"}
m.save_config(cfg)
saved = json.load(open(os.environ["DESK_COMPANION_CONFIG"]))
assert saved["map"]["1"]["action"] == "Cut" and len(saved["layers"]) == 3 and "edit_layer" not in saved
os.remove(os.environ["DESK_COMPANION_CONFIG"])
print("config migration OK")

k = Kit("dclayers2_")
e, sim, pump = k.e, k.sim, k.pump
assert e.dev.info["layers"] == 3 and "layers" in e.dev.info["caps"]

# ---- editing layer 2: the editor state, the twin and cfg["map"] all follow
k.listen("edit_layer", "map_changed", "pending")
e.set_edit_layer(1)
assert e.edit_layer == 1 and e.cfg["map"] is e.cfg["layers"][1] and e.pad.layer == 1
assert ("edit_layer", (1,)) in k.events and e.slot_assignment(1) == ("Media", "Previous Track")
e.drop_assign(1, {"cat": "Editing", "action": "Find"})
assert e.cfg["layers"][1]["1"]["action"] == "Find" and e.cfg["layers"][0]["1"]["action"] == "Copy"
assert 1 in e.pending_by_layer[1] and 1 in e.pending_slots
e.upload_slots([1])
assert pump(60, lambda: sim.layers[1].get(1) == {"type": "combo", "val": ["PRIMARY", "f"]}), sim.layers[1]
assert pump(30, lambda: 1 not in e.pending_by_layer[1])

# ---- the virtual keys switch layers
e.drop_assign(5, {"cat": "Layers & Pad", "action": "Next Layer"})
e.exec_slot(5)
pump(5)
assert e.edit_layer == 2 and e.pad.layer == 2, (e.edit_layer, e.pad.layer)
e.set_edit_layer(0)
img = e.pad.render()
e.pad.set_layer(1, notify=False)
e.pad.set_mode(6)
e.pad.set_info([{"k": "m", "label": "NOW PLAYING", "t": "Song", "a": "Artist", "b": "Album"}], [{"name": "mail", "n": 3}])
assert img.tobytes() != e.pad.render().tobytes()
e.pad.click(); e.pad.turn(3); e.pad.click(); e.pad.turn(1); e.pad.click(); e.pad.render()
assert e.pad.layer == 2, e.pad.layer
e.pad.set_mode(1); e.pad.set_layer(0, notify=False); e.set_edit_layer(0)

# ---- pad events: the pad changes layer, the app notices
sim.layers[0][5] = {"type": "layer", "val": "next"}
e.dev.request({"cmd": "input", "k": 5})
assert pump(60, lambda: e.pad_layer == 1), e.pad_layer
e.show_layer_on_pad()
assert pump(40, lambda: sim.layer == 0)

# ---- host actions: only what is in the configuration runs
opened, popen = [], []
e.hostact.open_url = opened.append
e.hostact.popen = lambda *a, **kw: popen.append((a, kw))
e.cfg["custom"]["Docs"] = {"type": "host", "val": {"op": "url", "arg": "https://example.org/docs"}}
e.cfg["custom"]["Danger"] = {"type": "host", "val": {"op": "shell", "arg": "echo pwned"}}
assert ("url", "https://example.org/docs") in e._allowed_host()
sim._send({"evt": "host", "op": "url", "arg": "https://example.org/docs"})
assert pump(60, lambda: opened == ["https://example.org/docs"]), opened
sim._send({"evt": "host", "op": "url", "arg": "https://evil.example/steal"})
sim._send({"evt": "host", "op": "shell", "arg": "echo pwned"})
pump(30)
assert opened == ["https://example.org/docs"] and not popen, (opened, popen)
log = "\n".join(e.vp_log_lines)
assert "refused" in log and "not part of your key configuration" in log and "switched off" in log, log
e.cfg["allow_shell"] = True
sim._send({"evt": "host", "op": "shell", "arg": "echo pwned"})
assert pump(60, lambda: len(popen) == 1) and popen[0][0][0] == "echo pwned"
e.cfg["allow_shell"] = False
e.drop_assign(2, {"cat": "Custom", "action": "Docs"})
e.upload_slots([2])
assert pump(60, lambda: sim.layers[0].get(2, {}).get("type") == "host")
opened.clear()
e.dev.request({"cmd": "input", "k": 2})
assert pump(60, lambda: opened == ["https://example.org/docs"]), opened
print("layers, events, host-action safety OK")

# ---- upload everything: three layers, read back
e.upload_all()
assert pump(200, lambda: "Uploaded to the pad: 3 layers" in k.status), k.status
assert "verified" in k.status
assert all(len(sim.layers[n]) == 7 for n in range(3))
sim.layers[2][3] = {"type": "text", "val": "tampered"}
e.verify_pad_keys()
assert pump(60, lambda: "Key check:" in k.status) and "L3 K3" in k.status, k.status
e.set_edit_layer(1)
e.reset_defaults()
assert pump(60, lambda: not sim.layers[1]) and sim.layers[0], (sim.layers[0], sim.layers[1])
assert e.cfg["layers"][1]["1"]["action"] == "Previous Track"
print("upload / verify / reset OK")
k.close()
print("ALL ENGINE LAYER TESTS PASSED")
