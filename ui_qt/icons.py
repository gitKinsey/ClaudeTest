"""Vector icons drawn in code (no image files): icon(name, color, size) -> QIcon / pixmap, and draw(name, painter, rect, color)."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _pen(p, color, w):
    pen = QPen(QColor(color), w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


def draw(name, p: QPainter, rect: QRectF, color, width=None):
    """Draw icon `name` into rect (square recommended) with stroke colour."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = min(rect.width(), rect.height())
    x0, y0 = rect.center().x() - s / 2, rect.center().y() - s / 2
    u = s / 24.0
    P = lambda x, y: QPointF(x0 + x * u, y0 + y * u)               # noqa: E731
    R = lambda x, y, w, h: QRectF(x0 + x * u, y0 + y * u, w * u, h * u)   # noqa: E731
    _pen(p, color, (width or 1.8) * u)
    fn = _ICONS.get(name, _ICONS["dot"])
    fn(p, P, R, u, QColor(color))
    p.restore()


def _home(p, P, R, u, col):
    path = QPainterPath(P(3.5, 11.5)); path.lineTo(P(12, 4)); path.lineTo(P(20.5, 11.5))
    p.drawPath(path)
    b = QPainterPath(P(6, 10)); b.lineTo(P(6, 20)); b.lineTo(P(10.5, 20)); b.lineTo(P(10.5, 14.5)); b.lineTo(P(13.5, 14.5)); b.lineTo(P(13.5, 20)); b.lineTo(P(18, 20)); b.lineTo(P(18, 10))
    p.drawPath(b)


def _keys(p, P, R, u, col):
    for i in range(3):
        p.drawRoundedRect(R(3 + i * 6.3, 5, 5, 5.5), 1.4 * u, 1.4 * u)
    p.drawRoundedRect(R(3, 13, 18, 6), 2 * u, 2 * u)
    p.drawEllipse(P(8, 16), 1.2 * u, 1.2 * u)
    p.drawEllipse(P(16, 16), 1.2 * u, 1.2 * u)


def _display(p, P, R, u, col):
    p.drawEllipse(P(12, 12), 8.5 * u, 8.5 * u)
    p.drawEllipse(P(12, 12), 4.6 * u, 4.6 * u)
    p.setBrush(col); p.drawEllipse(P(12, 12), 1.1 * u, 1.1 * u)


def _rules(p, P, R, u, col):
    p.drawEllipse(P(6, 6), 2.4 * u, 2.4 * u)
    p.drawEllipse(P(6, 18), 2.4 * u, 2.4 * u)
    p.drawEllipse(P(18, 12), 2.4 * u, 2.4 * u)
    path = QPainterPath(P(6, 8.4)); path.lineTo(P(6, 15.6)); p.drawPath(path)
    q = QPainterPath(P(6, 8.4)); q.cubicTo(P(6, 12), P(10, 12), P(15.6, 12)); p.drawPath(q)


def _scripts(p, P, R, u, col):
    a = QPainterPath(P(9, 7)); a.lineTo(P(4, 12)); a.lineTo(P(9, 17)); p.drawPath(a)
    b = QPainterPath(P(15, 7)); b.lineTo(P(20, 12)); b.lineTo(P(15, 17)); p.drawPath(b)
    c = QPainterPath(P(13.5, 5)); c.lineTo(P(10.5, 19)); p.drawPath(c)


def _padapp(p, P, R, u, col):
    for y, kx in ((6, 8), (12, 16), (18, 10)):
        l = QPainterPath(P(3.5, y)); l.lineTo(P(20.5, y)); p.drawPath(l)
        p.setBrush(QColor(0, 0, 0, 0)); p.drawEllipse(P(kx, y), 2.2 * u, 2.2 * u)


def _diag(p, P, R, u, col):
    path = QPainterPath(P(3, 13)); path.lineTo(P(7, 13)); path.lineTo(P(9.5, 6)); path.lineTo(P(13.5, 19)); path.lineTo(P(16, 11)); path.lineTo(P(21, 11))
    p.drawPath(path)


def _search(p, P, R, u, col):
    p.drawEllipse(P(10.5, 10.5), 6 * u, 6 * u)
    l = QPainterPath(P(15, 15)); l.lineTo(P(20, 20)); p.drawPath(l)


def _pop(p, P, R, u, col):
    p.drawRoundedRect(R(3.5, 6, 13, 11), 2 * u, 2 * u)
    p.drawRoundedRect(R(11.5, 12, 9, 7), 1.5 * u, 1.5 * u)


