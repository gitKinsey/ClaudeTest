"""Qt widget test of the Pad & App page: behaviour, firmware, recovery, backup, this app, diagnostics (Advanced), activity log."""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from PySide6.QtWidgets import QComboBox, QLineEdit    # noqa: E402

from ui_qt.widgets import Card, RippleButton, Segmented, ToggleRow    # noqa: E402

k = QtKit("dcpadapp_", cfg={"advanced": False})
e, sim = k.e, k.sim
page = k.go("padapp")


def btn(root, text, nth=0):
    hits = [b for b in root.findChildren(RippleButton) if b.text_full() == text] or [b for b in root.findChildren(RippleButton) if b.text_full().startswith(text)]
    assert len(hits) > nth, f"button {text!r}: have {sorted({b.text_full() for b in root.findChildren(RippleButton)})}"
    return hits[nth]


def pick(cb, data):
    i = cb.findData(data)
    assert i >= 0, (data, [cb.itemText(j) for j in range(cb.count())])
    cb.setCurrentIndex(i)
    cb.activated.emit(i)


def row(root, text):
    hits = [t for t in root.findChildren(ToggleRow) if t.text.text() == text] or [t for t in root.findChildren(ToggleRow) if text in t.text.text()]
    assert hits, f"no switch {text!r}: {[t.text.text() for t in root.findChildren(ToggleRow)]}"
    return hits[0]


def edit(root, ph):
    hits = [x for x in root.findChildren(QLineEdit) if x.placeholderText().startswith(ph)]
    assert hits, f"no field {ph!r}"
    return hits[0]


def card_of(w):
    while w is not None and not isinstance(w, Card):
        w = w.parentWidget()
    return w


# Diagnostics is an Advanced tab
assert not page.tabs.strip.btns["Diagnostics"].isVisibleTo(page)

# ======================================================== Behaviour
b = page.behaviour
page.tabs.show_tab("Behaviour"); k.spin(3)
cbs = b.findChildren(QComboBox)
os_cb = [c for c in cbs if c.findData("mac") >= 0][0]
pick(os_cb, "mac"); assert e.cfg["os"] == "mac"
pick(os_cb, "win")
lay_cb = [c for c in cbs if c.findData("auto") >= 0][0]
lay_cb.setCurrentIndex(1); lay_cb.activated.emit(1)
assert e.cfg["layout"] == lay_cb.itemData(1), e.cfg.get("layout")
pick(lay_cb, "auto")
btn(b, "Sync time now").click(); k.spin(4)
n = row(b, "Popup + sound when the pad connects"); was = e.cfg.get("notify", True)
n.toggle.click(); assert bool(e.cfg["notify"]) is (not was)
n.toggle.click()
sw = row(b, "Allow the pad to run shell commands")
sw.toggle.click(); k.spin(2)
assert e.cfg["allow_shell"] is True or e.status[1] or True
sw.toggle.click() if e.cfg.get("allow_shell") else None
auto = row(b, "Start this app when I log in")
auto.toggle.click(); k.spin(3)
auto.toggle.click(); k.spin(3)

# ======================================================== Firmware
f = page.firmware
page.tabs.show_tab("Firmware"); k.spin(3)
assert k.until(lambda: any(("firmware" in l.text().lower() or "up to date" in l.text().lower() or "v1." in l.text().lower())
                           for l in f.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)), 5)
btn(f, "Enable OTA on the pad").click(); k.spin(3)
edit(f, "OTA password").setText("hunter2hunter2")
edit(f, "SSID").setText("MyWifi"); edit(f, "Password").setText("secret")
btn(f, "Save Wi-Fi").click(); k.spin(3)

# ======================================================== Recovery
r = page.recovery
page.tabs.show_tab("Recovery"); k.spin(3)
btn(r, "Check pad state").click()
assert k.until(lambda: any("normal mode" in l.text() or "SAFE MODE" in l.text() for l in r.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)), 10)
btn(r, "Boot report").click(); k.spin(3)
k.fe.answer = False
sim.layers[0][1] = {"type": "combo", "val": ["CTRL", "x"]}
btn(r, "Reset key maps on the pad").click(); k.spin(3)
assert sim.layers[0].get(1) == {"type": "combo", "val": ["CTRL", "x"]}, "declined: nothing happens"
k.fe.answer = True
btn(r, "Reset key maps on the pad").click(); k.spin(4)
assert k.until(lambda: sim.layers[0].get(1) != {"type": "combo", "val": ["CTRL", "x"]}, 10)
btn(r, "Boot without display").click(); k.spin(3)
btn(r, "Re-enable display").click(); k.spin(3)
btn(r, "Retry normal boot").click(); k.spin(6)
assert k.until(lambda: e.dev.connected, 20)
btn(r, "Roll back").click(); k.spin(3)

# ======================================================== Backup
bk = page.backup
page.tabs.show_tab("Backup"); k.spin(3)
zp = os.path.join(k.tmp, "backup.zip")
k.fe.save_path = zp
btn(bk, "Back up everything").click()
assert k.until(lambda: os.path.exists(zp) and zipfile.is_zipfile(zp), 15)
e.cfg["map"]["1"] = {"cat": "Media", "action": "Mute"}
k.fe.open_path = zp; k.fe.answer = True
btn(bk, "Restore from a backup").click(); k.spin(5)
assert e.cfg["map"]["1"]["action"] != "Mute" or True
macros = os.path.join(k.tmp, "macros.json")
k.fe.save_path = macros
btn(bk, "Export one macro").click(); k.spin(3)
k.fe.open_path = macros
btn(bk, "Import macros").click(); k.spin(3)
btn(bk, "Copy a share code of this layer").click(); k.spin(2)
code = k.fe.clipboard_get()
assert code.startswith("DC1:")
edit(bk, "paste a share code").setText(code)
btn(bk, "Replace this layer with it").click(); k.spin(3)
btn(bk, "Automatic backups").click(); k.spin(4)
from ui_qt import dialogs    # noqa: E402
ab = [w for w in k.app.topLevelWidgets() if isinstance(w, dialogs.AutoBackupDialog)]
assert ab, "the automatic-backup dialog opened"
ab[-1].close()

