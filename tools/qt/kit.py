"""Helpers for the Qt widget tests: an offscreen application on the simulated pad, widget finders, a pump that runs Qt and the engine."""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_TMP = tempfile.mkdtemp(prefix="dcqt_")                      # before anything of the app is imported: CONFIG_PATH is read at import time
os.environ["HOME"] = _TMP
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(_TMP, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(_TMP, "gifs")


class QtKit:
    def __init__(self, prefix="dcqt_", cfg=None, simulate=True, size=(1300, 780)):
        self.tmp = _TMP
        if os.path.exists(os.environ["DESK_COMPANION_CONFIG"]):
            os.remove(os.environ["DESK_COMPANION_CONFIG"])
        if cfg is not None:
            import json
            with open(os.environ["DESK_COMPANION_CONFIG"], "w") as f:
                json.dump(cfg, f)
        from ui_qt.app import create
        from core.engine import NullFrontend
        self.fe = FE()
        self.app, self.e, self.shell = create(["x"], frontend=self.fe)
        self.fe.parent = None
        self.shell.show()
        self.shell.resize(*size)
        self.spin(10)
        if simulate:
            self.e.toggle_simulate()
            assert self.until(lambda: self.e.dev.connected, 20), "simulated pad did not connect"
            self.sim = self.e.dev.ser.sim
            self.spin(10)
        del NullFrontend

    def spin(self, n=10, d=0.02):
        for _ in range(n):
            self.app.processEvents()
            time.sleep(d)

    def until(self, cond, secs=8.0):
        t0 = time.time()
        while time.time() - t0 < secs:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return bool(cond())

    @property
    def status(self):
        return self.e.status[0]

    def go(self, page, section=None):
        self.shell.goto(page, section)
        self.spin(8)
        return self.shell.pages[page]

    def close(self):
        self.shell._really_quit = True
        self.shell.close()


from core.engine import NullFrontend    # noqa: E402


class FE(NullFrontend):
    """Scripted answers for dialogs."""
    save_path = ""
    open_path = ""
    open_paths = ()
    answer = True

    def confirm(self, title, text):
        self.asked.append((title, text))
        return self.answer

    def ask_save(self, title="", initial="", filters="", suffix=""):
        return self.save_path

    def ask_open(self, title="", filters=""):
        return self.open_path

    def ask_open_many(self, title="", filters=""):
        return list(self.open_paths)


def buttons(root):
    from ui_qt.widgets import RippleButton
    return [b for b in root.findChildren(RippleButton) if b.isVisibleTo(root.window()) or True]


def find_button(root, text):
    """The (visible-or-not) RippleButton whose full caption equals / starts with text."""
    from ui_qt.widgets import RippleButton
    hits = [b for b in root.findChildren(RippleButton) if b.text_full() == text]
    if not hits:
        hits = [b for b in root.findChildren(RippleButton) if b.text_full().startswith(text)]
    assert hits, f"no button {text!r}; have {sorted({b.text_full() for b in root.findChildren(RippleButton)})}"
    return hits[0]


def click(root, text):
    b = find_button(root, text)
    assert b.isEnabled(), f"button {text!r} is disabled"
    b.click()
    return b


def find_label(root, text):
    from PySide6.QtWidgets import QLabel
    hits = [l for l in root.findChildren(QLabel) if text in l.text()]
    assert hits, f"no label containing {text!r}"
    return hits[0]


def has_label(root, text):
    from PySide6.QtWidgets import QLabel
    return any(text in l.text() for l in root.findChildren(QLabel))
