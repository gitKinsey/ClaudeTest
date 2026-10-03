"""Qt widget test of the Scripts page (Advanced): editor, check, dry run, run, save, versions, templates, import, assign."""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from PySide6.QtWidgets import QLineEdit    # noqa: E402

from ui_qt.widgets import RippleButton    # noqa: E402

k = QtKit("dcscripts_", cfg={"advanced": True})
e = k.e
page = k.go("scripts")


def btn(text, nth=0):
    hits = [b for b in page.findChildren(RippleButton) if b.text_full() == text]
    assert len(hits) > nth, f"button {text!r}: have {sorted({b.text_full() for b in page.findChildren(RippleButton)})}"
    return hits[nth]


assert "scripts" in [it["id"] for it in k.shell.rail.items]
page.name.setText("Test script")
page.editor.setPlainText("repeat 2\n  wait 10\nend\nnotify hello\n")
btn("Check").click(); k.spin(2)
assert "OK - 2 commands" in page.log.toPlainText(), page.log.toPlainText()
page.editor.setPlainText("repeat 2\n  wait 10\n")
btn("Check").click(); k.spin(2)
assert "Problem" in page.log.toPlainText() and e.status[1], page.log.toPlainText()
btn("Save").click(); k.spin(2)
assert "Test script" not in e.cfg["scripts"] and e.status[1], "an invalid script is not saved"
page.editor.setPlainText("repeat 2\n  wait 10\nend\n")
btn("Save").click(); k.spin(3)
assert "Test script" in e.cfg["scripts"] and page.pick.currentText() == "Test script"
btn("Dry run").click(); k.spin(2)
assert "Dry run" in page.log.toPlainText() and "wait" in page.log.toPlainText().lower(), page.log.toPlainText()
page.editor.setPlainText("repeat 2\n  wait 20\nend\n")
btn("Save").click(); k.spin(2)
assert e.script_history("Test script"), "the old version is kept"
page.editor.setPlainText("# changed again\nwait 30\n")
btn("Restore that version").click(); k.spin(2)
assert "repeat 2" in page.editor.toPlainText()
# a ready-made template
i = page.tpl.findData("Presentation: no distractions")
assert i >= 0
page.tpl.setCurrentIndex(i); page.tpl.activated.emit(i); k.spin(2)
assert "dnd on" in page.editor.toPlainText() and page.name.text()
# run (3 s delay) - a script that only waits
page.new(); page.name.setText("Waiter"); page.editor.setPlainText("wait 50\n")
btn("Run in 3 s").click()
assert k.until(lambda: "Waiter" in e.cfg["scripts"] and page.log.toPlainText(), 15), "the script ran and reported"
btn("Stop").click(); k.spin(2)
# assign to the target key
e.set_target_slot(2)
btn("Assign to the target key").click(); k.spin(3)
assert e.cfg["map"]["2"]["action"] == "Script: Waiter", e.cfg["map"]["2"]
# import from an address (local server)
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"notify imported\nwait 5\n"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
[x for x in page.findChildren(QLineEdit) if x.placeholderText().startswith("https://")][0].setText(f"http://127.0.0.1:{srv.server_port}/shared.txt")
btn("Import from the address").click()
assert k.until(lambda: page.name.text() == "shared" and "imported" in page.editor.toPlainText(), 10), (page.name.text(), e.status)
srv.shutdown()
btn("Delete").click(); k.spin(2)                         # deletes the selected script (Waiter / the first)
assert len(e.cfg["scripts"]) >= 1

# the page is hidden with the Advanced switch off
k.shell.set_advanced(False); k.spin(8)
assert "scripts" not in [it["id"] for it in k.shell.rail.items] and k.shell.current != "scripts"
print("ALL QT SCRIPTS TESTS PASSED")
k.close()
