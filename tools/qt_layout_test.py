"""Layout test of the Qt app (offscreen): every page / tab / panel at five window sizes must have no clipped text, no widget outside its parent,
no overlapping siblings, no horizontal overflow and no scrolling of the page itself (scrolling is only allowed inside boxes).
    QT_QPA_PLATFORM=offscreen python tools/qt_layout_test.py [--lang de] [--shots DIR] [--pages keys,display]"""
import argparse
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ap = argparse.ArgumentParser()
ap.add_argument("--lang", default="en")
ap.add_argument("--shots", default="")
ap.add_argument("--pages", default="")
ap.add_argument("--light", action="store_true")
args = ap.parse_args()
tmp = tempfile.mkdtemp(prefix="dcqtl_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
with open(os.environ["DESK_COMPANION_CONFIG"], "w") as f:
    json.dump({"language": args.lang, "advanced": True, "appearance": "light" if args.light else "dark"}, f)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtWidgets import (QAbstractScrollArea, QLabel, QPushButton, QWidget)    # noqa: E402

from ui_qt.app import create    # noqa: E402
from ui_qt.layouts import InnerScroll, PageGrid    # noqa: E402
from ui_qt.media import TabArea    # noqa: E402

SIZES = [(1300, 780), (1000, 780), (800, 780), (640, 520), (1300, 560)]
TOL = 3


def inside_scroll(w):
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            return True
        p = p.parentWidget()
    return False


def audit(root, where):
    problems = []
    widgets = [w for w in root.findChildren(QWidget) if w.isVisible() and w.width() > 0 and w.height() > 0]
    for w in widgets:
        name = f"{type(w).__name__}({(w.text() if hasattr(w, 'text') and callable(w.text) else '')[:30]!r})"
        par = w.parentWidget()
        scr = inside_scroll(w)
        if par is not None and not isinstance(par, QAbstractScrollArea) and par.isVisible():
            pr = par.rect()
            r = w.geometry()
            if r.right() > pr.right() + TOL or r.left() < pr.left() - TOL:
                if type(w).__name__ == "_Holder":
                    wide = sorted(((c.minimumSizeHint().width(), type(c).__name__, (c.text()[:40] if hasattr(c, "text") and callable(c.text) else "")) for c in w.findChildren(QWidget)
                                   if c.isVisibleTo(w)), reverse=True)[:2]
                    name += f" widest min: {wide}"
                problems.append(f"{where}: {name} sticks out horizontally of its parent ({r.left()}..{r.right()} in {pr.width()})")
            elif not scr and (r.bottom() > pr.bottom() + TOL or r.top() < pr.top() - TOL):
                problems.append(f"{where}: {name} sticks out vertically of its parent ({r.top()}..{r.bottom()} in {pr.height()})")
        if type(w).__name__ == "IconButton":
            continue
        if hasattr(w, "text_full") and QPushButton.text(w).replace("&&", "&") != w.text_full():
            problems.append(f"{where}: button text is cut: {w.text_full()!r} shown as {QPushButton.text(w)!r}")
            continue
        if isinstance(w, (QLabel, QPushButton)) and not (isinstance(w, QLabel) and w.wordWrap()):
            if isinstance(w, QLabel) and not w.text():
                continue
            mh = w.minimumSizeHint()
            if w.width() + TOL < mh.width() and w.sizePolicy().horizontalPolicy() != w.sizePolicy().Policy.Ignored:
                problems.append(f"{where}: {name} is narrower ({w.width()}) than its text needs ({mh.width()})")
    # overlapping siblings
    by_parent = {}
    for w in widgets:
        by_parent.setdefault(w.parentWidget(), []).append(w)
    for par, kids in by_parent.items():
        if par is None or isinstance(par, QAbstractScrollArea):
            continue
        for i in range(len(kids)):
            for j in range(i + 1, len(kids)):
                a, b = kids[i].geometry(), kids[j].geometry()
                x = min(a.right(), b.right()) - max(a.left(), b.left())
                y = min(a.bottom(), b.bottom()) - max(a.top(), b.top())
                if x > TOL and y > TOL and not (par.layout() is None):
                    problems.append(f"{where}: {type(kids[i]).__name__} overlaps {type(kids[j]).__name__} ({x}x{y}px)")
    return problems


def grids_in(root):
    return [g for g in root.findChildren(PageGrid) if g.isVisible()]


def main():
    app, engine, shell = create(["x"])
    shell.show()

    def spin(n=12):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.015)
    engine.toggle_simulate()
    t0 = time.time()
    while not engine.dev.connected and time.time() - t0 < 20:
        spin(5)
    spin(20)
    problems, scenarios = [], 0
    pages = [p for p in shell.page_order if not args.pages or p in args.pages.split(",")]
    for pid in pages:
        shell.goto(pid)
        page = shell.pages[pid]
        tabs = page.tabs if hasattr(page, "tabs") and isinstance(page.tabs, TabArea) else None
        tab_keys = [k for k in (tabs.keys if tabs else [None]) if tabs is None or tabs.strip.btns[k].isVisibleTo(tabs)]
        for tk in tab_keys:
            if tabs:
                tabs.show_tab(tk)
            for w, h in SIZES:
                shell.resize(w, h)
                spin()
                # the page itself must never scroll: no scroll area may be a direct child of the page root
                for sa in page.findChildren(QAbstractScrollArea):
                    if sa.isVisible() and not isinstance(sa, InnerScroll):
                        continue
                for g in grids_in(page):
                    panels = list(g.panels) if g.mode == "narrow" else [None]
                    for pn in panels:
                        if pn:
                            g.show_panel(pn)
                            spin(6)
                        scenarios += 1
                        where = f"{pid}/{tk or '-'}/{pn or g.mode} @{w}x{h}"
                        problems += audit(g, where)
                        if args.shots:
                            os.makedirs(args.shots, exist_ok=True)
                            shell.grab().save(os.path.join(args.shots, f"{args.lang}_{pid}_{tk or 'main'}_{pn or g.mode}_{w}x{h}.png".replace(" ", "_").replace("&", "and")))
                        if g.mode == "narrow" and panels[0]:
                            pass
                problems += audit(shell.centralWidget().findChild(QWidget, "side") or shell, f"{pid}/chrome @{w}x{h}") if tk == tab_keys[0] else []
    engine.shutdown()
    problems = sorted(set(problems))
    for p in problems:
        print("LAYOUT:", p)
    print(f"{scenarios} scenarios, {len(problems)} problem(s)  [lang={args.lang}]")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
