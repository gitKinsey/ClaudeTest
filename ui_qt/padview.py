"""The pad twin: the device picture with live screen, five keys, a dial and its turn arrows. Click, wheel, long press, drag and drop."""
import json
import math

from PIL import Image, ImageDraw
from PySide6.QtCore import QByteArray, QMimeData, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from core.base import (ARROW_L, ARROW_R, ARROW_RAD, CAT_COLORS, DISP, ENC_C, ENC_R, KEY_EDGE, KEY_FILL, KEY_H, KEY_ON, KEY_W, KEY_XS, KEY_Y, LONG_MS,
                       SCR_C, SLOT_LABELS, VP_H, VP_W, make_body_image)
from ui_qt.theme import theme

MIME = "application/x-deskcompanion-action"


def pil_to_qimage(im):
    im = im.convert("RGBA")
    return QImage(im.tobytes(), im.width, im.height, QImage.Format.Format_RGBA8888).copy()


class PadViewQt(QWidget):
    """engine: the Engine. interactive=False draws only (used by the mini window title chip)."""
    hovered = Signal(int)

    def __init__(self, engine, parent=None, interactive=True):
        super().__init__(parent)
        self.engine, self.interactive = engine, interactive
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAcceptDrops(interactive)
        self.body = pil_to_qimage(make_body_image())
        m = Image.new("L", (DISP * 4, DISP * 4), 0)
        ImageDraw.Draw(m).ellipse([0, 0, DISP * 4 - 1, DISP * 4 - 1], fill=255)
        self._mask = m.resize((DISP, DISP), Image.LANCZOS)
        self._screen = None
        self.regions = []
        for slot, x in enumerate(KEY_XS, start=1):
            self.regions.append({"kind": "key", "slot": slot, "shape": "rect", "geom": (x - KEY_W / 2, KEY_Y - KEY_H / 2, x + KEY_W / 2, KEY_Y + KEY_H / 2)})
        for slot, (x, y), d in ((7, ARROW_L, -1), (6, ARROW_R, 1)):
            self.regions.append({"kind": "turn", "slot": slot, "dir": d, "shape": "circle", "geom": (x, y, ARROW_RAD)})
        self.regions.append({"kind": "knob", "slot": 0, "shape": "circle", "geom": (ENC_C[0], ENC_C[1], ENC_R)})
        self._flash = {}
        self._knob_deg = 0
        self._drop = None
        self._down = None
        self._press_pos = None
        self._long = QTimer(self)
        self._long.setSingleShot(True)
        self._long.timeout.connect(self._long_fire)
        e = engine
        e.on("key_flash", self.flash)
        e.on("spin", self.spin)
        for ev in ("map_changed", "pending", "edit_layer", "library"):
            e.on(ev, lambda *_: self.update())
        theme.changed.connect(self.update)
        self.setMinimumSize(QSize(theme.px(150), theme.px(171)))

    def sizeHint(self):
        return QSize(theme.px(150), theme.px(171))

    # ---- geometry
    def _xf(self):
        s = min(self.width() / VP_W, self.height() / VP_H)
        return s, (self.width() - VP_W * s) / 2, (self.height() - VP_H * s) / 2

    def to_design(self, pt):
        s, ox, oy = self._xf()
        return (pt.x() - ox) / s, (pt.y() - oy) / s

    def hit(self, x, y):
        for r in self.regions:
            g = r["geom"]
            if r["shape"] == "rect":
                if g[0] <= x <= g[2] and g[1] <= y <= g[3]:
                    return r
            elif (x - g[0]) ** 2 + (y - g[1]) ** 2 <= g[2] ** 2:
                return r
        return None

    def region_for_slot(self, slot):
        return next(r for r in self.regions if r["kind"] in ("key", "turn") and r["slot"] == slot)

    # ---- model updates
    def set_screen(self, pil_img):
        im = pil_img.convert("RGBA")
        im.putalpha(self._mask)
        self._screen = pil_to_qimage(im)
        self.update()

    def flash(self, slot):
        self._flash[slot] = True
        self.update()
        QTimer.singleShot(150, lambda: (self._flash.pop(slot, None), self.update()))

    def spin(self, steps):
        self._knob_deg = (self._knob_deg + 15 * steps) % 360
        self.update()

    def set_drop(self, slot):
        if slot != self._drop:
            self._drop = slot
            self.update()

    # ---- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s, ox, oy = self._xf()
        p.translate(ox, oy)
        p.scale(s, s)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(12, 8, VP_W - 24, VP_H - 16), 38, 38)
        p.save()
        p.setClipPath(clip)
        p.drawImage(QRectF(0, 0, VP_W, VP_H), self.body)
        p.restore()
        if self._screen is not None:
            p.drawImage(QPointF(SCR_C[0] - DISP / 2, SCR_C[1] - DISP / 2), self._screen)
        else:
            p.setBrush(QColor("#050608"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(SCR_C[0], SCR_C[1]), DISP / 2, DISP / 2)
        eng = self.engine
        pend = eng.pending_slots
        for r in self.regions:
            if r["kind"] == "key":
                self._draw_key(p, r, pend)
            elif r["kind"] == "turn":
                self._draw_turn(p, r, pend)
        self._draw_knob(p)
        if self._drop is not None:
            r = self.region_for_slot(self._drop)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(theme.c("ACCENT")), 3))
            g = r["geom"]
            if r["shape"] == "rect":
                p.drawRoundedRect(QRectF(g[0] - 4, g[1] - 4, g[2] - g[0] + 8, g[3] - g[1] + 8), 14, 14)
            else:
                p.drawEllipse(QPointF(g[0], g[1]), g[2] + 4, g[2] + 4)

    def _font(self, px, bold=True):
        f = QFont(self.font())
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    def _draw_key(self, p, r, pend):
        slot, (x0, y0, x1, y1) = r["slot"], r["geom"]
        m = self.engine.cfg["map"][str(slot)]
        un = m["cat"] == "Other"
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)
        p.setBrush(QColor(KEY_ON if self._flash.get(slot) else KEY_FILL))
        p.setPen(QPen(QColor(KEY_EDGE), 2))
        p.drawRoundedRect(rect, 12, 12)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CAT_COLORS.get(m["cat"], "#6b7280")))
        p.drawRoundedRect(QRectF(x0 + 12, y0 + 7, x1 - x0 - 24, 3), 1.5, 1.5)
        p.setFont(self._font(10))
        p.setPen(QColor("#7d8594"))
        p.drawText(QRectF(x0 + 11, y1 - 20, 40, 16), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), f"K{slot}")
        p.setFont(self._font(11))
        p.setPen(QColor("#6b7280" if un else "#e8ecf2"))
        p.drawText(QRectF(x0 + 6, y0 + 12, x1 - x0 - 12, y1 - y0 - 28), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), "-" if un else m["action"])
        if slot in pend:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ff9f1a"))
            p.drawEllipse(QPointF(x1 - 13.5, y0 + 16.5), 3.6, 3.6)

    def _draw_turn(self, p, r, pend):
        slot, (x, y, rad), d = r["slot"], r["geom"], r["dir"]
        m = self.engine.cfg["map"][str(slot)]
        p.setBrush(QColor(KEY_ON if self._flash.get(slot) else KEY_FILL))
        p.setPen(QPen(QColor(KEY_EDGE), 2))
        p.drawEllipse(QPointF(x, y), rad, rad)
        sdir = 9 * d
        path = QPainterPath(QPointF(x + sdir, y))
        path.lineTo(x - sdir * 0.6, y - 10)
        path.lineTo(x - sdir * 0.6, y + 10)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#9aa3b5"))
        p.drawPath(path)
        p.setFont(self._font(10, False))
        p.setPen(QColor("#7d8594"))
        p.drawText(QRectF(x - 55, y + rad + 3, 110, 14), int(Qt.AlignmentFlag.AlignCenter), "turn right" if d > 0 else "turn left")
        p.setFont(self._font(11))
        p.setPen(QColor("#6b7280" if m["cat"] == "Other" else "#cfd5e1"))
        p.drawText(QRectF(x - 56, y + rad + 18, 112, 32), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                   "" if m["cat"] == "Other" else m["action"])
        if slot in pend:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ff9f1a"))
            p.drawEllipse(QPointF(x + 17.5, y - 18.5), 3.6, 3.6)

    def _draw_knob(self, p):
        x, y = ENC_C
        p.setBrush(QColor("#3a3f49"))
        p.setPen(QPen(QColor("#7a8394"), 2))
        p.drawEllipse(QPointF(x, y), ENC_R, ENC_R)
        a = math.radians(self._knob_deg)
        pen = QPen(QColor(theme.c("ACCENT")), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x, y), QPointF(x + math.sin(a) * (ENC_R - 6), y - math.cos(a) * (ENC_R - 6)))
        p.setBrush(QColor("#2a2e36"))
        p.setPen(QPen(QColor("#566174"), 1))
        p.drawEllipse(QPointF(x, y), 9, 9)
        p.setFont(self._font(10, False))
        p.setPen(QColor("#7d8594"))
        p.drawText(QRectF(x - 90, y - 70, 180, 14), int(Qt.AlignmentFlag.AlignCenter), "click: menu     hold: next mode")

    # ---- mouse
    def mousePressEvent(self, e):
        if not self.interactive or e.button() != Qt.MouseButton.LeftButton:
            return
        x, y = self.to_design(e.position())
        r = self.hit(x, y)
        self._down = {"r": r, "drag": False, "long": False} if r else None
        self._press_pos = e.position()
        if r and r["kind"] == "knob":
            self._long.start(LONG_MS)

    def _long_fire(self):
        if self._down:
            self._down["long"] = True
            self.engine.pad.long_press()

    def mouseMoveEvent(self, e):
        x, y = self.to_design(e.position())
        r = self.hit(x, y)
        self.setCursor(Qt.CursorShape.PointingHandCursor if r else Qt.CursorShape.ArrowCursor)
        if not self._down or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        d = self._down["r"]
        if d["kind"] == "knob" or self._down["drag"]:
            return
        if (e.position() - self._press_pos).manhattanLength() < 8:
            return
        m = self.engine.cfg["map"][str(d["slot"])]
        if m["cat"] == "Other":
            return
        self._down["drag"] = True
        self._start_drag(d["slot"], m["action"])

    def _start_drag(self, slot, text):
        mime = QMimeData()
        mime.setData(MIME, QByteArray(json.dumps({"slot": slot}).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = ghost_pixmap(text)
        drag.setPixmap(pm)
        drag.setHotSpot(QPointF(-6, -6).toPoint())
        drag.exec(Qt.DropAction.MoveAction)
        self.set_drop(None)
        self._down = None

    def mouseReleaseEvent(self, e):
        d, self._down = self._down, None
        self._long.stop()
        if not d or d["drag"] or d["long"]:
            return
        x, y = self.to_design(e.position())
        r = self.hit(x, y)
        if r is not d["r"]:
            return
        eng = self.engine
        if r["kind"] == "key":
            eng.vp_key(r["slot"])
        elif r["kind"] == "turn":
            eng.vp_turn(r["dir"])
        else:
            eng.pad.click()

    def wheelEvent(self, e):
        x, y = self.to_design(e.position())
        r = self.hit(x, y)
        if r and r["kind"] in ("knob", "turn"):
            self.engine.vp_turn(1 if e.angleDelta().y() > 0 else -1)

    def contextMenuEvent(self, e):
        x, y = self.to_design(e.pos())
        r = self.hit(x, y)
        if r and r["kind"] in ("key", "turn"):
            self.slot_menu(r["slot"], e.globalPos())
        elif r:
            self.engine.pad.long_press()

    def slot_menu(self, slot, gpos):
        eng = self.engine
        menu = QMenu(self)
        menu.addAction(f"Test {SLOT_LABELS[slot]} now", lambda: eng.exec_slot(slot))
        menu.addAction("Clear", lambda: eng.drop_assign(slot, {"cat": "Other", "action": "Unassigned"}))
        sub = menu.addMenu("Assign")
        for cat in eng.categories():
            cm = sub.addMenu(cat)
            for n in eng.names_for(cat):
                cm.addAction(n, lambda c=cat, n=n: eng.drop_assign(slot, {"cat": c, "action": n}))
        menu.exec(gpos)

    # ---- drag and drop target
    def _slot_at(self, pos):
        x, y = self.to_design(pos)
        r = self.hit(x, y)
        return r["slot"] if r and r["kind"] in ("key", "turn") else None

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        slot = self._slot_at(e.position())
        self.set_drop(slot)
        if slot is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.set_drop(None)

    def dropEvent(self, e):
        slot = self._slot_at(e.position())
        self.set_drop(None)
        if slot is None:
            return
        payload = json.loads(bytes(e.mimeData().data(MIME)).decode())
        self.engine.drop_assign(slot, payload)
        e.acceptProposedAction()


def ghost_pixmap(text):
    f = QFont()
    f.setBold(True)
    f.setPixelSize(13)
    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(f)
    w, h = fm.horizontalAdvance(text) + 24, fm.height() + 12
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(theme.c("ACCENT_FILL")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, 0, w, h), int(Qt.AlignmentFlag.AlignCenter), text)
    p.end()
    return pm
