"""The component kit: cards, labels, buttons with a click ripple, animated switch / check / segmented control, pills, banners, tiles."""
from PySide6.QtCore import Property, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QFrame, QLabel, QPushButton, QSizePolicy, QWidget)

from desk_lib import i18n
from ui_qt import anim
from ui_qt.layouts import FlowLayout, InnerScroll, hbox, vbox
from ui_qt.theme import theme


def tr(text):
    return i18n.tr(text)


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def label(text="", role=None, wrap=None, selectable=False):
    if wrap is None:
        wrap = len(text) > 28                                   # long texts wrap instead of forcing the box wider
    l = QLabel(tr(text))
    if role:
        l.setProperty("role", role)
    l.setWordWrap(wrap)
    if wrap:
        l.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        l.setMinimumWidth(40)
    if selectable:
        l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return l


def set_role(l, role):
    l.setProperty("role", role)
    repolish(l)


class RippleButton(QPushButton):
    """Button with a click ripple. A caption that does not fit is elided (the full text stays as the tooltip) so a narrow box never gets pushed wider."""

    def __init__(self, text="", variant="secondary", parent=None):
        super().__init__("", parent)
        self._full = tr(text)
        super().setText(self._full)
        self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._r, self._origin = 1.0, QPointF()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def setText(self, t):
        self._full = t
        self._elide()
        self.updateGeometry()

    def text_full(self):
        return self._full

    def _pad(self):
        return theme.px(34)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        h = max(theme.px(34), fm.height() + theme.px(16))
        return QSize(fm.horizontalAdvance(self._full) + self._pad() + 8, h)

    def minimumSizeHint(self):
        return QSize(theme.px(64), self.sizeHint().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        fm = QFontMetrics(self.font())
        avail = max(10, self.width() - self._pad())
        shown = fm.elidedText(self._full, Qt.TextElideMode.ElideRight, avail) if self.width() > 0 else self._full
        if shown != QPushButton.text(self):
            super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")

    def set_variant(self, v, **props):
        self.setProperty("variant", v)
        for k, val in props.items():
            self.setProperty(k, val)
        repolish(self)

    def _get_r(self):
        return self._r

    def _set_r(self, v):
        self._r = v
        self.update()
    ripple = Property(float, _get_r, _set_r)

    def mousePressEvent(self, e):
        if self.isEnabled():
            self._origin = QPointF(e.position())
            self._r = 0.0
            anim.run(self, b"ripple", 0.0, 1.0, 380)
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._r < 1.0 and self.isEnabled():
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(self.rect()), theme.px(8), theme.px(8))
            p.setClipPath(clip)
            col = QColor("#ffffff")
            col.setAlphaF(0.28 * (1.0 - self._r))
            p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            rad = max(self.width(), self.height()) * 1.1 * self._r
            p.drawEllipse(self._origin, rad, rad)


def button(text, variant="secondary", cb=None, tip=None):
    b = RippleButton(text, variant)
    if cb:
        b.clicked.connect(lambda *_: cb())
    if tip:
        b.setToolTip(tip)
    return b


