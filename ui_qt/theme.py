"""Theme: colour tokens (shared with the Tk app via desk_lib.tokens), accent, UI scale, reduce-motion, and the global Qt style sheet."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication

from desk_lib import tokens

_PAIRS = {k: getattr(tokens, k) for k in ("BG", "SIDE", "CARD", "CARD2", "CARD3", "LINE", "TEXT", "MUTED", "FAINT", "PINK", "OK", "OK_FILL",
                                           "WARN", "WARN_FILL", "ERR", "ERR_FILL")}


class Theme(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.mode, self.accent_name, self.scale, self.reduce_motion = "dark", "cyan", 1.0, False

    # ---- configuration
    def configure(self, mode="dark", accent="cyan", scale=1.0, reduce_motion=False):
        self.mode = mode if mode in ("dark", "light", "system") else "dark"
        self.accent_name = accent if accent in tokens.ACCENTS else "cyan"
        self.scale = max(0.8, min(1.5, float(scale)))
        self.reduce_motion = bool(reduce_motion)
        self.changed.emit()

    def resolved(self):
        if self.mode != "system":
            return self.mode
        try:
            from PySide6.QtCore import Qt
            return "light" if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light else "dark"
        except Exception:                                      # noqa: BLE001
            return "dark"

    @property
    def dark(self):
        return self.resolved() == "dark"

    def px(self, n):
        return max(1, round(n * self.scale))

    # ---- colours
    def c(self, name):
        """Hex colour of a token for the current mode."""
        i = 1 if self.dark else 0
        if name == "ACCENT":
            return tokens.ACCENTS[self.accent_name][0][i]
        if name == "ACCENT_FILL":
            return tokens.ACCENTS[self.accent_name][1][i]
        if name == "ACCENT_FILL_H":
            return tokens.ACCENTS[self.accent_name][2][i]
        if name == "WHITE":
            return "#ffffff"
        return _PAIRS[name][i]

    def qc(self, name, alpha=1.0):
        col = QColor(self.c(name))
        col.setAlphaF(alpha)
        return col

    def kind_color(self, kind):
        return {"ok": self.c("OK"), "warn": self.c("WARN"), "err": self.c("ERR"), "muted": self.c("MUTED"), "off": self.c("MUTED"),
                "text": self.c("TEXT"), "accent": self.c("ACCENT")}.get(kind, self.c("TEXT"))

    # ---- global style sheet
    def qss(self):
        c, px = self.c, self.px
        return f"""
