"""Engine test: the app against a pad that still runs firmware 1.1.0 (single layer, 5 screens, no slots / info / recovery commands)."""
import os
import sys
import time

os.environ["DESK_COMPANION_SIM_LEGACY"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402

k = Kit("dccompat_")
app, sim, pump = k.e, k.sim, k.pump
status = lambda: k.status    # noqa: E731
fw = []
app.on("fw_status", lambda kind, text, banner: fw.append((kind, banner)))
app.refresh_fw_status()
tiles = []
app.on("health", lambda t: tiles.append(t))
gifs = []
app.on("pad_gifs", lambda st, r: gifs.append(st))

assert sim.legacy and app.dev.info["fw"] == "1.1.0" and "layers" not in app.dev.info and "caps" not in app.dev.info
pump(30)
assert app.fw_status()[0] == "old" and fw[-1][0] == "old" and fw[-1][1]
assert "Could not" not in status() and "failed" not in status().lower(), status()
assert not app._layers_supported()

app.upload_all()
assert pump(200, lambda: "Uploaded to the pad: 1 layer" in status()), status()
assert "verified" in status() and len(sim.layers[0]) == 7 and not sim.layers[1] and not sim.layers[2]
app.set_edit_layer(1); app.drop_assign(1, {"cat": "Editing", "action": "Find"}); app.upload_slots([1]); pump(20)
assert "no layers" in status() and sim.layers[0][1] != {"type": "combo", "val": ["PRIMARY", "f"]}, (status(), sim.layers[0][1])
app.set_edit_layer(0)
app.drop_assign(2, {"cat": "Editing", "action": "Cut"}); app.upload_slots([2])
assert pump(60, lambda: sim.layers[0].get(2) == {"type": "combo", "val": ["PRIMARY", "x"]})
app.verify_pad_keys(); assert pump(60, lambda: "Key check OK" in status()), status()
app.dev.request({"cmd": "mode", "val": 5}); app.pad.set_mode(6)
app.info_poll = 0.15; app.cfg["info"]["custom"] = True; app.cfg["info"]["c_t"] = "hi"
time.sleep(0.8); pump(10); assert sim.cards == []
app.use_preset("Spinner"); assert pump(120, lambda: app.gif_data is not None)
app.upload_gif(); assert pump(200, lambda: sim.gifs.get(0) == len(app.gif_data)), sim.gifs
gifs.clear(); app.refresh_pad_gifs(); pump(10)
assert gifs and gifs[-1] == "old", gifs
app.gif_slot = 1; app.upload_gif(); pump(10)
assert "single GIF slot" in status(), status()
app.gif_slot = 0
app.refresh_health(); assert pump(40, lambda: tiles and tiles[-1]["fw"][0] == "1.1.0"), tiles
texts = []
app.on("safe_text", lambda t, kd: texts.append(t))
app.recovery_check(); assert pump(40, lambda: texts and "normal mode" in texts[-1])
app.recovery("safe_retry"); pump(20); assert "failed" in status().lower() or "unknown" in status().lower() or "older" in status().lower(), status()


class FW:
    def get(self):
        return ("code", "x")


app.active_win, app.profile_poll = FW(), 0.1
app.cfg["profiles"] = [{"name": "c", "match": "code", "kind": "process", "layer": 2, "enabled": True}]; app.cfg["profiles_on"] = True
time.sleep(0.8); pump(10)
assert app.dev.connected, "profile loop must not drop the connection"
assert "no layers" in app._profile_state["text"], app._profile_state["text"]
k.close()
print("ALL ENGINE COMPAT TESTS PASSED")
