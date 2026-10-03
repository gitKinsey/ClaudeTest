"""Form helpers shared by the pages."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLineEdit, QListWidget, QPlainTextEdit, QSizePolicy, QWidget

from core.base import SLOT_LABELS
from ui_qt.layouts import FlowLayout, vbox
from ui_qt.theme import theme
from ui_qt.widgets import Field, flow, label, tr


def combo(items=(), current=None, width_chars=12, data=None):
    """A combo box that can shrink (its width follows the longest entry only up to width_chars)."""
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(width_chars)
    for i, it in enumerate(items):
        c.addItem(tr(it) if isinstance(it, str) else str(it), data[i] if data else it)
    if current is not None:
        i = c.findData(current)
        c.setCurrentIndex(i if i >= 0 else 0)
    return c


def val(c):
    """The untranslated value of a combo made by combo() (editable combos: the typed text)."""
    if c.isEditable():
        return c.currentText()
    d = c.currentData()
    return d if d is not None else c.currentText()


def set_val(c, value):
    i = c.findData(value)
    if i >= 0:
        c.setCurrentIndex(i)
    elif c.isEditable():
        c.setCurrentText(str(value))


def line(placeholder="", text="", width=None, password=False):
    e = QLineEdit(text)
    e.setPlaceholderText(tr(placeholder))
    if password:
        e.setEchoMode(QLineEdit.EchoMode.Password)
    if width:
        e.setMinimumWidth(theme.px(min(width, 120)))
        e.setMaximumWidth(theme.px(width))
        e.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return e


def field(caption, widget):
    return Field(caption, widget)


def clear_layout(layout):
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


class RowList(QWidget):
    """A vertical list of 'row cards' that is rebuilt from data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = vbox(self, spacing=6)

    def clear(self):
        clear_layout(self.lay)

    def add_row(self, widgets, stretch_last=False):
        """widgets: wrapped in a flow so a row wraps on narrow widths."""
        from PySide6.QtWidgets import QFrame
        f = QFrame()
        f.setObjectName("card2")
        fl = FlowLayout(f, 8, 4, (10, 8, 10, 8))
        for w in widgets:
            fl.addWidget(w)
        self.lay.addWidget(f)
        return f

    def empty(self, text):
        self.lay.addWidget(label(text, "muted", wrap=True))


class TargetKeyCombo(QWidget):
    """'Target key' selector bound to the engine's target slot."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        l = vbox(self, spacing=0)
        self.box = combo(list(SLOT_LABELS.values()), width_chars=14)
        self.box.activated.connect(lambda i: engine.set_target_slot(i + 1))
        self.wrap = flow(label("Target key", "muted"), self.box)
        l.addWidget(self.wrap)
        engine.on("target_slot", self.sync)
        self.sync(engine.target_slot)

    def sync(self, slot):
        self.box.blockSignals(True)
        self.box.setCurrentIndex(slot - 1)
        self.box.blockSignals(False)


def text_edit(placeholder="", height=64):
    t = QPlainTextEdit()
    t.setPlaceholderText(tr(placeholder))
    t.setMinimumHeight(theme.px(height))
    t.setMaximumHeight(theme.px(height + 60))
    return t


def list_widget(height=140, multi=False):
    w = QListWidget()
    w.setMinimumHeight(theme.px(height))
    if multi:
        w.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
    return w


def align_top(w):
    w.setAlignment(Qt.AlignmentFlag.AlignTop)
    return w


def cfg_switch(engine, text, key, default=False, after=None):
    """A switch bound to a boolean config key; other code can flip it through engine.set_pref (the 'pref' event)."""
    from ui_qt.widgets import ToggleRow
    t = ToggleRow(text, bool(engine.cfg.get(key, default)))

    def changed(v):
        engine.set_pref(key, bool(v))
        if after:
            after(bool(v))
    t.toggled.connect(changed)
    engine.on("pref", lambda k, v: t.setChecked(bool(v)) if k == key else None)
    return t