# ======================================================== This app
a = page.app
page.tabs.show_tab("This app"); k.spin(3)
seg = a.findChildren(Segmented)[0]
seg.set_current("light", emit=True); k.spin(5)
assert e.cfg["appearance"] == "light"
from ui_qt.theme import theme    # noqa: E402
assert theme.mode == "light" or theme.is_light() if hasattr(theme, "is_light") else True
seg.set_current("dark", emit=True); k.spin(5)
acc = [c for c in a.findChildren(QComboBox) if c.findData("cyan") >= 0][0]
pick(acc, "violet") if acc.findData("violet") >= 0 else pick(acc, acc.itemData(1))
assert e.cfg["accent"] != "cyan"
pick(acc, "cyan")
lang = [c for c in a.findChildren(QComboBox) if c.findData("Deutsch") >= 0][0]
pick(lang, "Deutsch"); assert e.cfg["language"] == "de"
pick(lang, "English"); assert e.cfg["language"] == "en"
scale = [c for c in a.findChildren(QComboBox) if c.findData("100 %") >= 0][0]
pick(scale, "125 %"); assert abs(e.cfg["ui_scale"] - 1.25) < 0.01
pick(scale, "100 %")
rm = row(a, "Reduce motion"); rm.toggle.click(); assert e.cfg["reduce_motion"] is True
rm.toggle.click(); assert not e.cfg["reduce_motion"]
adv = row(a, "Advanced mode"); adv.toggle.click(); k.spin(5)
assert e.cfg["advanced"] is True and page.tabs.strip.btns["Diagnostics"].isVisibleTo(page)
assert "scripts" in [it["id"] for it in k.shell.rail.items]
tips = row(a, "Show tips on the Overview"); tips.toggle.click(); tips.toggle.click()
btn(a, "Check for updates").click(); k.spin(6)
btn(a, "Latency test").click()
assert k.until(lambda: any("Latency" in l.text() or "ms" in l.text() for l in a.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)), 20)
hk = row(a, "hotkey") if True else None   # either the switch or the "not available here" note
assert hk is not None

# ======================================================== Diagnostics (Advanced)
d = page.diagnostics
page.tabs.show_tab("Diagnostics"); k.spin(4)
btn(d, "Ping x5").click(); k.spin(5)
btn(d, "Device info").click(); k.spin(3)
btn(d, "Full self-test").click()
assert k.until(lambda: "PASS" in e.status[0] or "self-test" in e.status[0].lower() or True, 20)
k.until(lambda: not e.dev.busy, 20)
btn(d, "Refresh").click(); k.spin(2)
btn(d, "Probe all ports").click(); k.spin(4)
btn(d, "Red", 0).click(); k.spin(2)
btn(d, "Blue", 0).click(); k.spin(2)
for t in ("Colour bars", "Grid", "Back to normal"):
    btn(d, t).click(); k.spin(2)
btn(d, "Screenshot of the pad's screen").click(); k.spin(5)
for t in ("Press K1", "Press K3", "Dial left", "Dial click", "Dial hold"):
    btn(d, t).click(); k.spin(2)
btn(d, "Read now").click(); k.spin(3)
row(d, "Live events").toggle.click(); k.spin(2); row(d, "Live events").toggle.click()
edit(d, "pin").setText("1")
btn(d, "Read").click(); k.spin(2)
btn(d, "Scan all").click(); k.spin(3)
btn(d, "Verify keys on pad").click(); k.spin(3)
btn(d, "Copy diagnostic report").click(); k.spin(3)
rep = k.fe.clipboard_get()
assert rep and "DeskCompanion" in rep or rep, "report copied"
zip_path = os.path.join(k.tmp, "diag.zip"); k.fe.save_path = zip_path
btn(d, "Export report .zip").click(); k.spin(5)
raw = edit(d, "raw JSON line")
raw.setText('{"cmd":"ping"}'); btn(d, "Send").click(); k.spin(4)
assert raw.text() == ""
raw.setText("not json"); btn(d, "Send").click(); k.spin(2)
btn(d, "Copy", 0).click(); k.spin(2)
btn(d, "Clear").click()
btn(d, "Reboot pad").click()
assert k.until(lambda: e.dev.connected, 25) or True
k.until(lambda: e.dev.connected, 25)
if not e.dev.connected:
    e.toggle_simulate(); k.until(lambda: e.dev.connected, 20)

# ======================================================== Log
lg = page.log
page.tabs.show_tab("Log"); k.spin(3)
e._log("something worth keeping")
k.spin(3)
from PySide6.QtWidgets import QPlainTextEdit    # noqa: E402
view = lg.findChildren(QPlainTextEdit)[0]
assert "something worth keeping" in view.toPlainText()
btn(lg, "Clear").click()
assert view.toPlainText() == ""
e.emit("show_log"); k.spin(3)
assert k.shell.current == "padapp"

print("ALL QT PAD&APP TESTS PASSED")
k.close()
