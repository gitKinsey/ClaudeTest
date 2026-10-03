"""Display -> GIFs: the library (built-in / my GIFs / online), the processed preview, upload, and what is stored on the pad."""
from pathlib import Path

from PySide6.QtWidgets import QMenu, QStackedWidget, QWidget

from core.base import ONLINE_PROVIDERS
from ui_qt.forms import RowList, combo, line, val
from ui_qt.layouts import InnerScroll, PageGrid, vbox
from ui_qt.media import GifPreview, TileGrid, pil_to_pixmap
from ui_qt.widgets import Bar, Card, CheckBox, Field, Segmented, button, flow, label, set_role, tr


def build_gifs_tab(page):
    e = page.engine
    shell = page.shell

    # ---------------------------------------------------------------- library card
    lib = Card("GIF library", scroll=False)
    seg = Segmented(["Built-in", "My GIFs", "Online"], "Built-in")
    lib.add(seg)
    stack = QStackedWidget()
    lib.add(stack, 1)

    def pm(pil):
        return pil_to_pixmap(pil) if pil is not None else None

    # ---- built-in
    builtin = QWidget()
    bl = vbox(builtin, spacing=6)
    bl.addWidget(label("Generated animations - nothing to download. Click one to preview it; then 'Upload to pad'.", "muted", wrap=True))
    bgrid = TileGrid()
    bl.addWidget(bgrid)
    bl.addStretch(1)
    stack.addWidget(InnerScroll(builtin))
    state = {"built": False}

    def fill_builtin():
        if state["built"]:
            return
        state["built"] = True
        for name in e.builtin_presets():
            bgrid.add(name, pm(e.builtin_thumb(name)), lambda n=name: e.use_preset(n))

    # ---- my GIFs
    mine = QWidget()
    ml = vbox(mine, spacing=6)
    mine_info = label("", "muted", wrap=True)
    ml.addWidget(flow(button("Add GIF...", "primary", lambda: e.add_my_gifs(e.ui.ask_open_many("Add GIFs", "GIF images (*.gif);;All files (*)"))),
                      button("Open folder", "secondary", e.open_gif_folder)))
    ml.addWidget(mine_info)
    mgrid = TileGrid()
    ml.addWidget(mgrid)
    mine_empty = label("Empty. Use 'Add GIF...', 'Select GIF file...' or save something from Online.", "muted", wrap=True)
    ml.addWidget(mine_empty)
    ml.addStretch(1)
    stack.addWidget(InnerScroll(mine))

    def show_mine(gen, folder, items):
        mgrid.clear()
        mine_info.setText(f"{len(items)} GIF(s) in {folder}  -  right-click a tile to delete it")
        mine_empty.setVisible(not items)
        for p, th in items:
            p = Path(p)

            def menu(gpos, p=p):
                m = QMenu(page)
                m.addAction(f"Use '{p.stem[:30]}'", lambda: e.use_gif_file(str(p), p.name, save=False))
                m.addAction("Delete from library", lambda: e.delete_my_gif(p))
                m.exec(gpos)
            mgrid.add(p.stem, pm(th), lambda p=p: e.use_gif_file(str(p), p.name, save=False), menu)
    e.on("mine_gifs", show_mine)

    # ---- online
    online = QWidget()
    ol = vbox(online, spacing=6)
    prov = combo(list(ONLINE_PROVIDERS), e.online_provider(), 8)
    key = line("API key (free)", e.online_key(e.online_provider()), password=True)
    ol.addWidget(flow(prov, key, button("Get a free key", "secondary", lambda: e.online_key_help(val(prov)))))
    query = line("search GIFs (empty = trending)")
    ostatus = label("", "muted")
    ol.addWidget(flow(query, button("Search", "primary", lambda: e.online_run(val(prov), key.text(), query.text())),
                      button("Trending", "secondary", lambda: e.online_run(val(prov), key.text(), "")), ostatus))
    ogrid = TileGrid()
    ol.addWidget(ogrid)
    ohelp = label("Paste a free Tenor or GIPHY API key above (needed once - it is stored in your config file), then search. "
                  "This is how you get the WhatsApp / GIPHY style GIFs without any file hunting.", "muted", wrap=True)
    ol.addWidget(ohelp)
    ol.addStretch(1)
    stack.addWidget(InnerScroll(online))
    query.returnPressed.connect(lambda: e.online_run(val(prov), key.text(), query.text()))

    def provider_changed():
        key.setText(e.online_key(val(prov)))
        e.online_loaded = False
    prov.activated.connect(lambda _i: provider_changed())
    otiles = {}

    def show_online(gen, results):
        ogrid.clear()
        otiles.clear()
        ohelp.setVisible(False)
        for i, r in enumerate(results):
            otiles[i] = ogrid.add(r["title"] or "GIF", None, lambda r=r: e.use_online(r))
    e.on("online_results", show_online)
    e.on("online_thumb", lambda gen, i, th: gen == e.online_gen and i in otiles and otiles[i].set_pixmap(pil_to_pixmap(th)))
    e.on("online_status", lambda t, err: (ostatus.setText(t), set_role(ostatus, "err" if err else "muted")))

    def view(name):
        idx = ["Built-in", "My GIFs", "Online"].index(name)
        stack.setCurrentIndex(idx)
        if name == "Built-in":
            fill_builtin()
        elif name == "My GIFs":
            e.refresh_my_gifs()
        elif name == "Online" and not e.online_loaded and key.text().strip():
            e.online_run(val(prov), key.text(), "")
    seg.valueChanged.connect(view)

    # ---------------------------------------------------------------- preview / upload card
    pv = Card("Selected GIF")
    preview = GifPreview()
    pv.add(preview, 1)
    pv.add(label("Round display preview", "muted"))
    name_lbl = label("nothing selected yet - pick a tile or your own file", wrap=True)
    pv.add(name_lbl)
    pv.add(flow(button("Select GIF file...", "secondary", lambda: _choose(e))))
    keep = CheckBox("Keep a copy of files / downloads in My GIFs", True)
    keep.toggled.connect(lambda v: setattr(e, "keep_copy", bool(v)))
    dith = CheckBox("Dithering (smoother gradients, larger file)", False)
    dith.toggled.connect(lambda v: setattr(e, "dither", bool(v)))
    pv.add(keep)
    pv.add(dith)
    info = label("Frames are centre-cropped, resized to 240x240 and masked to a circle.", "muted", wrap=True)
    pv.add(info)
    bar = Bar()
    pv.add(bar)
    up = button("Upload to pad", "primary", e.upload_gif)
    up.setEnabled(False)
    slot = combo(["Slot 1", "Slot 2", "Slot 3", "Slot 4"], "Slot 1", 6)
    slot.activated.connect(lambda i: setattr(e, "gif_slot", i))
    pv.add(flow(up, slot, button("Clear this slot on the pad", "secondary", e.delete_gif)))

    e.on("gif_busy", lambda lbl: (name_lbl.setText(lbl), info.setText(tr("Processing...")), up.setEnabled(False), bar.set(0)))
    e.on("gif_failed", lambda: info.setText(tr("Could not process this GIF - see the log.")))

    def ready(text, frames, durs):
        info.setText(text)
        up.setEnabled(True)
        preview.set_frames(frames, durs)
    e.on("gif_ready", ready)
    e.on("gif_uploading", lambda on: (up.setEnabled(not on and e.gif_data is not None), bar.set(0) if on else None))
    e.on("gif_progress", lambda f: bar.set(f))

    # ---- on the pad
    pad = Card("On the pad")
    pad.add_action(button("Refresh", "ghost", e.refresh_pad_gifs))
    rot = combo([c[0] for c in e.ROT_CHOICES], "off", 12)
    rot.activated.connect(lambda _i: e.gif_rotation(val(rot)))
    pad.add(Field("Rotate every", rot))
    rows = RowList()
    pad.add(rows)
    pad.stretch()

    def draw(st, r):
        rows.clear()
        if st == "core":
            return rows.empty("CoreBringup has no GIF storage. Flash the full firmware.")
        if st == "old":
            return rows.empty("This firmware has a single GIF slot. Update it (Pad & App -> Firmware) for 4 slots and rotation.")
        if st == "disconnected":
            return rows.empty("Connect the pad to see what is stored on it.")
        have = e.pad_gifs
        for sl in range(int(r.get("max", 4))):
            h = sl in have
            cur = h and sl == r.get("cur")
            txt = (f"{have[sl] / 1024:.0f} KB" if h else "empty") + (" - playing" if cur else "")
            row = [label(f"{sl + 1}", "h3"), label(txt, "accent" if cur else ("text" if h else "faint"))]
            if h:
                row += [button("Show", "secondary", lambda sl=sl: e.pad_gif_show(sl)), button("Del", "secondary", lambda sl=sl: e.delete_gif(sl))]
            rows.add_row(row)
        rot.blockSignals(True)
        rot.setCurrentIndex(max(0, rot.findData(e.rot_label(int(r.get("rot", 0))))))
        rot.blockSignals(False)
    e.on("pad_gifs", draw)

    grid = PageGrid({"library": lib, "preview": pv, "pad": pad},
                    {"wide": [[("library", 3)], [("preview", 3), ("pad", 2)]], "medium": [[("library", 3)], [("preview", 3), ("pad", 2)]]},
                    order=["library", "preview", "pad"], titles={"library": "Library", "preview": "Preview", "pad": "On the pad"},
                    tabs_factory=shell.make_tabs, wide_min=720, medium_min=720)
    page.gif_view = view
    fill_builtin()
    e.refresh_pad_gifs()
    return grid


def _choose(e):
    path = e.ui.ask_open("Select GIF file", "GIF images (*.gif);;All files (*)")
    if path:
        import os
        e.use_gif_file(path, os.path.basename(path), save=e.keep_copy)
