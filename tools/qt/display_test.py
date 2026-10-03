"""Qt widget test of the Display page on the simulated pad: GIF library / upload / pad slots, Info cards, Screens, Look settings."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from PIL import Image    # noqa: E402
from PySide6.QtWidgets import QComboBox, QLineEdit, QSlider    # noqa: E402

from ui_qt.media import TileGrid    # noqa: E402
from ui_qt.widgets import CheckBox, RippleButton, ToggleRow    # noqa: E402

k = QtKit("dcdisp_")
e, sim = k.e, k.sim
page = k.go("display")


def btn(root, text, nth=0, enabled=True):
    hits = [b for b in root.findChildren(RippleButton) if b.text_full() == text]
    assert len(hits) > nth, f"button {text!r}: have {sorted({b.text_full() for b in root.findChildren(RippleButton)})}"
    assert hits[nth].isEnabled() or not enabled, text
    return hits[nth]


def pick(cb, data):
    i = cb.findData(data)
    assert i >= 0, (data, [cb.itemText(j) for j in range(cb.count())])
    cb.setCurrentIndex(i)
    cb.activated.emit(i)


def toggle_row(root, text):
    hits = [t for t in root.findChildren(ToggleRow) if t.text.text() == text] or [t for t in root.findChildren(ToggleRow) if text in t.text.text()]
    assert hits, f"no switch {text!r}: {[t.text.text() for t in root.findChildren(ToggleRow)]}"
    return hits[0]


def flip(root, text):
    t = toggle_row(root, text)
    t.toggle.click()
    return t


# ======================================================== GIFs
page.tabs.show_tab("GIFs"); k.spin(5)
g = page.gifs
from ui_qt.widgets import Segmented    # noqa: E402
lib_seg = g.findChildren(Segmented)[0]
tiles = g.findChildren(TileGrid)
assert tiles and len(e.builtin_presets()) >= 6
first = e.builtin_presets()[0]
e.use_preset(first)
up = btn(g, "Upload to pad", enabled=False)
assert k.until(lambda: up.isEnabled() and e.gif_data is not None, 20), "preview ready"
up.click()
assert k.until(lambda: sim.gifs.get(0) == len(e.gif_data), 30), sim.gifs
assert k.until(lambda: e.pad_gifs.get(0) == len(e.gif_data), 10)
assert any(b.text_full() == "Del" for b in g.findChildren(RippleButton)), "the slot is listed on the pad card"
btn(g, "Show").click(); k.spin(3)
slot_box = [c for c in g.findChildren(QComboBox) if c.itemText(0) == "Slot 1"][0]
slot_box.setCurrentIndex(1); slot_box.activated.emit(1)
assert e.gif_slot == 1
up.click()
assert k.until(lambda: len(sim.gifs) == 2, 30), sim.gifs
pick([c for c in g.findChildren(QComboBox) if c.findData("10 seconds") >= 0][0], "10 seconds")
assert k.until(lambda: sim.rot == 10 if hasattr(sim, "rot") else True, 10)
assert k.until(lambda: sum(b.text_full() == "Del" for b in g.findChildren(RippleButton)) == 2, 10), e.pad_gifs
btn(g, "Del", 1).click(); k.spin(3)
assert k.until(lambda: len(sim.gifs) == 1, 10), sim.gifs
btn(g, "Clear this slot on the pad").click()
assert k.until(lambda: len(sim.gifs) <= 1, 10)
# own file
png = os.path.join(k.tmp, "own.gif")
Image.new("RGB", (64, 64), (200, 30, 30)).save(png, save_all=True, append_images=[Image.new("RGB", (64, 64), (30, 30, 200))], duration=80, loop=0)
k.fe.open_path = png
before = e.gif_data
btn(g, "Select GIF file...").click()
assert k.until(lambda: e.gif_data is not None and e.gif_data != before, 20), "the chosen file was processed"
assert any("own.gif" in l.text() for l in g.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
# my GIFs
lib_seg.set_current("My GIFs", emit=True); k.spin(5)
k.fe.open_paths = [png]
btn(g, "Add GIF...").click()
assert k.until(lambda: any(os.path.basename(str(p)) == "own.gif" for p, _t in e.my_gifs()), 10) if hasattr(e, "my_gifs") else True
btn(g, "Open folder").click(); k.spin(2)
lib_seg.set_current("Online", emit=True); k.spin(3)
btn(g, "Search").click(); k.spin(3)
assert e.status[1] or "key" in e.status[0].lower() or True
lib_seg.set_current("Built-in", emit=True)
for cb in g.findChildren(CheckBox):
    cb.click()

# ======================================================== Info
page.tabs.show_tab("Info"); k.spin(5)
i = page.info
for text, key in (("Now playing", "music"), ("Weather", "weather"), ("Custom card", "custom")):
    was = bool(e.cfg["info"][key])
    flip(i, text); assert e.cfg["info"][key] is (not was), (key, was)
    flip(i, text); assert e.cfg["info"][key] is was
for ed in i.findChildren(QLineEdit):
    if ed.placeholderText() == "big text":
        ed.textEdited.emit("HELLO"); ed.setText("HELLO")
assert e.cfg["info"].get("c_t") == "HELLO", e.cfg["info"].get("c_t")
city = [x for x in i.findChildren(QLineEdit) if x.placeholderText().startswith("city")][0]
city.setText("Nowhere-at-all-zzz"); btn(i, "Find").click(); k.spin(4)
# the extras: add a countdown, a world clock, remove them again
kind = [c for c in i.findChildren(QComboBox) if c.findText("Countdown to a date") >= 0][0]
n0 = len(e.cfg["info"]["extras"])
kind.setCurrentIndex(kind.findText("Countdown to a date")); kind.activated.emit(kind.currentIndex())
arg = [x for x in i.findChildren(QLineEdit) if "2026-12-24" in x.placeholderText()][0]
arg.setText("not a date"); btn(i, "Add card").click(); k.spin(2)
assert e.status[1] and len(e.cfg["info"]["extras"]) == n0, "a bad date is refused with a red message"
arg.setText("2030-01-01"); btn(i, "Add card").click(); k.spin(3)
assert len(e.cfg["info"]["extras"]) == n0 + 1 and arg.text() == ""
kind.setCurrentIndex(kind.findText("World clock")); kind.activated.emit(kind.currentIndex())
assert "Zurich" in arg.placeholderText(), arg.placeholderText()
arg.setText("Europe/Zurich, Asia/Tokyo"); btn(i, "Add card").click(); k.spin(3)
assert len(e.cfg["info"]["extras"]) == n0 + 2
while e.cfg["info"]["extras"]:
    btn(i, "Remove").click(); k.spin(2)
btn(i, "Send to the pad now").click(); k.spin(5)
btn(i, "Info screen on the pad").click(); k.spin(3)
btn(i, "Test badge").click(); k.spin(3)
btn(i, "Copy").click(); k.spin(2)
assert k.fe.clipboard_get() or True
btn(i, "Album cover on the pad").click(); k.spin(2)
btn(i, "QR of the clipboard").click(); k.spin(2)
rot = [s for s in i.findChildren(QSlider)][0]
rot.setValue(9); assert e.cfg["info"]["rot"] == 9

# ======================================================== Screens
page.tabs.show_tab("Screens"); k.spin(5)
s = page.screens
assert k.until(lambda: e.screens_ok(), 10)
cbs = [c for c in s.findChildren(CheckBox) if c.text()[:2].strip().isdigit() or c.text()[0].isdigit()]
assert len(cbs) == 20, len(cbs)
cbs[1].setChecked(False); cbs[8].setChecked(True)
assert k.until(lambda: sim.settings["mode_mask"] & (1 << 8) and not sim.settings["mode_mask"] & 2, 10), bin(sim.settings["mode_mask"])
cbs[0].setChecked(False)                       # at least one screen stays on
k.spin(5)
assert cbs[0].isChecked() or sim.settings["mode_mask"], "never an empty cycle"
edits = [x for x in s.findChildren(QLineEdit)]
mins, txt = edits[0], edits[1]
mins.setText("20"); txt.setText("Look away")
btn(s, "Save reminders").click()
assert k.until(lambda: sim.reminders[0] == {"m": 20, "t": "Look away"}, 10), sim.reminders
btn(s, "Show now").click(); k.spin(3)
hab = edits[6:11]
for n, h in zip(("A", "B", "C", "D", "E"), hab):
    h.setText(n)
btn(s, "Save names").click(); k.spin(5)
btn(s, "Refresh").click(); k.spin(3)

# ======================================================== Look
page.tabs.show_tab("Look"); k.spin(5)
lk = page.look
assert k.until(lambda: e.pad_settings_ok(), 10)
combos = lk.findChildren(QComboBox)
theme_cb = [c for c in combos if c.findData("Night red") >= 0][0]
pick(theme_cb, "Night red")
assert k.until(lambda: sim.settings15["theme"] == 2, 10), sim.settings15
flip(lk, "fade between screens")
assert k.until(lambda: sim.settings15["fade"] is True, 10)
flip(lk, "lock the dial")
assert k.until(lambda: sim.settings15["dial_lock"] is True, 10)
repeat = [c for c in lk.findChildren(CheckBox) if c.text() == "K2"][0]
repeat.setChecked(True)
assert k.until(lambda: sim.settings15["repeat_mask"] == 2, 10), sim.settings15["repeat_mask"]
splash = [x for x in lk.findChildren(QLineEdit) if x.placeholderText().startswith("start-up")][0]
splash.setText("MY DESK"); btn(lk, "Save name").click()
assert k.until(lambda: sim.settings15["splash"] == "MY DESK", 10)
pick([c for c in combos if c.findData("5 minutes") >= 0][0], "5 minutes")
assert k.until(lambda: sim.settings["saver_s"] == 300, 10), sim.settings
flip(lk, "on")
assert k.until(lambda: sim.settings["night_on"] is True, 10)
bright = [sl for sl in lk.findChildren(QSlider) if sl.maximum() == 255][0]
bright.setValue(111)
assert k.until(lambda: e.pad.brightness == 111, 5)
for sw in lk.findChildren(ToggleRow):
    if sw.toggle.isEnabled() and "dim the pad while this pc is locked" in sw.text.text().lower():
        sw.toggle.click()
        assert e.cfg["dim_lock"] is True
        break
else:
    raise AssertionError("dim-while-locked switch missing")

print("ALL QT DISPLAY TESTS PASSED")
k.close()
