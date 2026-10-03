"""Notification toasts: small always-on-top cards in the bottom-right corner that slide in, stack, and close on click or after a few seconds."""
from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from ui_qt.theme import theme

LIVE = []


class Toast(QFrame):
    def __init__(self, title, text, kind="ok", secs=5.0):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        accent = {"ok": theme.c("OK"), "warn": theme.c("WARN"), "off": theme.c("MUTED")}.get(kind, theme.c("OK"))
        self.setStyleSheet(f"Toast {{ background: {theme.c('CARD')}; border: 1px solid {theme.c('LINE')}; border-left: 5px solid {accent}; border-radius: 10px; }}")
        l = QVBoxLayout(self)
        l.setContentsMargins(16, 10, 16, 12)
        l.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 700; font-size: 14px;")
        b = QLabel(text)
        b.setWordWrap(True)
        b.setStyleSheet(f"color: {theme.c('MUTED')};")
        l.addWidget(t)
        l.addWidget(b)
        self.setFixedWidth(340)
        self.adjustSize()
        scr = QGuiApplication.primaryScreen().availableGeometry()
        stacked = sum(x.height() + 10 for x in LIVE)
        self._end = QPoint(scr.right() - self.width() - 20, scr.bottom() - self.height() - 24 - stacked)
        LIVE.append(self)
        self.move(self._end + QPoint(60 if not theme.reduce_motion else 0, 0))
        self.show()
        if not theme.reduce_motion:
            a = QPropertyAnimation(self, b"pos", self)
            a.setDuration(220)
            a.setStartValue(self.pos())
            a.setEndValue(self._end)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        QTimer.singleShot(int(secs * 1000), self.close)

    def mousePressEvent(self, e):
        self.close()

    def closeEvent(self, e):
        if self in LIVE:
            LIVE.remove(self)
        super().closeEvent(e)


def show_toast(title, text, kind="ok"):
    return Toast(title, text, kind)
