"""The main window: navigation rail, top bar, animated page stack, status bar. Reflows from a wide desktop layout down to a 640 px window."""
import base64

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QShortcut
from PySide6.QtWidgets import (QApplication, QBoxLayout, QFrame, QGraphicsOpacityEffect, QMainWindow, QSizePolicy, QStackedWidget, QWidget)

from core.base import APP_NAME, APP_VERSION
from ui_qt import toast as toasts
from ui_qt.layouts import hbox, vbox
from ui_qt.padview import pil_to_qimage
from ui_qt.rail import Rail
from ui_qt.theme import theme
from ui_qt.widgets import IconButton, Pill, TabStrip, ToggleRow, label, set_role, tr

MIN_W, MIN_H = 640, 520


class ChipScreen(QWidget):
    """A tiny round copy of the pad's screen in the top bar; click to pop the twin out."""

    def __init__(self, cb):
        super().__init__()
        self.cb, self._img = cb, None
        self.setFixedSize(theme.px(38), theme.px(38))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("Pop the pad out into its own window"))

    def set_image(self, pil_img):
        self._img = pil_to_qimage(pil_img)
        self.update()

    def mousePressEvent(self, e):
        self.cb()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addEllipse(r)
        p.setClipPath(path)
        p.fillRect(r, QColor("#050608"))
        if self._img is not None:
            p.drawImage(r, self._img)
        p.setClipping(False)
        p.setPen(QColor(theme.c("ACCENT")))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)


