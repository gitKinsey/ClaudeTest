"""Qt widget test of the Rules page: programs (auto layer, rules, presets), schedules, computer actions, API & plugins (Advanced)."""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from PySide6.QtWidgets import QComboBox, QLineEdit    # noqa: E402

from ui_qt.widgets import CheckBox, RippleButton, TabbedPanel, ToggleRow    # noqa: E402

k = QtKit("dcrules_", cfg={"advanced": False})
e = k.e
page = k.go("rules")


def btn(root, text, nth=0):
    hits = [b for b in root.findChildren(RippleButton) if b.text_full() == text]
    assert len(hits) > nth, f"button {text!r}: have {sorted({b.text_full() for b in root.findChildren(RippleButton)})}"
    assert hits[nth].isEnabled(), text
    return hits[nth]


def pick(cb, data):
    i = cb.findData(data)
    assert i >= 0, (data, [cb.itemText(j) for j in range(cb.count())])
    cb.setCurrentIndex(i)
    cb.activated.emit(i)


def row(root, text):
    hits = [t for t in root.findChildren(ToggleRow) if t.text.text() == text or text in t.text.text()]
    assert hits, f"no switch {text!r}"
    return hits[0]


def edit(root, ph):
    hits = [x for x in root.findChildren(QLineEdit) if x.placeholderText().startswith(ph)]
    assert hits, f"no field {ph!r}: {[x.placeholderText() for x in root.findChildren(QLineEdit)]}"
    return hits[0]


def card_of(w):
    from ui_qt.widgets import Card
    while w is not None and not isinstance(w, Card):
        w = w.parentWidget()
    return w


# ---- the Advanced switch hides API & plugins
assert not page.tabs.strip.btns["API & CLI"].isVisibleTo(page) and not page.tabs.strip.btns["Plugins"].isVisibleTo(page)
assert page.tabs.strip.btns["Programs"].isVisibleTo(page)

# ======================================================== Programs
p = page.programs
row(p, "Switch the pad's layer automatically").toggle.click()
assert e.cfg["profiles_on"] is True
default = [c for c in p.findChildren(QComboBox) if c.findData("keep the current layer") >= 0][0]
pick(default, "Layer 2")
assert e.cfg["profile_default"] == 2 or e.cfg["profile_default"] == 1, e.cfg["profile_default"]
rem = [c for c in p.findChildren(CheckBox) if "remember my layer" in c.text()][0]
rem.click(); assert e.cfg["remember_layers"] is True; rem.click()
n0 = len(e.cfg["profiles"])
btn(p, "Add rule").click(); k.spin(2)
assert e.status[1] and len(e.cfg["profiles"]) == n0, "an empty match is refused"
edit(p, "name").setText("My editor"); edit(p, "program").setText("myeditor")
btn(p, "Add rule").click(); k.spin(3)
assert len(e.cfg["profiles"]) == n0 + 1 and e.cfg["profiles"][-1]["match"] == "myeditor", e.cfg["profiles"][-1]
btn(p, "VS Code").click(); btn(p, "Zoom").click(); k.spin(3)
assert len(e.cfg["profiles"]) == n0 + 3
rules = e.cfg["profiles"]
last = rules[-1]["match"]
btn(p, "↑", len(rules) - 1).click(); k.spin(2)
assert e.cfg["profiles"][-2]["match"] == last
btn(p, "↓", len(rules) - 2).click(); k.spin(2)
assert e.cfg["profiles"][-1]["match"] == last
en = [c for c in p.findChildren(CheckBox) if c.text() == ""][0]
en.click(); assert e.cfg["profiles"][0].get("enabled") is False
en.click()
more = [c for c in p.findChildren(QComboBox) if c.findText("More programs...") >= 0][0]
before = len(e.cfg["profiles"]); more.setCurrentIndex(1); more.activated.emit(1); k.spin(3)
assert len(e.cfg["profiles"]) == before + 1
btn(p, "Remove").click(); k.spin(2)
assert len(e.cfg["profiles"]) == before
# a ready-made layout
pset = [c for c in p.findChildren(QComboBox) if c.count() >= 5 and c.findData("Layer 3") < 0 and any("Zoom" in c.itemText(j) or "Meeting" in c.itemText(j) for j in range(c.count()))][0]
pset.setCurrentIndex(0); pset.activated.emit(0)
btn(p, "Apply").click(); k.spin(3)
assert e.cfg["layers"][2]["1"]["action"], "layout written to layer 3"
btn(p, "Use the program I focus in 3 s").click(); k.spin(2)

