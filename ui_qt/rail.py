"""The navigation rail: icons + labels, a highlight that slides to the chosen page, hover glow, click ripple, badges.
Three shapes: full (icon + label), icons (narrow), top (a horizontal bar for very narrow windows)."""
from PySide6.QtCore import Property, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui_qt import anim, icons
from ui_qt.theme import theme
from ui_qt.widgets import tr


class Rail(QWidget):
    selected = Signal(str)

    def __init__(self, items, parent=None):
        """items: [(id, label, icon_name)]"""
        super().__init__(parent)
        self.items = [dict(id=i, label=l, icon=ic, badge=None, hover=0.0, ripple=1.0, origin=QPointF()) for i, l, ic in items]
        self.mode = "full"
        self.cur = 0
        self._pos = 0.0                                      # fractional index of the sliding highlight
        self._hover_idx = -1
        self._anims = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setAccessibleName("Navigation")
        self._apply_size()

    def set_items(self, items):
        self.items = [dict(id=i, label=l, icon=ic, badge=None, hover=0.0, ripple=1.0, origin=QPointF()) for i, l, ic in items]
        self._all = list(self.items)
        self.cur, self._pos = 0, 0.0
        self._apply_size()
        self.update()

    # ---- sizing
    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self._apply_size()
            self.update()

    def item_h(self):
        return theme.px(46)

    def _apply_size(self):
        n = len(self.items)
        if self.mode == "top":
            self.setMinimumSize(theme.px(54) * n, theme.px(50))
            self.setMaximumSize(16777215, theme.px(50))
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        else:
            w = theme.px(188) if self.mode == "full" else theme.px(60)
            self.setMinimumSize(w, self.item_h() * n + theme.px(8))
            self.setMaximumSize(w, 16777215)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.updateGeometry()

    def sizeHint(self):
        return self.minimumSize()

    def item_rect(self, i, pos=None):
        pos = float(i) if pos is None else pos
        if self.mode == "top":
            w = self.width() / len(self.items)
            return QRectF(w * pos, 0, w, self.height())
        h = self.item_h()
        return QRectF(theme.px(6), theme.px(4) + h * pos, self.width() - theme.px(12), h - theme.px(4))

    # ---- state
    def _get_pos(self):
        return self._pos

    def _set_pos(self, v):
        self._pos = v
        self.update()
    slide = Property(float, _get_pos, _set_pos)

    def set_current(self, ident, animate=True):
        i = next((k for k, it in enumerate(self.items) if it["id"] == ident), None)
        if i is None or i == self.cur and self._pos == float(i):
            return
        self.cur = i
        if animate:
            anim.run(self, b"slide", self._pos, float(i), 220)
        else:
            self._pos = float(i)
            self.update()

    def current_id(self):
        return self.items[self.cur]["id"]

    def set_badge(self, ident, value):
        for it in self.items:
            if it["id"] == ident and it["badge"] != value:
                it["badge"] = value
                self.update()

    def set_visible_ids(self, ids):
        """Hide pages (Advanced switch): the rail is rebuilt with only these ids."""
        keep = [it for it in self._all if it["id"] in ids]
        cur_id = self.current_id() if self.items else None
        self.items = keep
        self.cur = next((k for k, it in enumerate(self.items) if it["id"] == cur_id), 0)
        self._pos = float(self.cur)
        self._apply_size()
        self.update()

    # ---- events
    def _index_at(self, pt):
        for i in range(len(self.items)):
            if self.item_rect(i).contains(pt):
                return i
        return -1

    def _set_hover(self, i):
        if i == self._hover_idx:
            return
        for k, it in enumerate(self.items):
            want = 1.0 if k == i else 0.0
            if it["hover"] != want:
                self._anim_hover(k, want)
        self._hover_idx = i

    def _anim_hover(self, k, want):
        it = self.items[k]

        def step(v, it=it):
            it["hover"] = v
            self.update()
        old = self._anims.get(("h", k))
        if old is not None:
            try:
                old.stop()
            except RuntimeError:
                pass
        self._anims[("h", k)] = anim.value(self, it["hover"], want, 140, step)

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position())
        self._set_hover(i)
        if i >= 0 and self.mode != "full":
            self.setToolTip(tr(self.items[i]["label"]))
        else:
            self.setToolTip("")

    def leaveEvent(self, e):
        self._set_hover(-1)

    def mousePressEvent(self, e):
        i = self._index_at(e.position())
        if i < 0:
            return
        it = self.items[i]
        it["origin"] = e.position()

        def step(v, it=it):
            it["ripple"] = v
            self.update()
        it["ripple"] = 0.0
        self._anims[("r", i)] = anim.value(self, 0.0, 1.0, 420, step)
        self.set_current(it["id"])
        self.selected.emit(it["id"])

    def keyPressEvent(self, e):
        d = {Qt.Key.Key_Up: -1, Qt.Key.Key_Left: -1, Qt.Key.Key_Down: 1, Qt.Key.Key_Right: 1}.get(e.key())
        if d:
            j = max(0, min(len(self.items) - 1, self.cur + d))
            self.set_current(self.items[j]["id"])
            self.selected.emit(self.items[j]["id"])
        else:
            super().keyPressEvent(e)

    # ---- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.items)
        if not n:
            return
        rad = theme.px(10)
        hl = self.item_rect(0, self._pos)
        acc = QColor(theme.c("ACCENT"))
        bg = QColor(acc)
        bg.setAlphaF(0.13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(hl, rad, rad)
        bar = QColor(acc)
        p.setBrush(bar)
        if self.mode == "top":
            p.drawRoundedRect(QRectF(hl.x() + hl.width() * 0.25, self.height() - 4, hl.width() * 0.5, 3), 1.5, 1.5)
        else:
            p.drawRoundedRect(QRectF(hl.x(), hl.y() + hl.height() * 0.22, 3.5, hl.height() * 0.56), 1.75, 1.75)
        fm = QFontMetrics(self.font())
        base_font = self.font()
        isz = theme.px(22)
        for i, it in enumerate(self.items):
            p.setFont(base_font)
            r = self.item_rect(i)
            sel = abs(self._pos - i) < 0.5
            if it["hover"] > 0.01 and not sel:
                glow = QColor(theme.c("CARD3"))
                glow.setAlphaF(0.9 * it["hover"])
                p.setBrush(glow)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(r, rad, rad)
            if it["ripple"] < 1.0:
                p.save()
                clip = QPainterPath()
                clip.addRoundedRect(r, rad, rad)
                p.setClipPath(clip)
                rc = QColor(acc)
                rc.setAlphaF(0.30 * (1 - it["ripple"]))
                p.setBrush(rc)
                p.setPen(Qt.PenStyle.NoPen)
                rr = max(r.width(), r.height()) * 1.2 * it["ripple"]
                p.drawEllipse(it["origin"], rr, rr)
                p.restore()
            if sel:
                col = QColor(theme.c("ACCENT"))
            else:
                a, b = QColor(theme.c("MUTED")), QColor(theme.c("TEXT"))
                t = it["hover"]
                col = QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t), int(a.blue() + (b.blue() - a.blue()) * t))
            if self.mode == "full":
                ir = QRectF(r.x() + theme.px(14), r.center().y() - isz / 2, isz, isz)
            else:
                ir = QRectF(r.center().x() - isz / 2, r.center().y() - isz / 2, isz, isz)
            icons.draw(it["icon"], p, ir, col)
            if self.mode == "full":
                p.setPen(col if sel else QColor(theme.c("TEXT") if it["hover"] > 0.5 else theme.c("MUTED")))
                f = p.font()
                f.setBold(sel)
                p.setFont(f)
                p.drawText(QRectF(ir.right() + theme.px(12), r.y(), r.right() - ir.right() - theme.px(16), r.height()),
                           int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), fm.elidedText(tr(it["label"]), Qt.TextElideMode.ElideRight, int(r.width() - isz - theme.px(34))))
            b = it["badge"]
            if b:
                bc = QColor(theme.c("WARN") if b is True else theme.c("ACCENT"))
                if self.mode == "full" and b is not True:                    # a counter sits at the right end of the row, not on top of the label
                    cx, cy = r.right() - theme.px(30), r.center().y()
                else:
                    cx, cy = ir.right() + theme.px(3), ir.y() + theme.px(2)
                p.setPen(Qt.PenStyle.NoPen)
                if b is True:
                    p.setBrush(QColor(theme.c("SIDE")))
                    p.drawEllipse(QPointF(cx, cy), theme.px(7), theme.px(7))
                    p.setBrush(bc)
                    p.drawEllipse(QPointF(cx, cy), theme.px(4.5), theme.px(4.5))
                else:
                    txt = str(b) if int(b) < 100 else "99+"
                    f = p.font()
                    f.setPixelSize(theme.px(10))
                    f.setBold(True)
                    p.setFont(f)
                    tw = QFontMetrics(f).horizontalAdvance(txt) + theme.px(8)
                    pill = QRectF(cx - tw / 2 + theme.px(4), cy - theme.px(8), max(tw, theme.px(16)), theme.px(16))
                    p.setBrush(QColor(theme.c("SIDE")))
                    p.drawRoundedRect(pill.adjusted(-2, -2, 2, 2), theme.px(9), theme.px(9))
                    p.setBrush(bc)
                    p.drawRoundedRect(pill, theme.px(8), theme.px(8))
                    p.setPen(QColor("#ffffff") if not theme.dark else QColor("#0b0b0d"))
                    p.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), txt)
        if self.hasFocus():
            r = self.item_rect(self.cur)
            p.setPen(QPen(QColor(theme.c("ACCENT")), 1.5, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad, rad)
