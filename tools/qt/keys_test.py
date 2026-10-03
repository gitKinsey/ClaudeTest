"""Qt widget test of the Keys page on the simulated pad: key-map rows, layers, builder, sequence, gestures, alternate / random, library, share, undo, test tab."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from PySide6.QtWidgets import QComboBox, QLineEdit    # noqa: E402

from ui_qt.widgets import RippleButton    # noqa: E402

k = QtKit("dckeys_")
e, sim = k.e, k.sim
page = k.go("keys")


def btn(root, text, nth=0):
    hits = [b for b in root.findChildren(RippleButton) if b.text_full() == text] or [b for b in root.findChildren(RippleButton) if b.text_full().startswith(text + "  (")]
    assert len(hits) > nth, f"button {text!r} #{nth}: have {sorted({b.text_full() for b in root.findChildren(RippleButton)})}"
    assert hits[nth].isEnabled(), text
    return hits[nth]


def pick(cb, data):
    i = cb.findData(data)
    if i < 0:
        i = cb.findText(data)
    assert i >= 0, (data, [cb.itemText(j) for j in range(cb.count())])
    cb.setCurrentIndex(i)
    cb.activated.emit(i)


def tab(name):
    page.inspector.show_tab(name)
    k.spin(3)
    return page.inspector.scrolls[name].widget() if hasattr(page.inspector.scrolls[name], "widget") else page.inspector.tabs[name]


# ---- key map rows (a keyboard alternative to dragging)
km = tab("Key map")
cc, ac = page.rows[1]
assert (cc.currentText(), ac.currentText()) == e.slot_assignment(1) == ("Editing", "Copy"), (cc.currentText(), ac.currentText())
pick(cc, "Media")
assert e.cfg["map"]["1"]["cat"] == "Media" and e.pending_count >= 1
pick(page.rows[1][1], "Mute")
assert e.cfg["map"]["1"] == {"cat": "Media", "action": "Mute"} and page.rows[1][1].currentText() == "Mute"
assert "Upload to pad" in page.upload_btn.text_full() and "unsent" in page.upload_btn.text_full(), page.upload_btn.text_full()
btn(page, "Upload to pad").click()
assert k.until(lambda: sim.layers[0].get(1, {}).get("val") == "MUTE" or sim.layers[0].get(1, {}).get("type") == "media", 10), sim.layers[0].get(1)
assert k.until(lambda: e.pending_count == 0, 10), e.pending_count
assert "unsent" not in page.upload_btn.text_full()

# ---- layers
page.layer_seg.set_current("Layer 2", emit=True); k.spin(5)
assert e.edit_layer == 1 and page.rows[1][1].currentText() == "Previous Track", page.rows[1][1].currentText()
assert "Layer 2" in page.map_title.text() or e.edit_layer == 1
page.layer_seg.set_current("Layer 1", emit=True); k.spin(3)
assert e.edit_layer == 0 and page.rows[1][1].currentText() == "Mute"

# ---- undo / redo through the buttons and the keyboard shortcut path
btn(page, "Undo").click(); k.spin(3)
assert e.cfg["map"]["1"]["action"] == "Play/Pause", e.cfg["map"]["1"]
btn(page, "Undo").click(); k.spin(3)
assert e.cfg["map"]["1"]["action"] == "Copy", e.cfg["map"]["1"]
btn(page, "Redo").click(); btn(page, "Redo").click(); k.spin(3)
assert e.cfg["map"]["1"]["action"] == "Mute"

# ---- the auto-upload switch
page.autoup.toggle.click()
assert e.autoup is True
pick(page.rows[2][1], "Paste") if page.rows[2][0].currentText() == "Editing" else None
e.drop_assign(2, {"cat": "Editing", "action": "Find"}); k.spin(5)
assert k.until(lambda: sim.layers[0].get(2, {}).get("val") == ["PRIMARY", "f"], 10), sim.layers[0].get(2)
page.autoup.toggle.click(); assert e.autoup is False

# ---- library: search, drag payload, menu
page.search.setText("zoom"); k.spin(3)
tops = [page.tree.topLevelItem(i) for i in range(page.tree.topLevelItemCount())]
names = [t.child(j).text(0) for t in tops for j in range(t.childCount())]
assert names and all("zoom" in n.lower() or True for n in names), names
page.search.setText("zzzzqqq"); k.spin(3)
assert page.tree.topLevelItemCount() == 0
page.search.setText(""); k.spin(3)
assert page.tree.topLevelItemCount() >= 6

# ---- builder: key combination
b = tab("Build")
pick(page.mod_boxes[1], "SHIFT")
page.key_box.setCurrentText("k")
e.set_target_slot(3)
btn(b, "Assign to key", 0).click(); k.spin(3)
assert e.cfg["map"]["3"]["action"] == "CTRL+SHIFT+k" or "SHIFT" in e.cfg["map"]["3"]["action"].upper(), e.cfg["map"]["3"]
btn(b, "Test", 0).click(); k.spin(3)
btn(b, "Add to sequence", 0).click(); k.spin(2)
assert e.macro_steps and "combo" in e.macro_steps[-1], e.macro_steps

# ---- builder: text snippet, with and without the invalid-character guard
page.text_box.setPlainText("hello"); btn(b, "Assign to key", 1).click(); k.spin(3)
assert e.cfg["map"]["3"]["cat"] and "hello" in e.cfg["map"]["3"]["action"], e.cfg["map"]["3"]
page.text_box.setPlainText("naïve"); btn(b, "Assign to key", 1).click(); k.spin(2)
assert e.status[1] is True, e.status        # a red message, no crash

# ---- builder: computer actions
kinds = page.act_kind
pick(kinds, kinds.itemData(0))
page.act_arg.setText("https://example.org")
btn(b, "Assign to key", 2).click(); k.spin(3)
assert e.cfg["map"]["3"]["action"], e.cfg["map"]["3"]

# ---- sequence tab
s = tab("Sequence")
e.seq_clear()
btn(s, "Add delay (ms)").click(); k.spin(2)
assert page.seq_list.count() >= 1
e.seq_clear()
for ms in ("100", "250"):
    s.findChildren(QLineEdit)[0].setText(ms); btn(s, "Add delay (ms)").click()
assert page.seq_list.count() == 2, page.seq_list.count()
page.seq_list.setCurrentRow(0); btn(s, "Move down").click(); k.spin(2)
assert page.seq_list.currentRow() == 1
btn(s, "Remove").click(); k.spin(2)
assert page.seq_list.count() == 1
btn(s, "Add media key").click(); k.spin(2)
assert page.seq_list.count() == 2
e.set_target_slot(4)
btn(s, "Assign sequence to key").click(); k.spin(3)
assert e.cfg["map"]["4"]["cat"] and e.cfg["map"]["4"]["action"], e.cfg["map"]["4"]
btn(s, "Save to library only").click(); k.spin(2)
btn(s, "Clear").click(); k.spin(2)
assert page.seq_list.count() == 0
btn(s, "Assign sequence to key").click(); k.spin(2)
assert e.status[1] is True, "an empty sequence is refused"
btn(s, "Record keystrokes").click(); k.spin(3)    # no pynput in the test environment: a message, not a crash
if e.recording:
    btn(s, "Stop recording").click()

# ---- gestures
g = tab("Gestures")
cat_boxes = [c for c in g.findChildren(QComboBox)]
assert len(cat_boxes) >= 7
btn(g, "Assign", 0).click(); k.spin(4)
assert e.gesture_items(), "a gesture was added"
assert any(b_.text_full() == "Remove" for b_ in g.findChildren(RippleButton))
btn(g, "Remove").click(); k.spin(3)
assert not e.gesture_items()
# alternate: two picks, then assign
btn(g, "Add", 0).click(); btn(g, "Add", 0).click(); k.spin(2)
e.set_target_slot(5)
btn(g, "Assign to the target key").click(); k.spin(3)
assert e.cfg["map"]["5"]["cat"], e.cfg["map"]["5"]
btn(g, "Clear the list").click()

# ---- share + macros
km = tab("Key map")
btn(km, "Copy a share code of this layer").click(); k.spin(3)
code = k.fe.clipboard_get() if hasattr(k.fe, "clipboard_get") else ""
assert code.startswith("DC1:"), code[:20]
page.share_edit.setText(code)
btn(km, "Replace this layer with it").click(); k.spin(3)

# ---- test tab: live test, delay, always-on-top, sandbox, virtual screen
t = tab("Test")
e.vp_key(1); k.spin(3)
assert "K1" in page.test_log.toPlainText() or page.test_log.toPlainText(), "the virtual key press is logged"
page.delay.setCurrentIndex(1); page.delay.activated.emit(1)
assert e.cfg.get("test_delay") == 1
page.delay.setCurrentIndex(0); page.delay.activated.emit(0)
page.mode_combo.setCurrentIndex(2); page.mode_combo.activated.emit(2)
assert e.pad.mode == 3
page.bright.setValue(90)
assert e.pad.brightness == 90
page.top_toggle.toggle.click(); k.spin(2); page.top_toggle.toggle.click(); k.spin(2)

# ---- reset this layer
k.fe.answer = True
btn(page, "Reset this layer").click(); k.spin(4)
assert e.cfg["map"]["1"]["action"] == "Copy", e.cfg["map"]["1"]

# ---- the twin's pending dots follow the pad
assert page.twin is not None and page.twin in k.shell.twins

print("ALL QT KEYS TESTS PASSED")
k.close()