class Toggle(QAbstractButton):
    """An animated on / off switch."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._pos = 1.0 if checked else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toggled.connect(self._animate)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _get(self):
        return self._pos

    def _set(self, v):
        self._pos = v
        self.update()
    pos_ = Property(float, _get, _set)

    def _animate(self, on):
        anim.run(self, b"pos_", self._pos, 1.0 if on else 0.0, 140)

    def setChecked(self, on):
        super().setChecked(on)
        if theme.reduce_motion or not self.isVisible():
            self._pos = 1.0 if on else 0.0
            self.update()

    def sizeHint(self):
        return QSize(theme.px(40), theme.px(22))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        off, on = QColor(theme.c("CARD3")), QColor(theme.c("ACCENT_FILL"))
        t = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t), int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            col.setAlphaF(0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        r = h / 2 - 3
        cx = h / 2 + (w - h) * t
        p.setBrush(QColor("#ffffff") if self.isEnabled() else QColor(theme.c("FAINT")))
        p.drawEllipse(QPointF(cx, h / 2), r, r)
        if self.hasFocus():
            pen = QPen(QColor(theme.c("ACCENT")), 2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), h / 2, h / 2)


class ToggleRow(QWidget):
    """[switch] text that wraps. Emits toggled(bool)."""
    toggled = Signal(bool)

    def __init__(self, text, checked=False, parent=None, hint=None):
        super().__init__(parent)
        l = hbox(self, spacing=10)
        self.toggle = Toggle(checked)
        self.text = label(text, wrap=True)
        l.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)
        l.addWidget(self.text, 1)
        self.toggle.toggled.connect(self.toggled)
        self.text.mousePressEvent = lambda e: self.toggle.click() if self.toggle.isEnabled() else None
        if hint:
            self.setToolTip(hint)

    def isChecked(self):
        return self.toggle.isChecked()

    def setChecked(self, on):
        self.toggle.blockSignals(True)
        self.toggle.setChecked(bool(on))
        self.toggle._pos = 1.0 if on else 0.0
        self.toggle.update()
        self.toggle.blockSignals(False)

    def setEnabled(self, on):
        super().setEnabled(on)
        self.text.setProperty("role", None if on else "faint")
        repolish(self.text)

    def setText(self, t):
        self.text.setText(tr(t))


class CheckBox(QCheckBox):
    """A checkbox with a drawn tick (the style sheet cannot draw one). Long texts wrap onto more lines."""

    def __init__(self, text="", checked=False, parent=None):
        super().__init__(tr(text), parent)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        pol = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)

    def _text_rect_w(self, width):
        return max(30, width - theme.px(18) - 10)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(theme.px(26) + fm.horizontalAdvance(self.text()) + 6, max(theme.px(22), fm.height() + 6))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        fm = QFontMetrics(self.font())
        r = fm.boundingRect(0, 0, self._text_rect_w(w), 10000, int(Qt.TextFlag.TextWordWrap), self.text())
        return max(theme.px(22), r.height() + 6)

    def minimumSizeHint(self):
        return QSize(min(self.sizeHint().width(), theme.px(150)), max(theme.px(22), QFontMetrics(self.font()).height() + 6))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = theme.px(18)
        r = QRectF(1, (self.height() - s) / 2, s, s)
        on = self.isChecked()
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.c("ACCENT_FILL")) if self.isEnabled() else QColor(theme.c("CARD3")))
        else:
            p.setPen(QPen(QColor(theme.c("FAINT")), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 5, 5)
        if on:
            p.setPen(QPen(QColor("#ffffff"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath(QPointF(r.x() + s * 0.26, r.center().y()))
            path.lineTo(r.x() + s * 0.44, r.center().y() + s * 0.2)
            path.lineTo(r.x() + s * 0.76, r.center().y() - s * 0.22)
            p.drawPath(path)
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.c("ACCENT")), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 6, 6)
        p.setPen(QColor(theme.c("TEXT") if self.isEnabled() else theme.c("FAINT")))
        p.drawText(QRectF(s + 10, 0, self.width() - s - 10, self.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap), self.text())


class Segmented(QWidget):
    """Segmented control with a highlight that slides to the chosen segment."""
    valueChanged = Signal(str)

    def __init__(self, values, current=None, parent=None, keys=None):
        super().__init__(parent)
        self.values = list(values)
        self.keys = list(keys) if keys else list(values)
        self.idx = max(0, self.keys.index(current)) if current in self.keys else 0
        self._hl = float(self.idx)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _get(self):
        return self._hl

    def _set(self, v):
        self._hl = v
        self.update()
    hl = Property(float, _get, _set)

    def current(self):
        return self.keys[self.idx]

    def set_current(self, key, emit=False):
        if key in self.keys:
            i = self.keys.index(key)
            if i != self.idx:
                anim.run(self, b"hl", self._hl, float(i), 160)
                self.idx = i
                if emit:
                    self.valueChanged.emit(key)

    def _seg_w(self):
        fm = QFontMetrics(self.font())
        return max(fm.horizontalAdvance(tr(v)) for v in self.values) + theme.px(26)

    def sizeHint(self):
        return QSize(self._seg_w() * len(self.values) + 6, theme.px(34))

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(sum(fm.horizontalAdvance(tr(v)) + theme.px(18) for v in self.values) + 6, theme.px(34))

    def mousePressEvent(self, e):
        n = len(self.values)
        i = max(0, min(n - 1, int((e.position().x() - 3) / max(1, (self.width() - 6) / n))))
        self.set_current(self.keys[i], emit=True)

    def keyPressEvent(self, e):
        d = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(e.key())
        if d:
            self.set_current(self.keys[max(0, min(len(self.keys) - 1, self.idx + d))], emit=True)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.values)
        w, h = self.width() - 6, self.height() - 6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.c("CARD2")))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), theme.px(9), theme.px(9))
        sw = w / n
        p.setBrush(QColor(theme.c("ACCENT_FILL")))
        p.drawRoundedRect(QRectF(3 + sw * self._hl, 3, sw, h), theme.px(7), theme.px(7))
        for i, v in enumerate(self.values):
            sel = abs(self._hl - i) < 0.5
            p.setPen(QColor("#ffffff") if sel else QColor(theme.c("MUTED")))
            f = p.font()
            f.setBold(sel)
            p.setFont(f)
            p.drawText(QRectF(3 + sw * i, 3, sw, h), int(Qt.AlignmentFlag.AlignCenter), tr(v))
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.c("ACCENT")), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), theme.px(9), theme.px(9))


class Pill(QWidget):
    """A small status chip: coloured dot + text."""

    def __init__(self, text="", kind="muted", parent=None):
        super().__init__(parent)
        self._text, self._kind = text, kind
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    compact = False

    def set(self, text, kind):
        self._text, self._kind = text, kind
        self.setToolTip(tr(text))
        self.updateGeometry()
        self.update()

    def set_compact(self, on):
        self.compact = on
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        if self.compact:
            return QSize(theme.px(28), theme.px(28))
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(tr(self._text)) + theme.px(34), theme.px(28))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(theme.kind_color(self._kind))
        bg = QColor(col)
        bg.setAlphaF(0.14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
        p.setBrush(col)
        p.drawEllipse(QPointF(theme.px(13) if not self.compact else self.width() / 2, self.height() / 2), 4, 4)
        if self.compact:
            return
        p.setPen(col)
        p.drawText(QRectF(theme.px(24), 0, self.width() - theme.px(28), self.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), tr(self._text))


class Card(QFrame):
    """A titled box. With scroll=True its content scrolls inside the box when it does not fit (never the page)."""

    def __init__(self, title="", subtitle="", scroll=True, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.title = title
        outer = vbox(self, margins=(14, 12, 14, 12), spacing=6)
        self.head = hbox(spacing=8)
        self.title_lbl = label(title, "h2", wrap=True)
        self.head.addWidget(self.title_lbl, 1)
        outer.addLayout(self.head)
        self.sub_lbl = None
        if subtitle:
            self.sub_lbl = label(subtitle, "muted", wrap=True)
            outer.addWidget(self.sub_lbl)
        self.content = QWidget()
        self.body = vbox(self.content, margins=(0, 4, 0, 0), spacing=10)
        if scroll:
            self.scroller = InnerScroll(self.content)
            outer.addWidget(self.scroller, 1)
        else:
            self.scroller = None
            outer.addWidget(self.content, 1)
        if not title:
            self.title_lbl.hide()
        self.setMinimumHeight(theme.px(90))

    def min_content_width(self):
        """Narrowest width at which nothing inside is cut off (the box itself scrolls vertically only)."""
        return self.content.minimumSizeHint().width() + 28

    def natural_height(self, width):
        """Height this card needs to show everything at the given width (used to share a column's height sensibly)."""
        inner = max(50, width - 28)
        h = 24 + 6
        if self.title_lbl.isVisibleTo(self):
            h += self.title_lbl.heightForWidth(inner) + 6
        if self.sub_lbl is not None:
            h += self.sub_lbl.heightForWidth(inner) + 6
        h += (self.content.heightForWidth(inner) if self.content.hasHeightForWidth() else self.content.sizeHint().height()) + 4
        return h

    def add(self, w, stretch=0):
        if isinstance(w, QWidget):
            self.body.addWidget(w, stretch)
        else:
            self.body.addLayout(w, stretch)
        return w

    def add_action(self, w):
        self.head.addWidget(w, 0, Qt.AlignmentFlag.AlignRight)
        return w

    def stretch(self):
        self.body.addStretch(1)


class Banner(QFrame):
    """A coloured notice with optional action button."""

    def __init__(self, kind="warn", parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.setProperty("kind", kind)
        l = hbox(self, margins=(14, 10, 12, 10), spacing=12)
        self.title = label("", "h3", wrap=True)
        self.text = label("", wrap=True)
        col = vbox(spacing=2)
        col.addWidget(self.title)
        col.addWidget(self.text)
        l.addLayout(col, 1)
        self.btn = button("", "white")
        self._cb = None
        l.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title.hide()
        self.btn.hide()
        self.hide()

    def show_text(self, text, title="", action=None, cb=None):
        self.text.setText(text)
        self.title.setText(title)
        self.title.setVisible(bool(title))
        self.btn.setVisible(bool(action))
        if action:
            self.btn.setText(tr(action))
            if self._cb is not None:
                try:
                    self.btn.clicked.disconnect(self._cb)
                except (RuntimeError, TypeError):
                    pass
            self._cb = lambda *_: cb()
            self.btn.clicked.connect(self._cb)
        self.show()


class Tile(QFrame):
    """A small stat tile: caption + value."""

    def __init__(self, caption, parent=None):
        super().__init__(parent)
        self.setObjectName("tile")
        l = vbox(self, margins=(12, 8, 12, 8), spacing=2)
        self.cap = label(caption, "muted")
        self.val = label("-", "h2")
        l.addWidget(self.cap)
        l.addWidget(self.val)
        self.setMinimumWidth(theme.px(110))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set(self, text, kind="text"):
        self.val.setText(text)
        self.val.setStyleSheet(f"color: {theme.kind_color(kind)};")


class Bar(QWidget):
    """A thin progress bar with an animated value (0..1)."""

    def __init__(self, parent=None, height=8):
        super().__init__(parent)
        self._v, self._shown, self._h = 0.0, 0.0, height
        self.setFixedHeight(theme.px(height))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._anim = None

    def _get(self):
        return self._shown

    def _set(self, v):
        self._shown = v
        self.update()
    shown = Property(float, _get, _set)

    def set(self, v, animate=True):
        v = max(0.0, min(1.0, float(v)))
        if animate and not theme.reduce_motion:
            self._anim = anim.run(self, b"shown", self._shown, v, 220)
        else:
            self._shown = v
            self.update()
        self._v = v

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.c("CARD3")))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if self._shown > 0:
            p.setBrush(QColor(theme.c("ACCENT")))
            p.drawRoundedRect(QRectF(0, 0, max(r.height(), r.width() * self._shown), r.height()), r.height() / 2, r.height() / 2)