class Shell(QMainWindow):
    def __init__(self, engine, pages_factory):
        super().__init__()
        self.engine = engine
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(MIN_W, MIN_H)
        self.twins = []
        self.pages, self.page_order = {}, []
        self.mini = None
        self._really_quit = False
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        self.outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, root)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(0)

        # ---- sidebar (rail + footer)
        self.side = QFrame()
        self.side.setObjectName("side")
        self.side_l = QBoxLayout(QBoxLayout.Direction.TopToBottom, self.side)
        self.side_l.setContentsMargins(6, 10, 6, 10)
        self.side_l.setSpacing(6)
        self.brand = label(APP_NAME, "h3")
        self.rail = Rail([])
        self.pill = Pill("Searching...", "warn")
        self.adv = ToggleRow("Advanced", bool(engine.cfg.get("advanced")))
        self.adv.toggled.connect(self.set_advanced)
        self.adv_btn = IconButton("wrench", "Advanced mode", lambda: self.set_advanced(not engine.cfg.get("advanced")))
        self.ver = label(f"v{APP_VERSION}", "faint")
        self.side_l.addWidget(self.brand)
        self.side_l.addWidget(self.rail)
        self.side_l.addStretch(1)
        self.side_l.addWidget(self.pill)
        self.side_l.addWidget(self.adv)
        self.side_l.addWidget(self.adv_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.side_l.addWidget(self.ver)
        self.outer.addWidget(self.side)

        # ---- right side
        right = QWidget()
        self.right_l = vbox(right, spacing=0)
        self.outer.addWidget(right, 1)
        self.top = QFrame()
        tl = hbox(self.top, margins=(18, 10, 12, 6), spacing=8)
        tcol = vbox(spacing=0)
        self.title = label("", "h1")
        self.subtitle = label("", "muted")
        tcol.addWidget(self.title)
        tcol.addWidget(self.subtitle)
        tl.addLayout(tcol, 1)
        self.chip = ChipScreen(self.open_mini)
        tl.addWidget(self.chip)
        tl.addWidget(IconButton("search", "Command palette  (Ctrl+K)", lambda: engine.emit("open_palette")))
        self.mini_btn = IconButton("pop", "Pop the pad out into its own window", self.open_mini)
        tl.addWidget(self.mini_btn)
        self.theme_btn = IconButton("sun", "Toggle light / dark theme", self.toggle_theme)
        tl.addWidget(self.theme_btn)
        self.right_l.addWidget(self.top)
        self.stack = QStackedWidget()
        self.stack_wrap = QWidget()
        sw = vbox(self.stack_wrap, margins=(14, 4, 14, 6), spacing=0)
        sw.addWidget(self.stack)
        self.right_l.addWidget(self.stack_wrap, 1)
        self.status_bar = QFrame()
        self.status_bar.setObjectName("statusbar")
        sl = hbox(self.status_bar, margins=(16, 5, 16, 5), spacing=8)
        self.status = label("Searching for the pad...", "muted")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status.mousePressEvent = lambda e: self.show_log()
        sl.addWidget(self.status, 1)
        self.right_l.addWidget(self.status_bar)

        # ---- pages
        for page in pages_factory(self):
            self.pages[page.id] = page
            self.page_order.append(page.id)
            self.stack.addWidget(page)
        self.all_items = [(p.id, p.title, p.icon) for p in self.pages.values()]
        self.rail.set_items(self.all_items)
        self.rail.selected.connect(self.goto)
        self.current = self.page_order[0]
        self.apply_advanced()
        self.rail.set_current(self.current, animate=False)
        self.stack.setCurrentWidget(self.pages[self.current])
        self._mode = ""
        self.style_chrome()
        self._relayout(self.width())

        # ---- engine wiring
        e = engine
        e.on("status", self.on_status)
        e.on("conn_state", lambda t, k: self.pill.set(t, k))
        e.on("goto", self.goto)
        e.on("toast", self.on_toast)
        e.on("show_window", self.raise_window)
        e.on("quit", self.quit_app)
        e.on("appearance", lambda m: self.apply_theme())
        e.on("pending", lambda n: self.set_badge("keys", int(n) if n else None))
        e.on("open_mini", self.open_mini)
        e.on("advanced", lambda on: self.apply_advanced())
        theme.changed.connect(self.style_chrome)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: e.emit("open_palette"))
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=e.edit_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=e.edit_redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=e.edit_redo)
        self._pump = QTimer(self)
        self._pump.timeout.connect(e.pump)
        self._pump.start(30)
        self._twin = QTimer(self)
        self._twin.timeout.connect(self.twin_tick)
        self._twin.start(40)
        self.restore_geometry()
        self.on_status(*e.status)

    # ---- chrome
    def style_chrome(self):
        c = theme.c
        self.side.setStyleSheet(f"#side {{ background: {c('SIDE')}; border-right: 1px solid {c('LINE')}; }}")
        self.status_bar.setStyleSheet(f"#statusbar {{ background: {c('SIDE')}; border-top: 1px solid {c('LINE')}; }}")
        self.theme_btn.set_icon_name("sun" if theme.dark else "moon")
        self.update()

    def make_tabs(self, items, current, cb):
        return TabStrip(items, current, cb)

    def add_twin(self, view):
        self.twins.append(view)

    def twin_tick(self):
        vis = [t for t in self.twins if t.isVisible()]
        want_chip = self.chip.isVisible()
        img = self.engine.twin_step(render=bool(vis) or want_chip)
        if img is not None:
            for t in vis:
                t.set_screen(img)
            if want_chip:
                self.chip.set_image(img)

    # ---- pages
    def goto(self, page, section=None):
        if page not in self.pages or (self.pages[page].advanced and not self.engine.cfg.get("advanced")):
            return
        if page != self.current:
            self.current = page
            self.rail.set_current(page)
            new = self.pages[page]
            self.stack.setCurrentWidget(new)
            self.title.setText(tr(new.title))
            self.subtitle.setText(tr(new.subtitle))
            self._fade(new)
        new = self.pages[page]
        if section:
            new.show_section(section)
        new.on_show()
        self.title.setText(tr(new.title))
        self.subtitle.setText(tr(new.subtitle))

    def _fade(self, w):
        if theme.reduce_motion:
            return
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", w)
        a.setDuration(160)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.finished.connect(lambda: w.setGraphicsEffect(None))
        a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def set_badge(self, page, value):
        self.rail.set_badge(page, value)

    def set_advanced(self, on):
        on = bool(on)
        if bool(self.engine.cfg.get("advanced")) != on:
            self.engine.cfg["advanced"] = on
            self.engine.save_cfg()
        self.adv.setChecked(on)
        self.engine.emit("advanced", on)

    def apply_advanced(self):
        on = bool(self.engine.cfg.get("advanced"))
        ids = [i for i in self.page_order if on or not self.pages[i].advanced]
        self.rail.set_visible_ids(ids)
        if not on and self.pages.get(self.current) is not None and self.pages[self.current].advanced:
            self.goto(ids[0])
        self.adv.setChecked(on)
        for p in self.pages.values():
            if hasattr(p, "apply_advanced"):
                p.apply_advanced(on)

    # ---- theme
    def toggle_theme(self):
        self.engine.set_appearance("light" if theme.dark else "dark")

    def apply_theme(self):
        cfg = self.engine.cfg
        theme.configure(cfg.get("appearance", "dark"), cfg.get("accent", "cyan"), cfg.get("ui_scale", 1.0), cfg.get("reduce_motion", False))
        theme.apply(QApplication.instance())

    # ---- status / toasts
    def on_status(self, text, error):
        self.status.setText(text)
        set_role(self.status, "err" if error else "muted")
        self.status.setToolTip(text)

    def on_toast(self, title, text, kind):
        toasts.show_toast(title, text, kind)

    def show_log(self):
        self.engine.emit("show_log")

    # ---- window
    def set_on_top(self, on):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()

    def open_mini(self):
        self.engine.emit("open_mini_window")

    def raise_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._really_quit = True
        self.close()

    def closeEvent(self, e):
        if not self._really_quit and self.engine.want_hide_on_close():
            self.hide()
            e.ignore()
            return
        self.save_geometry()
        self.engine.shutdown()
        QApplication.quit()
        e.accept()

    def save_geometry(self):
        st = self.engine.cfg.setdefault("ui_qt", {})
        st["geometry"] = base64.b64encode(bytes(self.saveGeometry())).decode()
        st["grids"] = {pid: p.grid_state() for pid, p in self.pages.items()}
        self.engine.save_cfg()

    def restore_geometry(self):
        st = self.engine.cfg.get("ui_qt") or {}
        try:
            if st.get("geometry"):
                self.restoreGeometry(QByteArray.fromBase64(st["geometry"].encode()))
            else:
                self.resize(1300, 780)
        except Exception:                                      # noqa: BLE001
            self.resize(1300, 780)
        for pid, g in (st.get("grids") or {}).items():
            if pid in self.pages:
                self.pages[pid].restore_grid(g)

    def sizeHint(self):
        return QSize(1300, 780)

    # ---- responsive chrome
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout(e.size().width())

    def _relayout(self, w):
        mode = "full" if w >= 1100 else "icons" if w >= 640 else "top"
        short = self.height() < 600
        self.subtitle.setVisible(not short and mode != "top" or (not short and w >= 560))
        if mode == self._mode:
            return
        self._mode = mode
        self.rail.set_mode(mode)
        top = mode == "top"
        self.outer.setDirection(QBoxLayout.Direction.TopToBottom if top else QBoxLayout.Direction.LeftToRight)
        self.side_l.setDirection(QBoxLayout.Direction.LeftToRight if top else QBoxLayout.Direction.TopToBottom)
        self.side.setMaximumWidth(16777215 if top else (theme.px(200) if mode == "full" else theme.px(72)))
        self.side.setMinimumWidth(0 if top else (theme.px(200) if mode == "full" else theme.px(72)))
        self.brand.setVisible(mode == "full")
        self.ver.setVisible(mode == "full")
        self.adv.setVisible(mode == "full")
        self.adv_btn.setVisible(mode != "full")
        self.pill.set_compact(mode != "full")
        self.mini_btn.setVisible(True)
        self.side_l.setContentsMargins(*(6, 4, 6, 4) if top else (6, 10, 6, 10))
