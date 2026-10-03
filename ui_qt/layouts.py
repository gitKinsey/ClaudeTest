"""Responsive building blocks: FlowLayout (wraps), InnerScroll (scrolls inside a box only), PageGrid (3 columns -> 2 -> tabs, draggable splitters)."""
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLayout, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget)


class FlowLayout(QLayout):
    """Items left to right, wrapping to the next line when the width runs out (needs height-for-width from its parent layout)."""

    def __init__(self, parent=None, hspace=8, vspace=8, margins=(0, 0, 0, 0)):
        super().__init__(parent)
        self._items, self._h, self._v = [], hspace, vspace
        self.setContentsMargins(*margins)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            w = min(hint.width(), r.width())
            if x + w > r.right() + 1 and line_h > 0:
                x, y, line_h = r.x(), y + line_h + self._v, 0
            h = it.heightForWidth(w) if it.hasHeightForWidth() else hint.height()
            if not test:
                it.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x += w + self._h
            line_h = max(line_h, h)
        return y + line_h - rect.y() + m.bottom()


class _Holder(QWidget):
    """Wraps scroll content. Word-wrapped labels make a layout's *minimum* height as tall as their narrowest wrapping, which would make
    the scroll area think the content never fits; only the minimum width is reported, the height comes from height-for-width."""

    def __init__(self, content):
        super().__init__()
        self.content = content
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(content)

    def minimumSizeHint(self):
        return QSize(self.content.minimumSizeHint().width(), 0)


