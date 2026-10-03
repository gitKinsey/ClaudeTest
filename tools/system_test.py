"""Pure-logic tests: automatic rolling backups, start-with-computer, tray menu, CPU colour.   python3 tools/system_test.py"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from desk_lib import autobackup, autostart, padconst, tray   # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="dcsys_"))

# ---------------------------------------------------------------- automatic backups
clock = {"t": 1_800_000_000.0}
ab = autobackup.AutoBackup(tmp / "bk", keep=3, min_interval=120, clock=lambda: clock["t"])
cfg = {"layers": [{"1": {"cat": "Editing", "action": "Copy"}}, {}, {}], "usage": {"K1": 1}, "pushed": {}, "profiles": []}
p1 = ab.maybe(cfg)
assert p1 and p1.exists() and json.loads(p1.read_text())["layers"][0]["1"]["action"] == "Copy"
assert ab.maybe(cfg) is None, "unchanged config: no new snapshot"
cfg["usage"]["K1"] = 99; cfg["pushed"] = {"1": "x"}
clock["t"] += 500
assert ab.maybe(cfg) is None, "usage counters / upload bookkeeping are not 'changes'"
cfg["layers"][0]["2"] = {"cat": "Editing", "action": "Paste"}
p2 = ab.maybe(cfg); assert p2 and p2 != p1, "a real change after the interval gets a snapshot"
cfg["layers"][0]["3"] = {"cat": "Editing", "action": "Undo"}
clock["t"] += 10
assert ab.maybe(cfg) is None, "changed, but the last snapshot is younger than 120 s"
clock["t"] += 200
assert ab.maybe(cfg), "...and it is taken once the interval has passed"
cfg["profiles"].append({"name": "x"}); assert ab.maybe(cfg, force=True), "force ignores the interval"
clock["t"] += 1000
cfg["scripts"] = {"a": "key a"}; ab.maybe(cfg)
cfg["scripts"]["b"] = "key b"; clock["t"] += 1000; ab.maybe(cfg)
files = ab.list()
assert len(files) == 3, [f[0].name for f in files]                  # keep = 3, newest first
assert json.loads(files[0][0].read_text())["scripts"] == {"a": "key a", "b": "key b"} and files[0][1] >= files[1][1]
# a fresh helper (new app start) knows the last snapshot, so an unchanged config does not create another one
ab2 = autobackup.AutoBackup(tmp / "bk", keep=3, min_interval=0, clock=lambda: clock["t"] + 5000)
assert ab2.maybe(cfg) is None
cfg["scripts"]["c"] = "key c"; assert ab2.maybe(cfg)
# same-second snapshots do not overwrite each other
ab3 = autobackup.AutoBackup(tmp / "bk3", keep=10, min_interval=0, clock=lambda: 1_800_100_000.0)
a, b = ab3.maybe({"layers": [], "x": 1}, force=True), ab3.maybe({"layers": [], "x": 2}, force=True)
assert a != b and a.exists() and b.exists()
# reading
assert autobackup.AutoBackup.read(files[0][0])["layers"]
(tmp / "junk.json").write_text("{nope"); (tmp / "other.json").write_text('{"hello": 1}')
for bad in (tmp / "junk.json", tmp / "other.json", tmp / "missing.json"):
    try:
        autobackup.AutoBackup.read(bad); raise SystemExit(f"accepted {bad.name}")
    except ValueError:
        pass
assert autobackup.AutoBackup(tmp / "does-not-exist").list() == []
(tmp / "bk" / "notes.txt").write_text("keep me"); ab.maybe({"layers": [], "z": 1}, force=True)
assert (tmp / "bk" / "notes.txt").exists(), "pruning only touches backup files"
ro = tmp / "ro"; ro.write_text("a file, not a folder")
assert autobackup.AutoBackup(ro / "sub").maybe({"layers": []}) is None, "an unwritable location never raises"
print("autobackup OK")

# ---------------------------------------------------------------- start with the computer
argv = autostart.launch_command(frozen=False, exe="/usr/bin/python3", script="/opt/dc/companion_qt.py")
assert argv == ["/usr/bin/python3", "/opt/dc/companion_qt.py", "--minimized"]
assert autostart.launch_command(frozen=True, exe="/Apps/DeskCompanion") == ["/Apps/DeskCompanion", "--minimized"]
home = tmp / "home"
assert not autostart.is_enabled("Linux", home)
where = autostart.enable(["/usr/bin/python3", "/path with space/companion_qt.py", "--minimized"], "Linux", home)
d = Path(where).read_text()
assert Path(where) == home / ".config/autostart/deskcompanion.desktop" and 'Exec=/usr/bin/python3 "/path with space/companion_qt.py" --minimized' in d and autostart.is_enabled("Linux", home)
autostart.disable("Linux", home); assert not autostart.is_enabled("Linux", home); autostart.disable("Linux", home)   # twice is fine
where = autostart.enable(["/Apps/Desk & Co/dc", "--minimized"], "Darwin", home)
pl = Path(where).read_text()
assert Path(where) == home / "Library/LaunchAgents/com.deskcompanion.app.plist" and "<string>/Apps/Desk &amp; Co/dc</string>" in pl and "RunAtLoad" in pl and autostart.is_enabled("Darwin", home)
autostart.disable("Darwin", home); assert not autostart.is_enabled("Darwin", home)


class FakeWinreg:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 4, 1
    store = {}

    class _Key:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def OpenKey(self, *a): return self._Key()
    def SetValueEx(self, k, name, _r, _t, val): self.store[name] = val
    def QueryValueEx(self, k, name):
        if name not in self.store:
            raise FileNotFoundError(name)
        return self.store[name], 1
    def DeleteValue(self, k, name):
        if name not in self.store:
            raise FileNotFoundError(name)
        del self.store[name]
wr = FakeWinreg()
assert not autostart.is_enabled("Windows", winreg=wr)
autostart.enable([r"C:\Program Files\DC\dc.exe", "--minimized"], "Windows", winreg=wr)
assert wr.store["DeskCompanion"] == r'"C:\Program Files\DC\dc.exe" --minimized' and autostart.is_enabled("Windows", winreg=wr)
autostart.disable("Windows", winreg=wr); assert not autostart.is_enabled("Windows", winreg=wr); autostart.disable("Windows", winreg=wr)
assert autostart.wants_minimized(["x", "--minimized"]) and not autostart.wants_minimized(["x"])
print("autostart OK")

# ---------------------------------------------------------------- tray (fake pystray: records the menu, never opens a window)
class FakeItem:
    def __init__(self, text, action, default=False): self.text, self.action, self.default = text, action, default
class FakeMenu:
    def __init__(self, *items): self.items = items
class FakeIcon:
    made = []
    def __init__(self, name, image, title, menu):
        self.name, self.image, self.title, self.menu, self.running, self.stopped = name, image, title, menu, False, False
        FakeIcon.made.append(self)
    def run_detached(self): self.running = True
    def stop(self): self.stopped = True
fake = type("P", (), {"MenuItem": FakeItem, "Menu": FakeMenu, "Icon": FakeIcon})
calls = []
class App:
    def post(self, fn): fn()
    def show_window(self): calls.append("show")
    def tray_layer(self, n): calls.append(("layer", n))
    def quit_app(self): calls.append("quit")
t = tray.Tray(App(), fake)
t.start(); t.start()
ic = FakeIcon.made[0]
assert len(FakeIcon.made) == 1 and ic.running and ic.title == "Desk Companion" and ic.image.size == (64, 64) and ic.image.mode == "RGBA"
labels = [i.text for i in ic.menu.items]
assert labels == t.menu_labels() == ["Show Desk Companion", "Layer 1", "Layer 2", "Layer 3", "Quit"] and ic.menu.items[0].default
for it in ic.menu.items[1:4] + ic.menu.items[:1] + ic.menu.items[4:]:
    it.action()
assert calls == [("layer", 0), ("layer", 1), ("layer", 2), "show", "quit"], calls
t.stop(); assert ic.stopped and t.icon is None; t.stop()
assert isinstance(tray.available(), bool)
img = tray.make_icon(32); assert img.size == (32, 32) and img.getpixel((16, 16))[3] == 255 and img.getpixel((0, 0))[3] == 0
print("tray OK")

# ---------------------------------------------------------------- CPU colour
c = padconst.cpu_color
assert c(0) == (0, 96, 0) and c(100) == (96, 0, 0), (c(0), c(100))
mid = c(50); assert mid[0] > 80 and mid[1] > 80 and mid[2] == 0, mid
assert c(-5) == c(0) and c(500) == c(100) and c("30") == c(30)
assert all(0 <= v <= 255 for p in range(0, 101, 5) for v in c(p))
reds = [c(p)[0] for p in range(0, 101, 10)]
assert reds == sorted(reds), "more load = more red"
print("cpu colour OK")
print("ALL SYSTEM TESTS PASSED")
