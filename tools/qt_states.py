"""Screenshots of the non-page states: disconnected, banners, wizard pages, palette, hardware test, auto-backups, mini pad, toast, light theme.
    QT_QPA_PLATFORM=offscreen python tools/qt_states.py OUTDIR"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "qt"))
from kit import QtKit    # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/states"
os.makedirs(out, exist_ok=True)
k = QtKit("dcstates_", simulate=False, cfg={"advanced": True, "tips": True})
e, sh = k.e, k.shell
from ui_qt import dialogs, toast    # noqa: E402


def shot(w, name):
    k.spin(12)
    w.grab().save(os.path.join(out, name + ".png"))


sh.resize(1100, 700)
k.go("overview"); shot(sh, "overview_disconnected")
e.toggle_simulate(); k.until(lambda: e.dev.connected, 20); k.spin(20)
e.dev.info["fw"] = "1.1.0"; e.refresh_fw_status(); e._safe_banner({"safe": True})
e._show_trouble("COM5", {"device": "COM5", "vid": 0x303A, "pid": 0x1001}, 2)
shot(sh, "overview_banners")
e.dev.info["fw"] = "1.5.0"; e.refresh_fw_status(); e._safe_banner({"safe": False}); e._clear_trouble()
e.emit("toast", "Pad connected", "Simulated pad is ready", "ok")
e.emit("toast", "Reminder", "Stand up and stretch", "warn")
k.spin(15)
from PySide6.QtGui import QGuiApplication    # noqa: E402
QGuiApplication.primaryScreen().grabWindow(0).save(os.path.join(out, "toasts_screen.png"))
for t in list(toast.LIVE):
    t.close()
e.cfg["wizard_done"] = False
e.emit("open_wizard"); k.spin(8)
wz = [w for w in k.app.topLevelWidgets() if isinstance(w, dialogs.SetupWizard)][-1]
for i in range(len(wz.PAGES)):
    shot(wz, f"wizard_{i + 1}")
    if i < len(wz.PAGES) - 1:
        from ui_qt.widgets import RippleButton    # noqa: E402
        nb = [b for b in wz.findChildren(RippleButton) if b.text_full() in ("Next", "Finish")][0]
        nb.click(); k.spin(8)
wz.close()
e.emit("open_palette"); k.spin(8)
pal = [w for w in k.app.topLevelWidgets() if isinstance(w, dialogs.CommandPalette)][-1]
shot(pal, "palette")
pal.entry.setText("layer"); shot(pal, "palette_filtered")
pal.close()
e.emit("open_hwtest"); k.spin(8)
hw = [w for w in k.app.topLevelWidgets() if isinstance(w, dialogs.HardwareTest)][-1]
shot(hw, "hwtest_1")
k.until(lambda: hw.i >= 1, 10); shot(hw, "hwtest_led")
hw.close()
e.emit("open_autobackup"); k.spin(8)
ab = [w for w in k.app.topLevelWidgets() if isinstance(w, dialogs.AutoBackupDialog)][-1]
shot(ab, "autobackup")
ab.close()
e.emit("open_mini_window"); k.spin(10)
mini = [w for w in k.app.topLevelWidgets() if isinstance(w, dialogs.MiniPad)][-1]
mini.resize(260, 320); shot(mini, "minipad")
mini.close()
e.set_appearance("light"); k.spin(10)
sh.resize(1300, 780)
for p in ("overview", "keys", "display", "rules", "padapp"):
    k.go(p); shot(sh, f"light_{p}")
print("saved to", out)
k.close()