class InnerScroll(QScrollArea):
    """A scroll area for the inside of a card: no frame, no horizontal bar, transparent."""

    def __init__(self, content: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        if content is not None:
            self.setWidget(_Holder(content))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        return QSize(200, 120)

    def minimumSizeHint(self):
        return QSize(120, 60)


def vbox(parent=None, margins=(0, 0, 0, 0), spacing=8):
    l = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    l.setContentsMargins(*margins)
    l.setSpacing(spacing)
    return l


def hbox(parent=None, margins=(0, 0, 0, 0), spacing=8):
    l = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    l.setContentsMargins(*margins)
    l.setSpacing(spacing)
    return l


class PageGrid(QWidget):
    """Lays panels out for the page width: wide = columns (+ splitters), medium = fewer columns, narrow = one panel at a time with a tab strip.
    plans: {"wide": [[(name, weight), ...], ...columns], "medium": [...]}; order: tab order for the narrow mode."""
    modeChanged = Signal(str)

    def __init__(self, panels: dict, plans: dict, order=None, titles=None, wide_min=980, medium_min=700, tabs_factory=None, parent=None):
        super().__init__(parent)
        self.panels, self.plans, self.order = panels, plans, order or list(panels)
        self.titles = titles or {k: k for k in panels}
        self.wide_min, self.medium_min = wide_min, medium_min
        self.mode, self.current = "", self.order[0]
        self._tabs_factory = tabs_factory
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(8)
        self._tabs = None
        self._body = None
        self._splitters = []
        self._saved = {}
        self._sized = False
        self._applying = False
        self._auto = True                                    # panel sizes follow the window until the user drags a splitter
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._apply_sizes)
        for p in panels.values():
            p.setParent(self)
        self._rebuild(self._mode_for(self.width() or 1000))

    def _mode_for(self, w):
        if w >= self.wide_min and "wide" in self.plans:
            return "wide"
        if w >= self.medium_min and "medium" in self.plans:
            return "medium"
        return "narrow"

    def resizeEvent(self, e):
        super().resizeEvent(e)
        m = self._mode_for(e.size().width())
        if m != self.mode:
            self._rebuild(m)
        elif self._auto:
            self._timer.start(30)

    def _apply_sizes(self):
        """First layout of a mode: split the width in proportion to the plan's weights (unless the user dragged it before)."""
        if self.mode == "narrow" or self.width() < 50 or not self._auto or f"{self.mode}:root" in self._saved:
            return
        self._sized = True
        self._applying = True
        cols = self.plans[self.mode]
        weights = [max(w for _n, w in col) for col in cols]
        total, w = sum(weights), self.width() - 8 * (len(cols) - 1)
        for key, sp in self._splitters:
            if key == f"{self.mode}:root":
                sp.setSizes([int(w * x / total) for x in weights])
        avail = max(100, self.height() - 8)
        for ci, col in enumerate(cols):
            for key, sp in self._splitters:
                if key == f"{self.mode}:col{ci}" and f"{self.mode}:col{ci}" not in self._saved:
                    colw = int(w * weights[ci] / total)
                    nat = []
                    for name, wt in col:
                        pnl = self.panels[name]
                        nat.append(pnl.natural_height(colw) if hasattr(pnl, "natural_height") else pnl.sizeHint().height())
                    room = avail - 8 * (len(col) - 1)
                    tot = sum(nat)
                    if tot > room:                                  # not everything fits: share the height in proportion, nothing below a small minimum
                        sizes = [max(110, int(n * room / tot)) for n in nat]
                    else:                                           # spare height goes to the panels by weight
                        extra = room - tot
                        wsum = sum(wt for _n, wt in col)
                        sizes = [int(n + extra * wt / wsum) for n, (_nm, wt) in zip(nat, col)]
                    sp.setSizes(sizes)
        self._applying = False

    def minimumSizeHint(self):
        return QSize(300, 200)

    # ---- state (splitter sizes) so a layout the user dragged is remembered
    def state(self):
        out = dict(self._saved)
        for key, sp in self._splitters:
            out[key] = bytes(sp.saveState().toBase64()).decode()
        return out

    def restore(self, st):
        self._saved = dict(st or {})

    def _rebuild(self, mode):
        for key, sp in self._splitters:                       # remember what the user dragged before the structure changes
            self._saved[key] = bytes(sp.saveState().toBase64()).decode()
        self._splitters = []
        for p in self.panels.values():
            p.setParent(self)
            p.hide()
        if self._body is not None:
            self._lay.removeWidget(self._body)
            self._body.deleteLater()
            self._body = None
        if self._tabs is not None:
            self._lay.removeWidget(self._tabs)
            self._tabs.deleteLater()
            self._tabs = None
        self.mode = mode
        if mode == "narrow":
            if self._tabs_factory:
                self._tabs = self._tabs_factory([(k, self.titles[k]) for k in self.order], self.current, self._select)
                self._lay.addWidget(self._tabs)
            body = QWidget(self)
            bl = vbox(body, spacing=0)
            for k in self.order:
                bl.addWidget(self.panels[k])
            self._body = body
            self._lay.addWidget(body, 1)
            self._show_current()
        else:
            cols = self.plans[mode]
            root = QSplitter(Qt.Orientation.Horizontal, self)
            root.setChildrenCollapsible(False)
            root.setHandleWidth(8)
            for ci, col in enumerate(cols):
                if len(col) == 1:
                    w = self.panels[col[0][0]]
                    w.setParent(root)
                    root.addWidget(w)
                else:
                    sp = QSplitter(Qt.Orientation.Vertical, root)
                    sp.setChildrenCollapsible(False)
                    sp.setHandleWidth(8)
                    for name, weight in col:
                        sp.addWidget(self.panels[name])
                        sp.setStretchFactor(sp.count() - 1, weight)
                    root.addWidget(sp)
                    self._splitters.append((f"{mode}:col{ci}", sp))
                    st = self._saved.get(f"{mode}:col{ci}")
                    if st:
                        from PySide6.QtCore import QByteArray
                        sp.restoreState(QByteArray.fromBase64(st.encode()))
                root.setStretchFactor(ci, max(w for _n, w in col))
            self._splitters.append((f"{mode}:root", root))
            st = self._saved.get(f"{mode}:root")
            if st:
                from PySide6.QtCore import QByteArray
                root.restoreState(QByteArray.fromBase64(st.encode()))
            self._body = root
            self._lay.addWidget(root, 1)
            used = {n for col in cols for n, _w in col}
            for k in self.panels:
                self.panels[k].setVisible(k in used)
        self._body.show()
        self._auto = True
        for _k, sp in self._splitters:
            sp.splitterMoved.connect(self._user_dragged)
        self._timer.start(30)
        self.modeChanged.emit(mode)

    def _user_dragged(self, *_a):
        if not self._applying:
            self._auto = False

    def _select(self, key):
        self.current = key
        self._show_current()

    def _show_current(self):
        for k, p in self.panels.items():
            p.setVisible(k == self.current)

    def show_panel(self, key):
        self.current = key
        if self.mode == "narrow":
            self._show_current()
            if self._tabs is not None and hasattr(self._tabs, "set_current"):
                self._tabs.set_current(key)
