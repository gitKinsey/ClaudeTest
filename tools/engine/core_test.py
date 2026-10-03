"""Engine test: the app against the CoreBringup diagnostic sketch (simulated) - no command spam, plain-language trouble hints, guards that stop full-firmware actions."""
import os
import sys

os.environ["DESK_COMPANION_SIM_CORE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402

k = Kit("dccore_")
from core.engine import Engine    # noqa: E402
app, sim, pump = k.e, k.sim, k.pump
tiles, troubles = [], []
app.on("health", lambda t: tiles.append(t))
app.on("trouble", lambda t: troubles.append(t))

tt = Engine.trouble_text
boot = {"device": "COM5", "vid": 0x303A, "pid": 0x1001}
assert "WITHOUT holding BOOT" in tt(boot, 2) and "303A:1001" in tt(boot, 2)
mine = {"device": "COM5", "vid": 0x303A, "pid": 0x822B}
t = tt(mine, 2, recently_flashed=True)
assert "40 s" in t and "COM5" in t and "303A:822B" in t and "Device Manager" in t
assert "40 s" not in tt(mine, 9, recently_flashed=False)
assert tt(None, 2)

assert sim.core and app.dev.info.get("core_only")
pump(60)
assert sim.cmd_count.get("time", 0) == 0, f"time was sent to a core-only pad {sim.cmd_count}"
assert sim.cmd_count.get("os", 0) == 0 and sim.cmd_count.get("layout", 0) == 0, sim.cmd_count
assert sim.cmd_count.get("stats", 0) >= 2, sim.cmd_count
assert app.fw_status()[0] == "core"
app.refresh_health()
assert pump(40, lambda: tiles and "CoreBringup" in tiles[-1]["fw"][0]), tiles
assert app.dev.request({"cmd": "ping"}).get("evt") == "pong"
before = dict(sim.cmd_count)
app.upload_all()
app.pump()
assert "CoreBringup" in k.status and sim.cmd_count == before, (k.status, sim.cmd_count)

app.dev.info["core_only"] = False
sim.cmd_count.clear()
pump(80)
assert sim.cmd_count.get("time", 0) <= 1, f"time retry storm: {sim.cmd_count}"
assert sim.cmd_count.get("stats", 0) >= 3

app.dev.info["core_only"] = True
sim.disp_why = "init_hang"
app._update_health({"ok_disp": False, "disp_why": "init_hang", "fw": "1.2.0", "reset": "power-on", "crashes": 0, "fs_free": 0})
app.pump()
assert tiles[-1]["disp"][0] == "stalled" and "display start-up stalled" in k.status

app._show_trouble("COM5", mine, 2)
assert troubles[-1] and "COM5" in troubles[-1]
app._clear_trouble()
assert troubles[-1] is None
k.close()
print("core_test OK")