class Field(QWidget):
    """A caption above a control; used in flow rows so fields wrap on narrow widths."""

    def __init__(self, caption, widget, parent=None):
        super().__init__(parent)
        l = vbox(self, spacing=3)
        self.cap = label(caption, "muted")
        l.addWidget(self.cap)
        l.addWidget(widget)
        self.widget = widget


def flow(*widgets, hspace=8, vspace=8):
    """A wrapping row of widgets."""
    w = QWidget()
    fl = FlowLayout(w, hspace, vspace)
    for x in widgets:
        fl.addWidget(x)
    return w


def separator():
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.c('LINE')};")
    return f


class IconButton(QPushButton):
    """A ghost button that shows a vector icon (re-coloured with the theme)."""

    def __init__(self, name, tip="", cb=None, parent=None):
        super().__init__("", parent)
        self.name = name
        self.setProperty("variant", "ghost")
        self.setToolTip(tr(tip))
        self.setAccessibleName(tr(tip))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(theme.px(36), theme.px(34))
        if cb:
            self.clicked.connect(lambda *_: cb())

    def set_icon_name(self, name):
        self.name = name
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        from ui_qt import icons
        p = QPainter(self)
        col = QColor(theme.c("TEXT") if self.underMouse() else theme.c("MUTED"))
        s = theme.px(20)
        icons.draw(self.name, p, QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s), col)


