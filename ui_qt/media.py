"""Picture widgets: animated round GIF preview, tile grid for GIF thumbnails, plain image view, and the tab area."""
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QSizePolicy, QStackedWidget, QWidget

from ui_qt.layouts import FlowLayout, vbox
from ui_qt.padview import pil_to_qimage
from ui_qt.theme import theme
from ui_qt.widgets import TabStrip, tr


def pil_to_pixmap(im):
    return QPixmap.fromImage(pil_to_qimage(im))


class GifPreview(QWidget):
    """Round preview of the processed GIF; animates with the GIF's own frame times."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frames, self.durs, self.i = [], [], 0
        self._imgs = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(theme.px(120), theme.px(120))

    def sizeHint(self):
        return QSize(theme.px(240), theme.px(240))

    def set_frames(self, frames, durs):
        self.frames, self.durs, self.i = list(frames or []), list(durs or []), 0
        self._imgs = [pil_to_qimage(f) for f in self.frames]
        self._timer.stop()
        if self._imgs:
            self._timer.start(max(20, self.durs[0]))
        self.update()

    def _next(self):
        if not self._imgs:
            return
        self.i = (self.i + 1) % len(self._imgs)
        self.update()
        self._timer.start(max(20, self.durs[self.i % len(self.durs)]))

    def hideEvent(self, e):
        self._timer.stop()

    def showEvent(self, e):
        if self._imgs:
            self._timer.start(40)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = min(self.width(), self.height()) - 6
        r = QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s)
        path = QPainterPath()
        path.addEllipse(r)
        p.setClipPath(path)
        p.fillRect(r, QColor("#151515"))
        if self._imgs:
            p.drawImage(r, self._imgs[self.i % len(self._imgs)])
        else:
            p.setPen(QColor(theme.c("MUTED")))
            p.drawText(r.adjusted(s * 0.15, 0, -s * 0.15, 0), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), tr("Pick a tile on the left, or choose your own GIF"))
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.c("ACCENT")), 2))
        p.drawEllipse(r)


class ImageView(QWidget):
    """Shows a PIL image scaled to fit (keeps the aspect ratio)."""

    def __init__(self, size=240, parent=None):
        super().__init__(parent)
        self._img, self._size = None, size
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(theme.px(100), theme.px(100))

    def sizeHint(self):
        return QSize(theme.px(self._size), theme.px(self._size))

    def set_image(self, pil_img):
        self._img = pil_to_qimage(pil_img)
        self.update()

    def clear(self):
        self._img = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = min(self.width(), self.height())
        r = QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s)
        p.fillRect(r, QColor("#111111"))
        if self._img is not None:
            p.drawImage(r, self._img)
        p.setPen(QPen(QColor(theme.c("LINE")), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))


class TileButton(QPushButton):
    """A thumbnail with a caption underneath."""
    rightClicked = Signal(object)

    def __init__(self, text, pixmap=None, parent=None):
        super().__init__(parent)
        self.setProperty("variant", "tile")
        self._text, self._pm = (text or "")[:40], pixmap
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(theme.px(100), theme.px(96))
        self.setToolTip(text or "")

    def set_pixmap(self, pm):
        self._pm = pm
        self.update()

    def contextMenuEvent(self, e):
        self.rightClicked.emit(e.globalPos())

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = theme.px(56)
        if self._pm is not None:
            p.drawPixmap(QRectF((self.width() - s) / 2, theme.px(8), s, s), self._pm, QRectF(self._pm.rect()))
        else:
            p.setPen(QPen(QColor(theme.c("FAINT")), 1, Qt.PenStyle.DotLine))
            p.drawEllipse(QRectF((self.width() - s) / 2, theme.px(8), s, s))
        p.setPen(QColor(theme.c("TEXT")))
        fm = p.fontMetrics()
        p.drawText(QRectF(4, theme.px(68), self.width() - 8, theme.px(22)), int(Qt.AlignmentFlag.AlignCenter),
                   fm.elidedText(self._text, Qt.TextElideMode.ElideRight, self.width() - 10))


class TileGrid(QWidget):
    """Thumbnail tiles that wrap to the width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fl = FlowLayout(self, 6, 6)
        self.tiles = []

    def clear(self):
        for t in self.tiles:
            t.setParent(None)
            t.deleteLater()
        self.tiles = []

    def add(self, text, pixmap, cb, menu_cb=None):
        t = TileButton(text, pixmap)
        t.clicked.connect(lambda *_: cb())
        if menu_cb:
            t.rightClicked.connect(menu_cb)
        self.fl.addWidget(t)
        self.tiles.append(t)
        return t


class TabArea(QWidget):
    """A chip tab strip above a stack of widgets (each one is a PageGrid of cards or a plain widget)."""
    changed = Signal(str)

    def __init__(self, tabs: dict, current=None, parent=None):
        super().__init__(parent)
        self.keys = list(tabs)
        l = vbox(self, spacing=8)
        self.strip = TabStrip([(k, k) for k in tabs], current or self.keys[0], self.show_tab)
        self.stackw = QStackedWidget()
        self.widgets = tabs
        for w in tabs.values():
            self.stackw.addWidget(w)
        l.addWidget(self.strip)
        l.addWidget(self.stackw, 1)
        self.show_tab(current or self.keys[0])

    def show_tab(self, key):
        if key in self.widgets:
            self.stackw.setCurrentWidget(self.widgets[key])
            self.strip.set_current(key)
            self.changed.emit(key)

    def current(self):
        return self.strip.cur

    def set_tab_visible(self, key, on):
        b = self.strip.btns.get(key)
        if b is not None:
            b.setVisible(on)
        if not on and self.strip.cur == key:
            self.show_tab(next(k for k in self.keys if self.strip.btns[k].isVisibleTo(self) or k != key))
