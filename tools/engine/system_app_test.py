"""Engine test of the app-level system features: automatic backups + restore, LED follows CPU, start with computer, tray."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402

k = Kit("dcsysapp_")
import core.base as m    # noqa: E402
from desk_lib import padconst, tray    # noqa: E402
app, sim, pump = k.e, k.sim, k.pump
tmp = k.tmp
status = lambda: k.status    # noqa: E731

# ---------------------------------------------------------------- automatic backups follow save_config
ab = app.autobackup()
assert ab.folder == Path(tmp) / "cfg.json.backups"
ab.min_interval = 0
n0 = len(ab.list())
app.cfg["layers"][0]["1"] = {"cat": "Editing", "action": "Cut"}
m.save_config(app.cfg)
assert len(ab.list()) == n0 + 1, "a changed key map is snapshotted"
m.save_config(app.cfg)
app.cfg["usage"]["K1"] = 5
m.save_config(app.cfg)
assert len(ab.list()) == n0 + 1, "an unchanged config / usage counters do not add snapshots"
good = ab.list()[0][0]
app.cfg["layers"][0]["1"] = {"cat": "Editing", "action": "Paste"}
app.cfg["scripts"]["keepme"] = "key a"
m.save_config(app.cfg)
assert len(app.autobackup_list()) == n0 + 2
assert app.autobackup_restore(good)
assert app.cfg["layers"][0]["1"]["action"] == "Cut" and "keepme" not in app.cfg["scripts"] and "restored" in status()
bad = Path(tmp) / "bad.json"; bad.write_text("{}")
assert not app.autobackup_restore(bad)
assert "not a Desk Companion settings backup" in status() and app.cfg["layers"][0]["1"]["action"] == "Cut"
ab.min_interval = 120

# ---------------------------------------------------------------- LED follows the CPU load
assert pump(10)
app.cfg["led_cpu"] = False
app._led_follow_cpu(5)
assert sim.led["mode"] == 0, "off: nothing is sent"
app.cfg["led_cpu"] = True
app._led_follow_cpu(0)
assert (sim.led["r"], sim.led["g"], sim.led["b"], sim.led["mode"]) == (0, 96, 0, 2), sim.led
n = sim.cmd_count["led"]
app._led_follow_cpu(3); assert sim.cmd_count["led"] == n, "tiny changes are not sent"
app._led_follow_cpu(100); assert (sim.led["r"], sim.led["g"]) == (96, 0)
app.cfg["led_cpu"] = False
app._led_follow_cpu(100)
assert sim.led["mode"] == 0, "switching it off gives the LED back to the firmware (auto)"
n = sim.cmd_count["led"]; app._led_follow_cpu(100); assert sim.cmd_count["led"] == n
app.dev.info["core_only"] = True; app.cfg["led_cpu"] = True; app._led_follow_cpu(50); assert sim.cmd_count["led"] == n, "CoreBringup keeps its own LED"
app.dev.info["core_only"] = False; app.cfg["led_cpu"] = False

# ---------------------------------------------------------------- start with the computer (a temporary HOME, Linux autostart file)
from desk_lib import autostart    # noqa: E402
if sys.platform.startswith("linux"):
    assert app.autostart_set(True) and autostart.is_enabled() and "will start minimised" in status()
    desk = (Path(tmp) / ".config/autostart/deskcompanion.desktop").read_text()
    assert "--minimized" in desk and "companion_qt.py" in desk
    assert app.autostart_set(False) and not autostart.is_enabled() and "no longer starts" in status()
    import shutil
    shutil.rmtree(Path(tmp) / ".config")
    (Path(tmp) / ".config").write_text("a file where a folder should be")
    assert app.autostart_set(True) is False and "Could not change" in status()
    (Path(tmp) / ".config").unlink()

# ---------------------------------------------------------------- tray (fake pystray)
made = []


class FakeTray:
    def __init__(self, a):
        self.app, self.started, self.stopped = a, 0, 0
        made.append(self)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


real_av, real_tray = tray.available, tray.Tray
import core.engine_core as ec    # noqa: E402
ec.tray.available = lambda: False
assert "pystray" in app.tray_enable(True) and app.tray_icon is None
assert app.tray_set(True) is False and app.cfg["tray"] is False and "pip install pystray" in status()
ec.tray.available, ec.tray.Tray = (lambda: True), FakeTray
assert app.tray_set(True) and app.cfg["tray"] is True and made and made[0].started == 1 and app.tray_icon is made[0]
assert app.want_hide_on_close(), "closing the window hides it instead of quitting while the tray is on"
shown = []
app.on("show_window", lambda: shown.append(1))
app.show_window()
assert shown
app.tray_layer(2); assert app.edit_layer == 2
assert app.tray_set(False) and app.tray_icon is None and made[0].stopped == 1 and app.cfg["tray"] is False
assert not app.want_hide_on_close()
ec.tray.available, ec.tray.Tray = real_av, real_tray


class Broken:
    def __init__(self, a):
        raise RuntimeError("no tray on this desktop")


ec.tray.available, ec.tray.Tray = (lambda: True), Broken
assert "no tray" in app.tray_enable(True) and app.tray_icon is None
ec.tray.available, ec.tray.Tray = real_av, real_tray
assert padconst.cpu_color(0) == (0, 96, 0)
k.close()
print("ALL ENGINE SYSTEM APP TESTS PASSED")
