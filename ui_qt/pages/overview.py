"""Overview: connection, pad health, live telemetry, quick actions, usage, and the twin."""
from PySide6.QtWidgets import QComboBox, QWidget

from core.base import SIM_PORT
from ui_qt.layouts import FlowLayout, PageGrid, hbox
from ui_qt.padview import PadViewQt
from ui_qt.pages.base import Page
from ui_qt.theme import theme
from ui_qt.widgets import Banner, Bar, Card, Pill, Segmented, Tile, ToggleRow, button, flow, label, set_role, tr

HEALTH = (("fw", "Firmware"), ("layer", "Pad layer"), ("flash", "Free flash"), ("reset", "Last reset"),
          ("disp", "Display"), ("hid", "USB keyboard"), ("fs", "Storage"), ("temp", "Chip temp"))
USAGE = (("K1", "K1"), ("K2", "K2"), ("K3", "K3"), ("K4", "K4"), ("K5", "K5"), ("dial+", "dial right"), ("dial-", "dial left"))


class OverviewPage(Page):
    id, title, subtitle, icon = "overview", "Overview", "Connection, health and quick actions", "overview"

    def __init__(self, shell):
        super().__init__(shell)
        e = self.engine
        # ---- banners (shown only when needed)
        self.b_fw, self.b_safe, self.b_trouble = Banner("warn"), Banner("err"), Banner("err")
        self.b_tip = Banner("info")
        self.b_tip.btn.setProperty("variant", "secondary")
        for b in (self.b_trouble, self.b_safe, self.b_fw, self.b_tip):
            self.root.addWidget(b)

        # ---- connection
        c = Card("Connection", scroll=True)
        self.ports = QComboBox()
        self.ports.setMinimumWidth(theme.px(200))
        self.ports.setSizePolicy(self.ports.sizePolicy().horizontalPolicy(), self.ports.sizePolicy().verticalPolicy())
        self.rescan = button("Rescan", "secondary", self.refresh_ports)
        row = QWidget()
        rl = hbox(row, spacing=8)
        rl.addWidget(self.ports, 1)
        rl.addWidget(self.rescan)
        c.add(row)
        self.conn_btn = button("Connect", "primary", self.on_connect)
        self.sim_btn = button("Simulate pad (no hardware)", "secondary", e.toggle_simulate)
        c.add(flow(self.conn_btn, self.sim_btn))
        self.auto = ToggleRow("Auto-connect", True)
        self.auto.toggled.connect(e.set_auto_connect)
        c.add(self.auto)
        self.conn_lbl = label("Not connected", "warn", wrap=True)
        c.add(self.conn_lbl)
        c.add(label("No pad handy? 'Simulate' connects the app to an in-process stand-in that speaks the same protocol, so you can try remapping, "
                    "macros and GIF upload end-to-end without hardware.", "muted", wrap=True))
        c.stretch()

        # ---- health
        h = Card("Pad health")
        refresh = button("Refresh", "ghost", e.refresh_health)
        h.add_action(refresh)
        hw = QWidget()
        fl = FlowLayout(hw, 8, 8)
        self.tiles = {}
        for k, cap in HEALTH:
            t = Tile(cap)
            t.setFixedWidth(theme.px(138))
            fl.addWidget(t)
            self.tiles[k] = t
        h.add(hw)
        h.stretch()

        # ---- quick actions + telemetry
        q = Card("Quick actions")
        q.add(flow(button("Upload everything to the pad", "primary", e.upload_all), button("Setup wizard", "secondary", lambda: e.emit("open_wizard")),
                   button("Guided hardware test", "secondary", lambda: e.emit("open_hwtest")), button("Pick a GIF", "secondary", lambda: e.goto("display", "gifs")),
                   button("Command palette  (Ctrl+K)", "secondary", lambda: e.emit("open_palette"))))
        q.add(label("Live telemetry (sent to the pad every second)", "h3", wrap=True))
        self.cpu_l, self.cpu_b = label("CPU   0%", "mono"), Bar()
        self.ram_l, self.ram_b = label("RAM   0%", "mono"), Bar()
        for l, b in ((self.cpu_l, self.cpu_b), (self.ram_l, self.ram_b)):
            r = QWidget()
            rl2 = hbox(r, spacing=10)
            l.setMinimumWidth(theme.px(78))
            rl2.addWidget(l)
            rl2.addWidget(b, 1)
            q.add(r)
        q.stretch()

        # ---- usage
        u = Card("How you use the pad")
        u.add_action(button("Reset counters", "ghost", e.reset_usage))
        self.ubars = {}
        for key, cap in USAGE:
            r = QWidget()
            rl3 = hbox(r, spacing=10)
            cl = label(cap)
            cl.setMinimumWidth(theme.px(70))
            bar, n = Bar(height=8), label("0", "muted")
            n.setMinimumWidth(theme.px(40))
            rl3.addWidget(cl)
            rl3.addWidget(bar, 1)
            rl3.addWidget(n)
            u.add(r)
            self.ubars[key] = (bar, n)
        u.add(label("Counted while the app runs and the pad is connected. Stored only on this computer.", "muted", wrap=True))
        u.stretch()

        # ---- twin
        t = Card("Your pad")
        self.layer_seg = Segmented(["Layer 1", "Layer 2", "Layer 3"], "Layer 1")
        self.layer_seg.valueChanged.connect(lambda v: e.set_edit_layer(int(v.split()[-1]) - 1))
        self.layer_pill = Pill("pad layer: ?", "muted")
        t.add(flow(self.layer_seg, self.layer_pill))
        self.twin = PadViewQt(e)
        shell.add_twin(self.twin)
        t.add(self.twin, 1)
        t.add(flow(button("Edit keys", "secondary", lambda: e.goto("keys")), button("Show on pad", "ghost", e.show_layer_on_pad)))

        self.grid = PageGrid({"connect": c, "health": h, "quick": q, "usage": u, "twin": t},
                             {"wide": [[("connect", 3), ("health", 3)], [("quick", 3), ("usage", 3)], [("twin", 1)]],
                              "medium": [[("connect", 3), ("health", 3)], [("quick", 3), ("usage", 3)]]},
                             order=["connect", "health", "quick", "usage", "twin"],
                             titles={"connect": "Connection", "health": "Health", "quick": "Actions", "usage": "Usage", "twin": "Pad"},
                             tabs_factory=shell.make_tabs, wide_min=1000, medium_min=720)
        self.root.addWidget(self.grid, 1)

        # ---- events
        e.on("connected", self.on_connected)
        e.on("disconnected", self.on_disconnected)
        e.on("auto_flag", lambda v: self.auto.setChecked(v))
        e.on("meters", self.on_meters)
        e.on("health", self.on_health)
        e.on("fw_status", self.on_fw)
        e.on("safe", self.on_safe)
        e.on("trouble", self.on_trouble)
        e.on("tip", self.on_tip)
        e.on("usage", self.refresh_usage)
        e.on("pad_layer", self.on_pad_layer)
        e.on("edit_layer", lambda n: self.layer_seg.set_current(f"Layer {n + 1}"))
        self.refresh_ports()
        self.refresh_usage()
        self.b_tip.hide()

    def on_show(self):
        self.refresh_ports()
        self.refresh_usage()
        self.engine.refresh_fw_status()

    # ---- connection
    def refresh_ports(self):
        cur = self.ports.currentData()
        self.ports.clear()
        choices = self.engine.port_choices()
        for lab, dev in choices:
            self.ports.addItem(lab, dev)
        if not choices:
            self.ports.addItem("(no serial ports found)", "")
        i = self.ports.findData(cur)
        if i >= 0:
            self.ports.setCurrentIndex(i)

    def on_connect(self):
        self.engine.toggle_connect(self.ports.currentData())

    def on_connected(self, info, lab):
        sim = self.engine.dev.port == SIM_PORT
        self.conn_lbl.setText(f"Connected on {lab} - firmware {info.get('fw', '?')}, free flash {info.get('fs_free', 0) // 1024} KB")
        set_role(self.conn_lbl, "ok")
        self.conn_btn.setText(tr("Disconnect"))
        self.sim_btn.setText(tr("Stop simulating" if sim else "Simulate pad (no hardware)"))

    def on_disconnected(self):
        self.conn_lbl.setText(tr("Not connected"))
        set_role(self.conn_lbl, "warn")
        self.conn_btn.setText(tr("Connect"))
        self.sim_btn.setText(tr("Simulate pad (no hardware)"))
        for t in self.tiles.values():
            t.set("-")

    # ---- live data
    def on_meters(self, cpu, ram):
        self.cpu_l.setText(f"CPU {cpu:3.0f}%")
        self.ram_l.setText(f"RAM {ram:3.0f}%")
        self.cpu_b.set(cpu / 100)
        self.ram_b.set(ram / 100)

    def on_health(self, tiles):
        for k, (text, kind) in tiles.items():
            if k in self.tiles:
                self.tiles[k].set(text, kind)

    def on_fw(self, kind, text, banner):
        if banner:
            self.b_fw.show_text(banner, "", "Update firmware", lambda: self.engine.goto("padapp", "firmware"))
        else:
            self.b_fw.hide()
        self.shell.set_badge("padapp", True if kind in ("old", "newer", "unknown", "core") else None)

    def on_safe(self, text):
        if text:
            self.b_safe.show_text(text, "", "Recovery options", lambda: self.engine.goto("padapp", "recovery"))
        else:
            self.b_safe.hide()

    def on_trouble(self, text):
        if text:
            self.b_trouble.show_text(text, "Can't connect?")
        else:
            self.b_trouble.hide()

    def on_tip(self, text):
        if text:
            self.b_tip.show_text("Tip:  " + text, "", "Got it", self.engine.dismiss_tip)
        else:
            self.b_tip.hide()

    def on_pad_layer(self, n):
        self.layer_pill.set(f"pad is on layer {n + 1}", ("accent", "muted", "warn")[n % 3])

    def refresh_usage(self):
        u = self.engine.cfg["usage"]
        top = max([int(u.get(k, 0)) for k, _c in USAGE] + [1])
        for k, (bar, n) in self.ubars.items():
            v = int(u.get(k, 0))
            bar.set(v / top)
            n.setText(str(v))
