"""Headless test of the app-level system features: automatic backups + restore, LED follows CPU, start with computer, tray.
xvfb-run -a python3 tools/system_app_test.py"""
import os
import sys
import tempfile
import time
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="dcsysapp_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402
from desk_lib import padextras, tray, wizards   # noqa: E402

app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


# ---------------------------------------------------------------- automatic backups follow save_config
ab = app.autobackup()
assert ab.folder == Path(tmp) / "cfg.json.backups"
ab.min_interval = 0
n0 = len(ab.list())
app.cfg["layers"][0]["1"] = {"cat": "Editing", "action": "Cut"}
m.save_config(app.cfg)
assert len(ab.list()) == n0 + 1, "a changed key map is snapshotted"
m.save_config(app.cfg)
app.cfg["usage"]["K1"] = 5; m.save_config(app.cfg)
assert len(ab.list()) == n0 + 1, "an unchanged config / usage counters do not add snapshots"
good = ab.list()[0][0]
app.cfg["layers"][0]["1"] = {"cat": "Editing", "action": "Paste"}
app.cfg["scripts"]["keepme"] = "key a"
m.save_config(app.cfg)
assert len(ab.list()) == n0 + 2
# the dialog lists them and restores one (the key map goes back, the app refreshes)
dlg = wizards.AutoBackupDialog(app)
app.update()
assert len(dlg.box.winfo_children()) == n0 + 2
dlg.restore(good)
app.update()
assert app.cfg["layers"][0]["1"]["action"] == "Cut" and "keepme" not in app.cfg["scripts"] and "restored" in app.status.cget("text")
bad = Path(tmp) / "bad.json"; bad.write_text("{}")
dlg2 = wizards.AutoBackupDialog(app); dlg2.restore(bad)
assert "not a Desk Companion settings backup" in app.status.cget("text") and app.cfg["layers"][0]["1"]["action"] == "Cut"
dlg2.destroy()
ab.min_interval = 120

# ---------------------------------------------------------------- LED follows the CPU load
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
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
from desk_lib import autostart   # noqa: E402
if sys.platform.startswith("linux"):
    app.autostart_var.set(True); app._autostart_toggled()
    assert autostart.is_enabled() and "will start minimised" in app.status.cget("text")
    desk = (Path(tmp) / ".config/autostart/deskcompanion.desktop").read_text()
    assert "--minimized" in desk and "companion_app.py" in desk
    app.autostart_var.set(False); app._autostart_toggled()
    assert not autostart.is_enabled() and "no longer starts" in app.status.cget("text")
    import shutil
    shutil.rmtree(Path(tmp) / ".config")
    (Path(tmp) / ".config").write_text("a file where a folder should be")        # the change fails: the switch flips back and says why
    app.autostart_var.set(True); app._autostart_toggled()
    assert app.autostart_var.get() is False and "Could not change" in app.status.cget("text")
    (Path(tmp) / ".config").unlink()

# ---------------------------------------------------------------- tray (fake pystray)
made = []


class FakeTray:
    def __init__(self, a): self.app, self.started, self.stopped = a, 0, 0; made.append(self)
    def start(self): self.started += 1
    def stop(self): self.stopped += 1


real_av, real_tray = tray.available, tray.Tray
tray.available = lambda: False
assert "pystray" in app.tray_enable(True) and app.tray_icon is None
app.tray_var.set(True); app._tray_toggled()
assert app.tray_var.get() is False and app.cfg["tray"] is False and "pip install pystray" in app.status.cget("text")
tray.available, tray.Tray = (lambda: True), FakeTray
app.tray_var.set(True); app._tray_toggled()
assert app.cfg["tray"] is True and made and made[0].started == 1 and app.tray_icon is made[0]
# closing the window hides it instead of quitting while the tray is on
app.update()
app._on_close()
assert not app.closing and app.state() == "withdrawn", app.state()
app.show_window(); app.update()
assert app.state() == "normal"
app.tray_layer(2); assert app.edit_layer == 2
app.tray_var.set(False); app._tray_toggled()
assert app.tray_icon is None and made[0].stopped == 1 and app.cfg["tray"] is False
tray.available, tray.Tray = real_av, real_tray
# a tray icon that fails to start is reported, not fatal
class Broken:
    def __init__(self, a): raise RuntimeError("no tray on this desktop")
tray.available, tray.Tray = (lambda: True), Broken
assert "no tray" in app.tray_enable(True) and app.tray_icon is None
tray.available, tray.Tray = real_av, real_tray
assert padextras.cpu_color(0) == (0, 96, 0)
app.destroy()
print("ALL SYSTEM APP TESTS PASSED")
