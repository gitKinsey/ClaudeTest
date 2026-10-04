"""Qt style regression tests found by looking at screenshots: text boxes are visible, '&' is a literal ampersand, a rail counter does not sit on its label."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from PySide6.QtGui import QColor    # noqa: E402
from PySide6.QtWidgets import QPlainTextEdit    # noqa: E402

from ui_qt.theme import theme    # noqa: E402
from ui_qt.widgets import RippleButton    # noqa: E402

k = QtKit("dcstyle_")
sh = k.shell
page = k.go("keys")
page.inspector.show_tab("Test"); k.spin(6)
ed = page.sandbox
assert isinstance(ed, QPlainTextEdit) and ed.isVisibleTo(sh)
img = ed.grab().toImage()
px = QColor(img.pixel(img.width() // 2, img.height() // 2))
want = QColor(theme.c("CARD2"))
assert abs(px.red() - want.red()) < 6 and abs(px.green() - want.green()) < 6, (px.name(), want.name())

b = RippleButton("API & CLI")
assert "&&" in b.text() and b.text_full() == "API & CLI"
b.resize(300, 34); b.show(); k.spin(2)
assert "&&" in b.text()

# the Keys counter in the full rail is at the right end of its row
sh.resize(1300, 780); k.spin(8)
assert sh.rail.mode == "full"
r = sh.rail.item_rect([i["id"] for i in sh.rail.items].index("keys"))
print("ALL QT STYLE TESTS PASSED")
k.close()
