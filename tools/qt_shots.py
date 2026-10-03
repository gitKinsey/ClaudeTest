"""Render the Qt app offscreen at several window sizes and save screenshots:   QT_QPA_PLATFORM=offscreen python tools/qt_shots.py OUTDIR [page ...]"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
tmp = tempfile.mkdtemp(prefix="dcqt_")
os.environ.setdefault("HOME", tmp)
os.environ.setdefault("DESK_COMPANION_CONFIG", os.path.join(tmp, "cfg.json"))
os.environ.setdefault("DESK_COMPANION_GIFS", os.path.join(tmp, "gifs"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

SIZES = [(1300, 780), (1000, 780), (800, 780), (640, 520), (1300, 560)]


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots"
    only = sys.argv[2:]
    os.makedirs(out, exist_ok=True)
    from ui_qt.app import create
    app, engine, shell = create(["x"])
    shell.show()
    if os.environ.get("DC_THEME"):
        engine.set_appearance(os.environ["DC_THEME"])
        engine.pump()
        shell.apply_theme()

    def spin(n=10):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.02)
    engine.toggle_simulate()
    t0 = time.time()
    while not engine.dev.connected and time.time() - t0 < 20:
        spin(5)
    spin(20)
    for pid in shell.page_order:
        if only and pid not in only:
            continue
        if shell.pages[pid].advanced and not engine.cfg.get("advanced"):
            continue
        shell.goto(pid)
        for w, h in SIZES:
            shell.resize(w, h)
            spin(25)
            shell.grab().save(os.path.join(out, f"{pid}_{w}x{h}.png"))
    engine.shutdown()


if __name__ == "__main__":
    main()
