"""Screenshot tour: every page, every tab and inspector tab, on a connected simulated pad, at the given sizes.
    QT_QPA_PLATFORM=offscreen python tools/qt_tour.py OUTDIR [--sizes 1300x780,640x520] [--light] [--lang de] [--pages keys,display]"""
import argparse
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--sizes", default="1300x780")
ap.add_argument("--light", action="store_true")
ap.add_argument("--lang", default="en")
ap.add_argument("--pages", default="")
args = ap.parse_args()
tmp = tempfile.mkdtemp(prefix="dctour_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
json.dump({"advanced": True, "language": args.lang, "appearance": "light" if args.light else "dark"}, open(os.environ["DESK_COMPANION_CONFIG"], "w"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui_qt.app import create    # noqa: E402
from ui_qt.media import TabArea    # noqa: E402
from ui_qt.widgets import TabbedPanel    # noqa: E402

app, e, sh = create(["x"])
sh.show()


def spin(n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.02)


e.toggle_simulate()
t0 = time.time()
while not e.dev.connected and time.time() - t0 < 20:
    spin(5)
spin(30)
os.makedirs(args.out, exist_ok=True)
only = [p for p in args.pages.split(",") if p]
sizes = [tuple(int(x) for x in s.split("x")) for s in args.sizes.split(",")]
n = 0
for pid in sh.page_order:
    if only and pid not in only:
        continue
    page = sh.pages[pid]
    sh.goto(pid)
    spin(10)
    variants = [("", None)]
    for ta in page.findChildren(TabArea):
        variants += [(f"{k}".replace(" ", "").replace("&", "and"), (ta, k)) for k in ta.keys if ta.strip.btns[k].isVisibleTo(ta)]
    for tp in page.findChildren(TabbedPanel):
        variants += [(f"insp_{k}".replace(" ", ""), (tp, k)) for k in tp.keys]
    for name, v in variants:
        if v is not None:
            v[0].show_tab(v[1])
            spin(10)
            # nested tab areas (Display -> GIFs ...) show their first panel; nothing more to do here
        for w, h in sizes:
            sh.resize(w, h)
            spin(20)
            fn = f"{pid}{'_' + name if name else ''}_{w}x{h}.png"
            sh.grab().save(os.path.join(args.out, fn))
            n += 1
print(n, "screenshots in", args.out)
e.shutdown()