def _sun(p, P, R, u, col):
    p.drawEllipse(P(12, 12), 4 * u, 4 * u)
    for i in range(8):
        a = math.radians(i * 45)
        l = QPainterPath(P(12 + math.cos(a) * 7, 12 + math.sin(a) * 7)); l.lineTo(P(12 + math.cos(a) * 9.5, 12 + math.sin(a) * 9.5)); p.drawPath(l)


def _moon(p, P, R, u, col):
    path = QPainterPath(P(19, 14.5))
    path.cubicTo(P(12, 19), P(5, 13), P(9.5, 5)); path.cubicTo(P(7, 13), P(14, 15.5), P(19, 14.5))
    p.drawPath(path)


def _check(p, P, R, u, col):
    path = QPainterPath(P(5, 12.5)); path.lineTo(P(10, 17.5)); path.lineTo(P(19, 7)); p.drawPath(path)


def _close(p, P, R, u, col):
    for a, b in (((6, 6), (18, 18)), ((18, 6), (6, 18))):
        l = QPainterPath(P(*a)); l.lineTo(P(*b)); p.drawPath(l)


def _chev(p, P, R, u, col):
    path = QPainterPath(P(9, 6)); path.lineTo(P(15, 12)); path.lineTo(P(9, 18)); p.drawPath(path)


def _chev_down(p, P, R, u, col):
    path = QPainterPath(P(6, 9)); path.lineTo(P(12, 15)); path.lineTo(P(18, 9)); p.drawPath(path)


def _plug(p, P, R, u, col):
    p.drawRoundedRect(R(7, 3, 10, 8), 2 * u, 2 * u)
    for x in (9.5, 14.5):
        l = QPainterPath(P(x, 11)); l.lineTo(P(x, 14)); p.drawPath(l)
    path = QPainterPath(P(12, 14)); path.lineTo(P(12, 21)); p.drawPath(path)


def _warn(p, P, R, u, col):
    path = QPainterPath(P(12, 4)); path.lineTo(P(21, 19.5)); path.lineTo(P(3, 19.5)); path.closeSubpath(); p.drawPath(path)
    l = QPainterPath(P(12, 10)); l.lineTo(P(12, 14)); p.drawPath(l)
    p.setBrush(col); p.drawEllipse(P(12, 16.8), 0.8 * u, 0.8 * u)


def _upload(p, P, R, u, col):
    l = QPainterPath(P(12, 16)); l.lineTo(P(12, 4)); p.drawPath(l)
    a = QPainterPath(P(7, 9)); a.lineTo(P(12, 4)); a.lineTo(P(17, 9)); p.drawPath(a)
    b = QPainterPath(P(4, 15)); b.lineTo(P(4, 20)); b.lineTo(P(20, 20)); b.lineTo(P(20, 15)); p.drawPath(b)


def _plus(p, P, R, u, col):
    for a, b in (((12, 5), (12, 19)), ((5, 12), (19, 12))):
        l = QPainterPath(P(*a)); l.lineTo(P(*b)); p.drawPath(l)


def _wrench(p, P, R, u, col):
    path = QPainterPath(P(14.5, 5)); path.cubicTo(P(11, 4.5), P(8.5, 8), P(10.5, 11)); path.lineTo(P(4, 17.5)); path.cubicTo(P(3, 19), P(5, 21), P(6.5, 20)); path.lineTo(P(13, 13.5))
    path.cubicTo(P(16, 15.5), P(19.5, 13), P(19, 9.5)); path.lineTo(P(16.5, 12)); path.lineTo(P(13.5, 9)); path.lineTo(P(16, 6.5))
    p.drawPath(path)


def _dot(p, P, R, u, col):
    p.setBrush(col); p.drawEllipse(P(12, 12), 3 * u, 3 * u)


_ICONS = {"overview": _home, "keys": _keys, "display": _display, "rules": _rules, "scripts": _scripts, "padapp": _padapp, "diag": _diag,
          "search": _search, "pop": _pop, "sun": _sun, "moon": _moon, "check": _check, "close": _close, "chev": _chev, "chev_down": _chev_down,
          "plug": _plug, "warn": _warn, "upload": _upload, "plus": _plus, "wrench": _wrench, "dot": _dot}


def pixmap(name, color, size=24, dpr=2.0):
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw(name, p, QRectF(0, 0, size, size), color)
    p.end()
    return pm


def icon(name, color, size=24):
    return QIcon(pixmap(name, color, size))