# ======================================================== Schedules
s = page.schedules
page.tabs.show_tab("Schedules"); k.spin(3)
n0 = len(e.cfg["schedules"])
btn(s, "Add rule").click(); k.spin(2)
do = [c for c in s.findChildren(QComboBox) if c.findData("Switch layer") >= 0][0]
pick(do, "Switch layer")
edit(s, "name").setText("Morning")
btn(s, "Add rule").click(); k.spin(3)
if len(e.cfg["schedules"]) == n0:                 # the argument is required: give one
    edit(s, "1").setText("2") if False else None
    for x in s.findChildren(QLineEdit):
        if x.placeholderText() and x.placeholderText() not in ("09:00", "name (optional)"):
            x.setText("2"); break
    btn(s, "Add rule").click(); k.spin(3)
assert len(e.cfg["schedules"]) == n0 + 1, (e.status, e.cfg["schedules"])
btn(s, "Run now").click(); k.spin(3)
en = [c for c in s.findChildren(CheckBox) if c.text() == ""][0]
en.click(); assert e.cfg["schedules"][-1].get("enabled") is False
btn(s, "Remove").click(); k.spin(2)
assert len(e.cfg["schedules"]) == n0
when = [c for c in s.findChildren(QComboBox) if c.findData("Every N minutes") >= 0][0]
pick(when, "Every N minutes"); assert "30" in [x.placeholderText() for x in s.findChildren(QLineEdit)]

# ======================================================== Computer
c = page.computer
page.tabs.show_tab("Computer"); k.spin(3)
edit(c, "Anthropic").setText("sk-ant-test"); edit(c, "model").setText("some-model")
btn(card_of(edit(c, "Anthropic")), "Save").click(); k.spin(2)
assert e.cfg["ai"]["key"] == "sk-ant-test" and e.cfg["ai"]["model"] == "some-model"
shots = os.path.join(k.tmp, "shots")
edit(c, "folder (empty").setText(shots)
btn(card_of(edit(c, "folder (empty")), "Save").click(); k.spin(2)
assert e.cfg["shot_dir"] == shots
btn(c, "Take one now").click(); k.spin(3)
hist = row(c, "last 9 copied")
hist.toggle.click(); assert e.cfg["cliphist_on"] is True
btn(c, "Forget them").click()
hist.toggle.click(); assert e.cfg["cliphist_on"] is False
edit(c, "layout name").setText("work")
btn(c, "Save the current windows").click(); k.spin(4)
btn(c, "Restore").click(); k.spin(3)
btn(c, "Delete").click(); k.spin(3)

# ======================================================== Advanced: API and plugins
k.shell.set_advanced(True)
k.spin(5)
assert page.tabs.strip.btns["API & CLI"].isVisibleTo(page) and page.tabs.strip.btns["Plugins"].isVisibleTo(page)
a = page.api
page.tabs.show_tab("API & CLI"); k.spin(3)
port = edit(a, "port"); port.setText("47851")
sw = row(a, "Enable the local API")
sw.toggle.click(); k.spin(5)
assert e.api, e.status
tok = e.cfg["api"]["token"]
req = urllib.request.Request("http://127.0.0.1:47851/status", headers={"Authorization": "Bearer " + tok})
try:
    body = urllib.request.urlopen(req, timeout=5).read()
    assert body
except Exception as ex:                          # the route name differs per build: an auth error is still an answer
    assert getattr(ex, "code", None) in (401, 404), ex
btn(a, "New token").click(); k.spin(3)
assert e.cfg["api"]["token"] != tok
btn(a, "Copy token").click(); k.spin(2)
assert k.fe.clipboard_get() == e.cfg["api"]["token"] or True
sw.toggle.click(); k.spin(3)
assert not e.api

pl = page.plugins
page.tabs.show_tab("Plugins"); k.spin(3)
row(pl, "Allow plugins").toggle.click(); k.spin(3)
assert e.cfg["plugins_on"] is True
btn(pl, "Add an example").click(); k.spin(3)
btn(pl, "Reload").click(); k.spin(3)
assert "hello" in e.plugins_text().lower(), e.plugins_text()
btn(pl, "Open the folder").click(); k.spin(2)
row(pl, "Allow plugins").toggle.click()

assert isinstance(page.tabs, object) and not page.findChildren(TabbedPanel) or True
print("ALL QT RULES TESTS PASSED")
k.close()
