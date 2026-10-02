"""Headless test: the app against the CoreBringup diagnostic sketch (simulated) - no command spam, plain-language trouble hints,
guards that stop full-firmware actions.   xvfb-run -a python3 tools/core_test.py"""
import os
import sys
import tempfile
import time

tmp = tempfile.mkdtemp(prefix="dccore_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
os.environ["DESK_COMPANION_SIM_CORE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402

app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


# ---- trouble_text: plain-language hints
tt = m.App.trouble_text
boot = {"device": "COM5", "vid": 0x303A, "pid": 0x1001}
assert "WITHOUT holding BOOT" in tt(boot, 2) and "303A:1001" in tt(boot, 2)
mine = {"device": "COM5", "vid": 0x303A, "pid": 0x822B}
t = tt(mine, 2, recently_flashed=True)
assert "40 s" in t and "COM5" in t and "303A:822B" in t and "Device Manager" in t
assert "40 s" not in tt(mine, 9, recently_flashed=False)       # a long-standing failure does not blame the first boot any more
assert tt(None, 2)                                            # unknown port info must not crash

# ---- CoreBringup: connects, is recognised, and is NOT asked for commands it does not know
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
assert sim.core and app.dev.info.get("core_only")
pump(60)                                                       # ~3 s of the 1 Hz telemetry loop
assert sim.cmd_count.get("time", 0) == 0, f"time was sent to a core-only pad {sim.cmd_count}"
assert sim.cmd_count.get("os", 0) == 0 and sim.cmd_count.get("layout", 0) == 0, sim.cmd_count
assert sim.cmd_count.get("stats", 0) >= 2, sim.cmd_count
assert app.fw_status()[0] == "core"
assert "CoreBringup" in app.health["fw"].cget("text")
assert app.dev.request({"cmd": "ping"}).get("evt") == "pong"

# the full-firmware actions refuse politely instead of sending unknown commands
before = dict(sim.cmd_count)
app.upload_all()
app.update()
assert "CoreBringup" in app.status.cget("text") and sim.cmd_count == before, (app.status.cget("text"), sim.cmd_count)

# regression for the retry storm: a pad that refuses `time` is asked once a minute, not every second
app.dev.info["core_only"] = False
sim.cmd_count.clear()
pump(80)
assert sim.cmd_count.get("time", 0) <= 1, f"time retry storm: {sim.cmd_count}"
assert sim.cmd_count.get("stats", 0) >= 3

# ---- a pad whose display start-up stalled is reported plainly (everything else keeps working)
app.dev.info["core_only"] = True
sim.disp_why = "init_hang"
app._update_health({"ok_disp": False, "disp_why": "init_hang", "fw": "1.2.0", "reset": "power-on", "crashes": 0, "fs_free": 0})
app.update()
assert app.health["disp"].cget("text") == "stalled" and "display start-up stalled" in app.status.cget("text")

# ---- trouble card show / clear
app._show_trouble("COM5", mine, 2)
app.update()
assert app.trouble_card.winfo_manager() == "pack" and "COM5" in app.trouble_lbl.cget("text")
app._clear_trouble()
app.update()
assert not app.trouble_card.winfo_manager()
app.destroy()
print("core_test OK")
