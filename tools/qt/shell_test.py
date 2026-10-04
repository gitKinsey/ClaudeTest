"""Qt widget test: shell, rail, Overview, pad twin, theme, Advanced switch, geometry, tray-aware close, mini pad, dialogs."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit, click, find_button, has_label    # noqa: E402

from PySide6.QtCore import QPointF, Qt    # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent    # noqa: E402

k = QtKit("dcqtshell_")
e, sh, app = k.e, k.shell, k.app
from ui_qt.theme import theme    # noqa: E402
from ui_qt.widgets import RippleButton    # noqa: E402

# ---- the rail lists the pages; Scripts is hidden until Advanced is switched on
assert [it["id"] for it in sh.rail.items] == ["overview", "keys", "display", "rules", "padapp"], [it["id"] for it in sh.rail.items]
assert sh.current == "overview" and sh.pages["overview"].isVisible()
sh.rail.selected.emit("keys"); k.spin(10)
assert sh.current == "keys" and sh.stack.currentWidget() is sh.pages["keys"] and sh.title.text() == "Keys"
assert sh.rail.current_id() == "keys"
sh.goto("scripts"); assert sh.current == "keys", "an Advanced page cannot be opened while Advanced is off"
sh.set_advanced(True); k.spin(5)
assert [it["id"] for it in sh.rail.items][-2:] == ["scripts", "padapp"] or "scripts" in [it["id"] for it in sh.rail.items]
sh.goto("scripts"); assert sh.current == "scripts"
assert "Diagnostics" in sh.pages["padapp"].tabs.keys and sh.pages["padapp"].tabs.strip.btns["Diagnostics"].isVisibleTo(sh.pages["padapp"])
sh.set_advanced(False); k.spin(5)
assert sh.current != "scripts" and "scripts" not in [it["id"] for it in sh.rail.items] and not sh.pages["padapp"].tabs.strip.btns["Diagnostics"].isVisibleTo(sh.pages["padapp"])
assert e.cfg["advanced"] is False

# ---- the connection pill, status line, toasts
assert sh.pill._text == "Simulated pad" and sh.pill._kind == "ok"
e.set_status("hello world"); k.spin(3)
assert sh.status.text() == "hello world"
e.set_status("oops", error=True); k.spin(3)
assert sh.status.property("role") == "err"
from ui_qt import toast    # noqa: E402
e.notify("Title", "Body text", "ok"); k.spin(5)
assert len(toast.LIVE) >= 1
for t in list(toast.LIVE):
    t.close()

# ---- Overview: connect / disconnect / simulate, health, telemetry, usage
ov = k.go("overview")
assert ov.conn_btn.text_full() == "Disconnect" and "Connected on Simulated pad" in ov.conn_lbl.text()
assert k.until(lambda: ov.tiles["fw"].val.text() == "1.5.0"), ov.tiles["fw"].val.text()
assert ov.tiles["flash"].val.text().endswith("KB") and ov.tiles["hid"].val.text() == "ok"
assert ov.cpu_l.text().startswith("CPU") and ov.ram_l.text().startswith("RAM")
click(ov, "Stop simulating"); k.spin(5)
assert not e.dev.connected and ov.conn_btn.text_full() == "Connect" and sh.pill._kind == "warn"
click(ov, "Simulate pad (no hardware)")
assert k.until(lambda: e.dev.connected)
k.sim = e.dev.ser.sim
assert ov.sim_btn.text_full() == "Stop simulating"
e._count_use("K1"); e._count_use("K1"); e.emit("usage"); k.spin(3)
assert ov.ubars["K1"][1].text() == "2"
click(ov, "Reset counters"); assert e.cfg["usage"] == {} and ov.ubars["K1"][1].text() == "0"
# banners: firmware / trouble / safe mode / tip
e.dev.info["fw"] = "1.1.0"; e.refresh_fw_status(); k.spin(3)
assert ov.b_fw.isVisibleTo(ov) and "older" in ov.b_fw.text.text() and sh.rail.items[4]["badge"] is True
e.dev.info["fw"] = "1.5.0"; e.refresh_fw_status(); k.spin(3)
assert not ov.b_fw.isVisibleTo(ov)
e._show_trouble("COM5", {"device": "COM5", "vid": 0x303A, "pid": 0x1001}, 2); k.spin(3)
assert ov.b_trouble.isVisibleTo(ov) and "COM5" in ov.b_trouble.text.text() or "built-in bootloader" in ov.b_trouble.text.text()
e._clear_trouble(); k.spin(3)
assert not ov.b_trouble.isVisibleTo(ov)
e._safe_banner({"safe": True}); k.spin(3)
assert ov.b_safe.isVisibleTo(ov)
e._safe_banner({"safe": False}); k.spin(3)
assert not ov.b_safe.isVisibleTo(ov)
e.cfg["tips"] = True; e.refresh_tip(); k.spin(3)
assert ov.b_tip.isVisibleTo(ov)
first = e.tip_id
click(ov, "Got it"); k.spin(3)
assert first in e.cfg["tips_dismissed"]
# overview twin layer buttons
ov.layer_seg.set_current("Layer 2", emit=True); k.spin(3)
assert e.edit_layer == 1
ov.layer_seg.set_current("Layer 1", emit=True)

# ---- the pad twin: hit testing, click, wheel, long press, context menu
kp = sh.pages["keys"]
tw = kp.twin
k.go("keys")
tw.resize(400, 460); k.spin(5)
s, ox, oy = tw._xf()
def at(dx, dy):   # noqa: E306
    return QPointF(ox + dx * s, oy + dy * s)
from core.base import KEY_XS, KEY_Y, ARROW_R, ENC_C    # noqa: E402
r = tw.hit(KEY_XS[1], KEY_Y); assert r["kind"] == "key" and r["slot"] == 2
assert tw.hit(*ARROW_R)["kind"] == "turn" and tw.hit(*ENC_C)["kind"] == "knob" and tw.hit(5, 5) is None
flashed = []
e.on("key_flash", lambda slot: flashed.append(slot))
spun = []
e.on("spin", lambda st: spun.append(st))
def press_release(pt):   # noqa: E306
    tw.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, pt, tw.mapToGlobal(pt), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    tw.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, pt, tw.mapToGlobal(pt), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
press_release(at(KEY_XS[1], KEY_Y)); assert flashed[-1] == 2
press_release(at(*ARROW_R)); assert spun[-1] == 1 and flashed[-1] == 6
vol0 = e.pad.vol
press_release(at(*ENC_C)); e.pad.render()
from PySide6.QtCore import QPoint    # noqa: E402
w = QWheelEvent(at(*ENC_C), tw.mapToGlobal(at(*ENC_C)), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
tw.wheelEvent(w); assert spun[-1] == 1
assert vol0 is not None
# drop on a key assigns (the mime payload is what the library tree / another key produce)
import json    # noqa: E402
from PySide6.QtCore import QByteArray, QMimeData    # noqa: E402
from ui_qt.padview import MIME    # noqa: E402
class FakeDrop:   # noqa: E301
    def __init__(self, pos, payload):
        self._p, self.md, self.accepted = pos, QMimeData(), False
        self.md.setData(MIME, QByteArray(json.dumps(payload).encode()))
    def position(self): return self._p   # noqa: E704
    def mimeData(self): return self.md   # noqa: E704
    def acceptProposedAction(self): self.accepted = True   # noqa: E704
    def ignore(self): self.accepted = False   # noqa: E704
tw.dragMoveEvent(FakeDrop(at(KEY_XS[2], KEY_Y), {"cat": "Editing", "action": "Find"})); assert tw._drop == 3
d = FakeDrop(at(KEY_XS[2], KEY_Y), {"cat": "Editing", "action": "Find"}); tw.dropEvent(d)
assert e.cfg["map"]["3"] == {"cat": "Editing", "action": "Find"} and d.accepted and tw._drop is None
tw.dropEvent(FakeDrop(at(KEY_XS[0], KEY_Y), {"slot": 3}))                       # a key dragged onto another = swap
assert e.cfg["map"]["1"]["action"] == "Find" and e.cfg["map"]["3"]["action"] == "Copy", (e.edit_layer, e.cfg["map"]["1"], e.cfg["map"]["3"], tw._drop)
tw.dropEvent(FakeDrop(at(5, 5), {"cat": "Editing", "action": "Cut"})); assert e.cfg["map"]["2"]["action"] != "Cut", "a drop outside any key does nothing"
e.edit_undo(); e.edit_undo()

# ---- theme / accent / scale are applied live
e.set_appearance("light"); k.spin(5)
assert theme.mode == "light" and not theme.dark and "#f6f6f7" in app.styleSheet().lower()
e.set_appearance("dark"); k.spin(3)
e.set_pref("accent", "pink"); k.spin(3)
assert theme.accent_name == "pink" and theme.c("ACCENT") == "#ff4fa8"
e.set_pref("accent", "cyan"); k.spin(3)
e.set_pref("reduce_motion", True); k.spin(3)
assert theme.reduce_motion is True
sh.rail.set_current("overview", animate=True); assert sh.rail._pos == 0.0, "reduce motion: no sliding"
e.set_pref("reduce_motion", False); k.spin(3)

# ---- responsive chrome
for w_, h_, mode in ((1300, 780, "full"), (1000, 780, "icons"), (640, 520, "top")):
    sh.resize(w_, h_); k.spin(8)
    assert sh.rail.mode == mode, (w_, sh.rail.mode)
assert sh.minimumWidth() == 640 and sh.minimumHeight() == 520
sh.resize(1300, 780); k.spin(5)

# ---- palette, wizard, hardware test, autobackup dialogs, mini pad
from ui_qt import dialogs    # noqa: E402
e.emit("open_palette"); k.spin(5)
pal = [w for w in app.topLevelWidgets() if isinstance(w, dialogs.CommandPalette)][-1]
pal.entry.setText("switch to layer 3"); k.spin(3)
assert len(pal.shown) == 1 and "layer 3" in pal.shown[0][0], [c[0] for c in pal.shown]
pal.list.setCurrentRow(0); pal._run()
assert k.until(lambda: e.edit_layer == 2 and k.sim.layer == 2)
pal.close()
cmds = [c[0] for c in dialogs.palette_commands(sh)]
for need in ("Upload everything to the pad", "Toggle Advanced mode", "Open the mini pad", "Undo the last key assignment  (Ctrl+Z)", "Pad: show screen 20 Sound bars", "Check for app updates", "Latency test"):
    assert need in cmds, need
assert sum(c.startswith("Go to") for c in cmds) == 5
e.set_edit_layer(0)
e.cfg["wizard_done"] = False
e.emit("open_wizard"); k.spin(5)
wz = [w for w in app.topLevelWidgets() if isinstance(w, dialogs.SetupWizard)][-1]
seen = []
for _ in range(6):
    seen.append(wz.head.text()); k.spin(3)
    nxt = find_button(wz, "Next") if wz.i < len(wz.PAGES) - 1 else find_button(wz, "Finish")
    nxt.click()
    if not wz.isVisible():
        break
assert seen[0] == "Welcome to Desk Companion" and "Firmware" in seen and e.cfg["wizard_done"] is True, seen
e.emit("open_autobackup"); k.spin(5)
ab = [w for w in app.topLevelWidgets() if isinstance(w, dialogs.AutoBackupDialog)][-1]
find_button(ab, "Back up now").click(); k.spin(3)
ab.close()
e.emit("open_mini_window"); k.spin(5)
mini = [w for w in app.topLevelWidgets() if isinstance(w, dialogs.MiniPad)][-1]
assert mini.isVisible() and mini.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
mini.resize(260, 300); k.spin(15)
assert mini.view in sh.twins and mini.view._screen is not None, "the mini pad shows the live screen"
mini.close(); assert mini.view not in sh.twins

# ---- hardware test dialog against the simulator
e.emit("open_hwtest"); k.spin(5)
hw = [w for w in app.topLevelWidgets() if isinstance(w, dialogs.HardwareTest)][-1]
def texts():
    return [b.text_full() for b in hw.btns.findChildren(RippleButton)]
def yes():
    return k.until(lambda: any(t.startswith("Yes") for t in texts()), 15)
assert yes(), "LED question"; find_button(hw.btns, "Yes, works").click()
assert k.until(lambda: hw.i == 2 and any(t.startswith("Yes") for t in texts()), 20), "display question"
find_button(hw.btns, "Yes, works").click()
assert k.until(lambda: hw.i == 3 and hw.lbls, 10), "keys step"
for kk in range(1, 6):
    e.dev.request({"cmd": "input", "k": kk})
assert k.until(lambda: hw.i == 4, 10), hw.seen
assert k.until(lambda: "left" in hw.lbls, 5)
e.dev.request({"cmd": "input", "turn": -1}); e.dev.request({"cmd": "input", "turn": 1}); e.dev.request({"cmd": "input", "click": True})
assert k.until(lambda: hw.i == 5, 10), hw.seen
assert k.until(lambda: "Type it" in texts(), 5)
find_button(hw.btns, "Type it").click()
assert k.until(lambda: any(t.startswith("Yes") for t in texts()), 15), "HID question"
find_button(hw.btns, "Yes, works").click()
assert k.until(lambda: hw.i >= 7, 20), "self-test"
assert [r for _n, r, _d in hw.results] == ["PASS"] * 7, hw.results
import webbrowser    # noqa: E402
webbrowser.open = lambda *a, **kw: True
find_button(hw.btns, "Save HTML report").click()
rep = [p for p in __import__("pathlib").Path.home().glob("deskcompanion_hwtest_*.html")]
assert rep and "ALL PASSED" in rep[0].read_text()
hw.close(); assert e.hw_listener is None

# ---- geometry is remembered, close hides when the tray is on, otherwise quits
sh.resize(1111, 700); k.spin(5)
sh.save_geometry()
assert e.cfg["ui_qt"]["geometry"] and isinstance(e.cfg["ui_qt"]["grids"], dict)
e.tray_icon = object(); e.cfg["tray"] = True
sh.close(); k.spin(3)
assert not sh.isVisible() and not e.closing, "with the tray on, closing the window only hides it"
e.show_window(); k.spin(5)
assert sh.isVisible()
e.tray_icon = None
assert has_label(sh, "Overview")
k.close()
print("ALL QT SHELL TESTS PASSED")
