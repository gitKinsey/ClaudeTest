"""The new app against a pad that still runs firmware 1.1.0 (single layer, 5 screens, no slots / info / recovery commands).
xvfb-run -a python3 tools/compat_test.py"""
import os
import sys
import tempfile
import time

tmp = tempfile.mkdtemp(prefix="dccompat_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
os.environ["DESK_COMPANION_SIM_LEGACY"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tkinter.messagebox as mb   # noqa: E402
import companion_app as m   # noqa: E402

mb.askyesno = lambda *a, **k: True
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
assert sim.legacy and app.dev.info["fw"] == "1.1.0" and "layers" not in app.dev.info and "caps" not in app.dev.info
status = lambda: app.status.cget("text")   # noqa: E731

# banner + status say "older", nothing complains on connect
pump(30)
assert app.fw_status()[0] == "old" and app.fw_banner.winfo_manager() == "pack"
assert "Could not" not in status() and "failed" not in status().lower(), status()
assert not app._layers_supported()

# upload: only layer 1, no "layer" field ever sent, read-back verified
app.upload_all()
assert pump(200, lambda: "Uploaded to the pad: 1 layer" in status()), status()
assert "verified" in status() and len(sim.layers[0]) == 7 and not sim.layers[1] and not sim.layers[2]
# editing layer 2 is allowed in the app, but sending it is refused with a hint instead of overwriting layer 1
app._layer_seg("Layer 2"); app.drop_assign(1, {"cat": "Editing", "action": "Find"}); app.upload_slots([1]); pump(20)
assert "no layers" in status() and sim.layers[0][1] != {"type": "combo", "val": ["PRIMARY", "f"]}, (status(), sim.layers[0][1])
app._layer_seg("Layer 1")
# other things work as before
app.drop_assign(2, {"cat": "Editing", "action": "Cut"}); app.upload_slots([2])
assert pump(60, lambda: sim.layers[0].get(2) == {"type": "combo", "val": ["PRIMARY", "x"]})
app.verify_pad_keys(); assert pump(60, lambda: "Key check OK" in status()), status()
app.dev.request({"cmd": "mode", "val": 5}); app.pad.set_mode(6)
# the Info feed never sends cards to a pad without the Info screen
app.info_poll = 0.15; app.cfg["info"]["custom"] = True; app.cfg["info"]["c_t"] = "hi"
time.sleep(0.8); pump(10); assert sim.cards == []
# GIF: slot 1 works, the slot manager explains instead of erroring
app.tabs.set("GIF Upload"); app.use_preset("Spinner"); assert pump(120, lambda: app.gif_data is not None)
app.upload_gif(); assert pump(200, lambda: sim.gifs.get(0) == len(app.gif_data)), sim.gifs
app.refresh_pad_gifs(); pump(10)
assert "single GIF slot" in " ".join(w.cget("text") for w in app.pad_gif_box.winfo_children() if hasattr(w, "cget")), "legacy hint"
app.gif_slot_var.set("Slot 2"); app.upload_gif(); pump(10)
assert "single GIF slot" in status(), status()
# health + recovery do not break
app.refresh_health(); assert pump(40, lambda: app.health["fw"].cget("text") == "1.1.0")
app.recovery_check(); assert pump(40, lambda: "normal mode" in app.safe_lbl.cget("text"))
app.recovery("safe_retry"); pump(20); assert "failed" in status().lower() or "unknown" in status().lower() or "older" in status().lower(), status()
# profiles: layer commands are rejected by old firmware - the loop must survive
class FW:
    def get(self): return ("code", "x")
app.active_win, app.profile_poll = FW(), 0.1
app.cfg["profiles"] = [{"name": "c", "match": "code", "kind": "process", "layer": 2, "enabled": True}]; app.cfg["profiles_on"] = True
time.sleep(0.8); pump(10)
assert app.dev.connected, "profile loop must not drop the connection"
assert "no layers" in app.prof_live.cget("text"), app.prof_live.cget("text")
app.destroy()
print("ALL COMPAT TESTS PASSED")
