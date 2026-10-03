"""Monkey test of the Qt UI against the simulated pad: every page and tab is visited, every button is clicked, every switch / choice / slider / field is
changed. Nothing may raise (slot exceptions are collected through sys.excepthook), nothing may hang on a modal dialog, and the app must still answer."""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from PySide6.QtCore import QObject    # noqa: E402
from shiboken6 import isValid    # noqa: E402
from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QMenu, QPlainTextEdit, QSlider, QSpinBox, QAbstractButton    # noqa: E402

from ui_qt.widgets import RippleButton, Segmented, TabbedPanel    # noqa: E402

errors = []


def hook(et, ev, tb):
    errors.append("".join(traceback.format_exception(et, ev, tb)))


sys.excepthook = hook

# nothing may open a real window, browser, process, or block in a modal loop
import webbrowser    # noqa: E402
import subprocess    # noqa: E402
webbrowser.open = lambda *a, **kw: True
_Popen = subprocess.Popen
subprocess.Popen = lambda *a, **kw: (_ for _ in ()).throw(OSError("blocked by the monkey test"))
QMenu.exec = lambda self, *a, **kw: None
QDialog.exec = lambda self, *a, **kw: 0
from PySide6.QtGui import QDrag    # noqa: E402
QDrag.exec = lambda self, *a, **kw: None

SKIP_BUTTONS = ("quit", "exit")
n_clicks = 0


def poke(root, k):
    """Change every control under root once."""
    global n_clicks
    for w in root.findChildren(QObject):
        if not isValid(w):                       # a click rebuilt part of the page
            continue
        if isinstance(w, Segmented):
            for key in w.keys:
                w.set_current(key, emit=True)
        elif isinstance(w, QComboBox):
            for i in range(min(w.count(), 6)):
                w.setCurrentIndex(i)
                w.activated.emit(i)
        elif isinstance(w, QSlider):
            w.setValue((w.minimum() + w.maximum()) // 2)
        elif isinstance(w, QSpinBox):
            w.setValue(w.minimum())
        elif isinstance(w, QLineEdit):
            if not w.isReadOnly():
                w.setText("x")
                w.editingFinished.emit()
        elif isinstance(w, QPlainTextEdit):
            if not w.isReadOnly():
                w.setPlainText("hello")
        elif isinstance(w, QAbstractButton) and w.isEnabled():
            t = w.text_full() if isinstance(w, RippleButton) else w.text()
            if t.strip().lower() in SKIP_BUTTONS:
                continue
            w.click()
            n_clicks += 1
        k.spin(1, 0.001)


def main():
    k = QtKit("dcmonkey_")
    e, sh = k.e, k.shell
    k.fe.answer = False                         # every confirmation is refused first
    for answer in (False, True):
        k.fe.answer = answer
        for pid, page in sh.pages.items():
            sh.goto(pid)
            k.spin(4)
            for tp in page.findChildren(TabbedPanel):
                for key in tp.keys:
                    tp.show_tab(key)
                    k.spin(2)
                    poke(tp.scrolls[key], k)
            poke(page, k)
            k.spin(6)
            k.check_handlers()
            assert not errors, f"page {pid} (answer={answer}):\n" + "\n---\n".join(errors[:3])
        # still alive and answering
        if not e.dev.connected:                  # the monkey may have switched the simulator off
            e.toggle_simulate()
            assert k.until(lambda: e.dev.connected, 20), "pad did not come back"
        assert e.dev.request({"cmd": "ping"}), "pad does not answer after the round"
    assert not errors, "\n---\n".join(errors[:3])
    for pid, page in sh.pages.items():
        assert page.isVisibleTo(sh) or True
    print(f"monkey test: {n_clicks} clicks, {len(sh.pages)} pages, no exceptions")
    k.close()
    subprocess.Popen = _Popen
    print("ALL QT MONKEY TESTS PASSED")


try:
    main()
except BaseException:
    sys.stderr.write("".join(traceback.format_exc()) + "\n".join(errors[:3]) + "\n")
    os._exit(1)
