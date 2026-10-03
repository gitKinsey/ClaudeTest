"""Keys: the pad twin with drag and drop, the action library, and the inspector (key map, builder, sequence, gestures, test)."""
import json

from PySide6.QtCore import QByteArray, QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QComboBox, QLineEdit, QMenu, QPlainTextEdit, QSlider, QTreeWidget, QTreeWidgetItem, QWidget

from core.base import LAYER_NAMES, MODE_CHOICES, SLOT_LABELS
from ui_qt.layouts import FlowLayout, PageGrid, hbox, vbox
from ui_qt.padview import MIME, PadViewQt, ghost_pixmap
from ui_qt.pages.base import Page
from ui_qt.pages.keys_tabs import build_builder, build_gestures, build_sequence
from ui_qt.theme import theme
from ui_qt.widgets import Card, Pill, Segmented, TabbedPanel, ToggleRow, button, flow, label, set_role, tr


class LibraryTree(QTreeWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)
        self.setIndentation(theme.px(14))
        self.open_cats = {"Editing"}
        self.itemDoubleClicked.connect(self.menu_for)
        self.setAccessibleName("Action library")

    def populate(self, query=""):
        if not query:
            for i in range(self.topLevelItemCount()):
                it = self.topLevelItem(i)
                cat = it.data(0, Qt.ItemDataRole.UserRole + 1)
                (self.open_cats.add if it.isExpanded() else self.open_cats.discard)(cat)
        self.clear()
        for cat, names in self.engine.library_tree(query):
            top = QTreeWidgetItem([f"{cat}  ({len(names)})"])
            top.setData(0, Qt.ItemDataRole.UserRole + 1, cat)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.addTopLevelItem(top)
            for n in names:
                ch = QTreeWidgetItem([n])
                ch.setData(0, Qt.ItemDataRole.UserRole, {"cat": cat, "action": n})
                top.addChild(ch)
            top.setExpanded(bool(query) or cat in self.open_cats)

    def startDrag(self, actions):
        it = self.currentItem()
        data = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if not data:
            return
        mime = QMimeData()
        mime.setData(MIME, QByteArray(json.dumps(data).encode()))
        d = QDrag(self)
        d.setMimeData(mime)
        d.setPixmap(ghost_pixmap(data["action"]))
        d.exec(Qt.DropAction.CopyAction)

    def menu_for(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        m = QMenu(self)
        for s, lbl in SLOT_LABELS.items():
            m.addAction(f"Assign to {lbl}", lambda s=s: self.engine.drop_assign(s, dict(data)))
        m.exec(self.cursor().pos())


class KeysPage(Page):
    id, title, subtitle, icon = "keys", "Keys", "Your keys on three layers, with a live twin of the device", "keys"

    def __init__(self, shell):
        super().__init__(shell)
        e = self.engine
        # ---- twin
        t = Card("")
        self.layer_seg = Segmented(["Layer 1", "Layer 2", "Layer 3"], "Layer 1")
        self.layer_seg.valueChanged.connect(lambda v: e.set_edit_layer(int(v.split()[-1]) - 1))
        self.layer_pill = Pill("pad layer: ?", "muted")
        t.add(flow(self.layer_seg, self.layer_pill, button("Show on pad", "ghost", e.show_layer_on_pad)))
        self.twin = PadViewQt(e)
        shell.add_twin(self.twin)
        t.add(self.twin, 1)
        self.upload_btn = button("Upload to pad", "primary", e.upload_all)
        self.autoup = ToggleRow("Upload every change immediately", False)
        self.autoup.toggled.connect(e.set_autoup)
        t.add(flow(self.upload_btn, button("Reset this layer", "secondary", e.reset_defaults), button("Undo", "ghost", e.edit_undo), button("Redo", "ghost", e.edit_redo)))
        t.add(self.autoup)
        t.add(label("Orange dots on the pad mark changes the physical device does not have yet.", "muted", wrap=True))

        # ---- library
        lib = Card("Action library", "Drag an action onto a key or an encoder arrow. Double-click for a menu.")
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search actions..."))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.tree.populate(self.search.text().strip()))
        self.tree = LibraryTree(e)
        lib.body.setContentsMargins(0, 0, 0, 0)
        lib.scroller.hide()
        lib.layout().addWidget(self.search)
        lib.layout().addWidget(self.tree, 1)
        self.tree.populate()

        # ---- inspector
        self.rows = {}
        keymap = self._build_keymap()
        builder = build_builder(self)
        seq = build_sequence(self)
        gest = build_gestures(self)
        test = self._build_test()
        self.inspector = TabbedPanel("Inspector", {"Key map": keymap, "Build": builder, "Sequence": seq, "Gestures": gest, "Test": test})

        self.grid = PageGrid({"twin": t, "library": lib, "inspector": self.inspector},
                             {"wide": [[("library", 2)], [("twin", 4)], [("inspector", 3)]],
                              "medium": [[("library", 2)], [("twin", 3), ("inspector", 3)]]},
                             order=["twin", "library", "inspector"], titles={"twin": "Pad", "library": "Library", "inspector": "Inspector"},
                             tabs_factory=shell.make_tabs, wide_min=1000, medium_min=720)
        self.root.addWidget(self.grid, 1)

        e.on("pending", self.on_pending)
        e.on("uploading", lambda on: self.upload_btn.setEnabled(not on))
        e.on("library", self.on_library)
        e.on("map_changed", self.refresh_rows)
        e.on("edit_layer", self.on_edit_layer)
        e.on("pad_layer", lambda n: self.layer_pill.set(f"pad is on layer {n + 1}", ("accent", "muted", "warn")[n % 3]))
        e.on("vp_log", self.on_vp_log)
        e.on("pad_state", self.on_pad_state)
        e.on("live_test", self.on_live)
        self.refresh_rows()
        self.on_pending(e.pending_count)

    # ---- events
    def on_pending(self, n):
        self.upload_btn.setText(tr("Upload to pad") if not n else f"{tr('Upload to pad')}  ({n} {tr('unsent')})")
        self.upload_btn.set_variant("primary", warnfill="true" if n else "false")

    def on_library(self):
        self.tree.populate(self.search.text().strip())
        self.refresh_rows()

    def on_edit_layer(self, n):
        self.layer_seg.set_current(f"Layer {n + 1}")
        self.map_title.setText(f"{tr('Key map')} - {LAYER_NAMES[n]}")

    def on_vp_log(self, line):
        self.test_log.appendPlainText(line)

    def on_pad_state(self, kind):
        if kind == "mode":
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(self.engine.pad.mode - 1)
            self.mode_combo.blockSignals(False)
        elif kind == "bright":
            self.bright.blockSignals(True)
            self.bright.setValue(self.engine.pad.brightness)
            self.bright.blockSignals(False)

    # ---- key map
    def _build_keymap(self):
        w = QWidget()
        l = vbox(w, spacing=8)
        self.map_title = label(f"{tr('Key map')} - {LAYER_NAMES[0]}", "h3", wrap=True)
        l.addWidget(self.map_title)
        l.addWidget(label("A keyboard-friendly alternative to dragging. Changes are stored on the pad once uploaded.", "muted", wrap=True))
        for slot, name in SLOT_LABELS.items():
            row = QWidget()
            rl = vbox(row, spacing=3)
            rl.addWidget(label(name, "muted"))
            fr = QWidget()
            fl = FlowLayout(fr, 8, 6)
            cc, ac = QComboBox(), QComboBox()
            for cb in (cc, ac):
                cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                cb.setMinimumContentsLength(14)
            cc.activated.connect(lambda _i, s=slot: self._on_cat(s))
            ac.activated.connect(lambda _i, s=slot: self._on_action(s))
            fl.addWidget(cc)
            fl.addWidget(ac)
            rl.addWidget(fr)
            l.addWidget(row)
            self.rows[slot] = (cc, ac)
        l.addWidget(flow(button("Upload all to pad", "primary", self.engine.upload_all), button("Reset this layer to defaults", "secondary", self.engine.reset_defaults)))
        l.addWidget(label("Share this layer", "h3"))
        self.share_edit = QLineEdit()
        self.share_edit.setPlaceholderText(tr("paste a share code (DC1:...)"))
        l.addWidget(flow(button("Copy a share code of this layer", "secondary", self.engine.share_copy)))
        l.addWidget(self.share_edit)
        l.addWidget(flow(button("Replace this layer with it", "secondary", lambda: self.engine.share_import(self.share_edit.text())),
                         button("Export macros...", "ghost", self.engine.macro_export), button("Import macros...", "ghost", self.engine.macro_import)))
        l.addStretch(1)
        return w

    def refresh_rows(self):
        e = self.engine
        for slot, (cc, ac) in self.rows.items():
            cat, name = e.slot_assignment(slot)
            cc.blockSignals(True)
            ac.blockSignals(True)
            cc.clear()
            cc.addItems(e.categories())
            cc.setCurrentText(cat)
            ac.clear()
            ac.addItems(e.names_for(cat))
            ac.setCurrentText(name)
            cc.blockSignals(False)
            ac.blockSignals(False)

    def _on_cat(self, slot):
        cc, ac = self.rows[slot]
        names = self.engine.names_for(cc.currentText())
        if names:
            self.engine.assign_combo_action(slot, cc.currentText(), names[0])

    def _on_action(self, slot):
        cc, ac = self.rows[slot]
        self.engine.assign_combo_action(slot, cc.currentText(), ac.currentText())

    # ---- test on this PC
    def _build_test(self):
        e = self.engine
        w = QWidget()
        l = vbox(w, spacing=8)
        l.addWidget(label("Test on this PC", "h3"))
        self.live = ToggleRow("Run actions for real (live test)", e.live_test)
        self.live.toggled.connect(e.set_live_test)
        if not e.host.available:
            self.live.setEnabled(False)
        l.addWidget(self.live)
        self.hint = label("", wrap=True)
        l.addWidget(self.hint)
        self.delay = QComboBox()
        for s in (0, 1, 2, 3, 5):
            self.delay.addItem(f"{s} s", s)
        self.delay.setCurrentIndex(max(0, self.delay.findData(int(e.cfg.get("test_delay", 0)))))
        self.delay.activated.connect(lambda _i: e.set_test_delay(self.delay.currentData()))
        l.addWidget(flow(label("Delay before sending"), self.delay))
        self.top_toggle = ToggleRow("Keep this window on top", False)
        self.top_toggle.toggled.connect(self.shell.set_on_top)
        l.addWidget(self.top_toggle)
        l.addWidget(label("Sandbox: click inside, then press virtual keys", "muted", wrap=True))
        self.sandbox = QPlainTextEdit()
        self.sandbox.setMinimumHeight(theme.px(56))
        self.sandbox.setMaximumHeight(theme.px(90))
        e.ui.sandbox = self.sandbox
        l.addWidget(self.sandbox)
        l.addWidget(label("Test log", "muted"))
        self.test_log = QPlainTextEdit()
        self.test_log.setReadOnly(True)
        self.test_log.setMaximumBlockCount(200)
        self.test_log.setMinimumHeight(theme.px(80))
        l.addWidget(self.test_log)
        l.addWidget(label("Virtual screen", "h3"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(MODE_CHOICES)
        self.mode_combo.setCurrentIndex(e.pad.mode - 1)
        self.mode_combo.activated.connect(lambda i: e.pad.set_mode(i + 1))
        l.addWidget(flow(label("Mode"), self.mode_combo))
        self.bright = QSlider(Qt.Orientation.Horizontal)
        self.bright.setRange(5, 255)
        self.bright.setValue(e.pad.brightness)
        self.bright.valueChanged.connect(lambda v: e.pad.set_brightness(v))
        row = QWidget()
        rl = hbox(row, spacing=8)
        rl.addWidget(label("Brightness"))
        rl.addWidget(self.bright, 1)
        l.addWidget(row)
        l.addStretch(1)
        self.on_live()
        return w

    def on_live(self):
        text, kind = self.engine.live_hint()
        self.hint.setText(text)
        set_role(self.hint, kind)
        self.live.setChecked(self.engine.live_test)

    def on_show(self):
        self.engine.pad.dirty = True