* {{ font-size: {px(13)}px; color: {c('TEXT')}; }}
QMainWindow, #root {{ background: {c('BG')}; }}
QToolTip {{ background: {c('CARD3')}; color: {c('TEXT')}; border: 1px solid {c('LINE')}; padding: 4px 8px; border-radius: 6px; }}
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-size: {px(22)}px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: {px(15)}px; font-weight: 700; }}
QLabel[role="h3"] {{ font-size: {px(13)}px; font-weight: 700; }}
QLabel[role="muted"] {{ color: {c('MUTED')}; }}
QLabel[role="faint"] {{ color: {c('FAINT')}; }}
QLabel[role="ok"] {{ color: {c('OK')}; }} QLabel[role="warn"] {{ color: {c('WARN')}; }} QLabel[role="err"] {{ color: {c('ERR')}; }}
QLabel[role="accent"] {{ color: {c('ACCENT')}; }}
QLabel[role="mono"] {{ font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: {px(12)}px; }}
#card {{ background: {c('CARD')}; border: 1px solid {c('LINE')}; border-radius: {px(12)}px; }}
#card2 {{ background: {c('CARD2')}; border: none; border-radius: {px(10)}px; }}
#banner {{ border: none; border-radius: {px(10)}px; }}
#banner[kind="warn"] {{ background: {c('WARN_FILL')}; }} #banner[kind="err"] {{ background: {c('ERR_FILL')}; }} #banner[kind="info"] {{ background: {c('CARD2')}; }}
#banner[kind="warn"] QLabel, #banner[kind="err"] QLabel {{ color: #ffffff; }}
#tile {{ background: {c('CARD2')}; border-radius: {px(10)}px; }}
QPushButton {{ background: {c('CARD3')}; border: none; border-radius: {px(8)}px; padding: {px(7)}px {px(14)}px; font-weight: 600; }}
QPushButton:hover {{ background: {c('FAINT')}; }}
QPushButton:pressed {{ background: {c('LINE')}; }}
QPushButton:disabled {{ color: {c('FAINT')}; background: {c('CARD2')}; }}
QPushButton:focus {{ border: 2px solid {c('ACCENT')}; padding: {px(5)}px {px(12)}px; }}
QPushButton[variant="primary"] {{ background: {c('ACCENT_FILL')}; color: #ffffff; }}
QPushButton[variant="primary"]:hover {{ background: {c('ACCENT_FILL_H')}; }}
QPushButton[variant="primary"]:disabled {{ background: {c('CARD3')}; color: {c('FAINT')}; }}
QPushButton[variant="primary"][warnfill="true"] {{ background: {c('WARN_FILL')}; }}
QPushButton[variant="danger"] {{ background: {c('ERR_FILL')}; color: #ffffff; }}
QPushButton[variant="danger"]:hover {{ background: {c('ERR')}; }}
QPushButton[variant="ghost"] {{ background: transparent; color: {c('MUTED')}; }}
QPushButton[variant="ghost"]:hover {{ background: {c('CARD3')}; color: {c('TEXT')}; }}
QPushButton[variant="good"] {{ background: {c('OK_FILL')}; color: #ffffff; }}
QPushButton[variant="white"] {{ background: #ffffff; color: #111111; }}
QPushButton[variant="tile"] {{ background: {c('CARD2')}; border: 1px solid transparent; padding: {px(4)}px; font-weight: 500; }}
QPushButton[variant="tile"]:hover {{ background: {c('CARD3')}; border: 1px solid {c('ACCENT')}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{ background: {c('CARD2')}; border: 1px solid {c('LINE')}; border-radius: {px(8)}px; padding: {px(6)}px {px(9)}px;
    selection-background-color: {c('ACCENT_FILL')}; selection-color: #ffffff; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{ border: 1px solid {c('ACCENT')}; }}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {c('FAINT')}; }}
QLineEdit[readOnly="true"] {{ color: {c('MUTED')}; }}
QComboBox {{ background: {c('CARD2')}; border: 1px solid {c('LINE')}; border-radius: {px(8)}px; padding: {px(6)}px {px(9)}px; min-height: {px(18)}px; }}
QComboBox:hover {{ border: 1px solid {c('FAINT')}; }}
QComboBox:focus, QComboBox:on {{ border: 1px solid {c('ACCENT')}; }}
QComboBox:disabled {{ color: {c('FAINT')}; }}
QComboBox::drop-down {{ border: none; width: {px(22)}px; }}
QComboBox QAbstractItemView {{ background: {c('CARD')}; border: 1px solid {c('LINE')}; selection-background-color: {c('ACCENT_FILL')}; selection-color: #ffffff; outline: 0; padding: 3px; }}
QMenu {{ background: {c('CARD')}; border: 1px solid {c('LINE')}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: {px(6)}px {px(18)}px; border-radius: 5px; }} QMenu::item:selected {{ background: {c('ACCENT_FILL')}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {c('LINE')}; margin: 4px 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: {px(10)}px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c('CARD3')}; border-radius: {px(3)}px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c('FAINT')}; }}
QScrollBar:horizontal {{ background: transparent; height: {px(10)}px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c('CARD3')}; border-radius: {px(3)}px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {c('FAINT')}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QTreeWidget, QListWidget, QTableWidget {{ background: {c('CARD2')}; border: 1px solid {c('LINE')}; border-radius: {px(8)}px; outline: 0; padding: 3px; }}
QTreeWidget::item, QListWidget::item {{ padding: {px(4)}px; border-radius: 5px; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: {c('CARD3')}; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {c('ACCENT_FILL')}; color: #ffffff; }}
QHeaderView::section {{ background: {c('CARD3')}; border: none; padding: 4px 8px; color: {c('MUTED')}; }}
QProgressBar {{ background: {c('CARD3')}; border: none; border-radius: {px(4)}px; max-height: {px(8)}px; min-height: {px(8)}px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c('ACCENT')}; border-radius: {px(4)}px; }}
QSlider::groove:horizontal {{ height: {px(6)}px; background: {c('CARD3')}; border-radius: {px(3)}px; }}
QSlider::sub-page:horizontal {{ background: {c('ACCENT')}; border-radius: {px(3)}px; }}
QSlider::handle:horizontal {{ background: {c('ACCENT')}; width: {px(16)}px; height: {px(16)}px; margin: -{px(5)}px 0; border-radius: {px(8)}px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {c('ACCENT')}; }}
QDialog, #dialog {{ background: {c('BG')}; }}
QStatusBar {{ background: {c('SIDE')}; }}
"""

    def apply(self, app):
        f = QFont(app.font())
        f.setPixelSize(self.px(13))
        app.setFont(f)
        app.setStyleSheet(self.qss())


theme = Theme()