class TabStrip(QWidget):
    """Chip tabs that wrap onto a second line when the width runs out (no horizontal scrolling)."""

    def __init__(self, items, current, cb, parent=None):
        super().__init__(parent)
        self.cb, self.btns, self.cur = cb, {}, current
        fl = FlowLayout(self, 6, 6)
        for key, title in items:
            b = RippleButton(title, "primary" if key == current else "ghost")
            b.clicked.connect(lambda _=False, k=key: self.pick(k))
            fl.addWidget(b)
            self.btns[key] = b

    def set_current(self, key):
        self.cur = key
        for k, b in self.btns.items():
            b.set_variant("primary" if k == key else "ghost")

    def pick(self, key):
        self.set_current(key)
        self.cb(key)


class TabbedPanel(Card):
    """A card with chip tabs; every tab's content scrolls inside the card when it does not fit."""

    def __init__(self, title, tabs: dict, current=None, parent=None):
        super().__init__(title, scroll=False, parent=parent)
        from PySide6.QtWidgets import QStackedWidget
        self.tabs = tabs
        self.keys = list(tabs)
        self.stackw = QStackedWidget()
        self.scrolls = {}
        for k, w in tabs.items():
            sc = InnerScroll(w)
            self.scrolls[k] = sc
            self.stackw.addWidget(sc)
        self.strip = TabStrip([(k, k) for k in tabs], current or self.keys[0], self.show_tab)
        self.body.addWidget(self.strip)
        self.body.addWidget(self.stackw, 1)
        self.show_tab(current or self.keys[0])

    def min_content_width(self):
        return max(sc.widget().minimumSizeHint().width() for sc in self.scrolls.values()) + 28

    def show_tab(self, key):
        if key in self.scrolls:
            self.stackw.setCurrentWidget(self.scrolls[key])
            self.strip.set_current(key)

    def set_tab_visible(self, key, on):
        b = self.strip.btns.get(key)
        if b is not None:
            b.setVisible(on)
        if not on and self.stackw.currentWidget() is self.scrolls.get(key):
            first = next((k for k in self.keys if self.strip.btns[k].isVisible()), self.keys[0])
            self.show_tab(first)
