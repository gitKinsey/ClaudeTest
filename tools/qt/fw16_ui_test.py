"""Qt widget test of the firmware 1.6 controls: layer names, program name / banners / accent switches, perf line, the twin following the pad's own changes,
and the same controls against a simulated 1.5 pad (they explain instead of doing nothing)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit, find_button    # noqa: E402

from PySide6.QtWidgets import QLabel, QLineEdit    # noqa: E402

from ui_qt.widgets import ToggleRow    # noqa: E402


def row(root, text):
    hits = [t for t in root.findChildren(ToggleRow) if text in t.text.text()]
    assert hits, f"no switch {text!r}: {[t.text.text() for t in root.findChildren(ToggleRow)]}"
    return hits[0]


k = QtKit("dcfw16ui_", cfg={"advanced": True})
e, sim, sh = k.e, k.sim, k.shell

# ---- layer names (Keys -> Key map)
keys = k.go("keys")
keys.inspector.show_tab("Key map"); k.spin(4)
assert len(keys.name_edits) == 3 and all(ed.maxLength() == 10 for ed in keys.name_edits)
assert "pad shows these" in keys.names_note.text(), keys.names_note.text()
for ed, t in zip(keys.name_edits, ("WORK", "MUSIC", "")):
    ed.setText(t)
find_button(keys.inspector.tabs["Key map"], "Save layer names").click()
assert k.until(lambda: sim.lnames == ["WORK", "MUSIC", ""], 10), sim.lnames
assert e.layer_names() == ["WORK", "MUSIC", ""]
keys.name_edits[0].setText("Café long long name"); keys.name_edits[0].returnPressed.emit()
assert k.until(lambda: sim.lnames[0].isascii() and len(sim.lnames[0]) <= 10, 10), sim.lnames

# ---- program name switch (Rules -> Programs)
rules = k.go("rules")
sw = row(rules.programs, "program's name on the pad")
assert sw.toggle.isChecked() is True
sw.toggle.click(); assert e.cfg["pad_ctx"] is False
n = sim.cmd_count.get("ctx", 0)
assert not e.send_ctx("x") and sim.cmd_count.get("ctx", 0) == n
sw.toggle.click(); assert e.cfg["pad_ctx"] is True and e.send_ctx("Zoom") and sim.ctx == "Zoom"

# ---- banners and accent (Pad & App)
pa = k.go("padapp")
pa.tabs.show_tab("Behaviour"); k.spin(3)
sw = row(pa.behaviour, "reminders and app messages on the pad")
assert sw.toggle.isChecked()
sw.toggle.click(); assert e.cfg["pad_toasts"] is False and not e.pad_toast("hidden")
sw.toggle.click(); assert e.pad_toast("shown") and k.until(lambda: sim.toast_log and sim.toast_log[-1]["text"] == "shown", 5)
pa.tabs.show_tab("This app"); k.spin(3)
sw = row(pa.app, "pad follows this accent")
sw.toggle.click(); assert e.cfg["pad_accent"] is True and k.until(lambda: sim.settings15.get("accent") == 1, 10)
e.set_pref("accent", "green"); assert k.until(lambda: sim.settings15["accent"] == 3, 10)
sw.toggle.click(); assert k.until(lambda: sim.settings15["accent"] == 0, 10)
e.set_pref("accent", "cyan")

# ---- counters in Diagnostics
pa.tabs.show_tab("Diagnostics"); k.spin(3)
e.perf_refresh()
assert k.until(lambda: any("loop: average" in l.text() for l in pa.diagnostics.findChildren(QLabel)), 10)

# ---- the twin follows the pad's own screen / brightness / layer changes
sim.pad_side_change(mode=4, bright=77, layer=1)
assert k.until(lambda: e.pad.mode == 4 and e.pad.brightness == 77 and e.pad_layer == 1, 10)
k.go("keys")
assert "layer 2" in keys.layer_pill._text.lower(), keys.layer_pill._text
look = k.go("display"); look.tabs.show_tab("Look"); k.spin(4)
from PySide6.QtWidgets import QSlider    # noqa: E402
sl = [s for s in look.look.findChildren(QSlider) if s.maximum() == 255][0]
assert sl.value() == 77, sl.value()
k.close()

# ---- a pad without 1.6: the same controls explain themselves
os.environ["DESK_COMPANION_SIM_V15"] = "1"
k5 = QtKit("dcfw15ui_", cfg={"advanced": True})
e5, sim5 = k5.e, k5.sim
keys = k5.go("keys"); keys.inspector.show_tab("Key map"); k5.spin(4)
assert "cannot show layer names" in keys.names_note.text(), keys.names_note.text()
keys.name_edits[0].setText("WORK"); find_button(keys.inspector.tabs["Key map"], "Save layer names").click(); k5.spin(6)
assert e5.layer_names()[0] == "WORK" and "layer_names" not in sim5.cmd_count and "cannot show" in e5.status[0]
pa = k5.go("padapp"); pa.tabs.show_tab("This app"); k5.spin(3)
row(pa.app, "pad follows this accent").toggle.click(); k5.spin(4)
assert "cannot follow" in e5.status[0] and "accent" not in sim5.settings15
k5.close()
print("ALL QT FW16 UI TESTS PASSED")
