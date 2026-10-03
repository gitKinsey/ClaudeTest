"""Pad & App -> Diagnostics (Advanced): bring-up tools - ports, LED, inputs, display, keyboard, GPIO, system, the terminal and reports."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QHeaderView, QPlainTextEdit, QSlider, QTableWidget, QTableWidgetItem

from ui_qt.forms import combo, line, val
from ui_qt.layouts import PageGrid
from ui_qt.media import ImageView
from ui_qt.theme import theme
from ui_qt.widgets import Card, Field, Pill, ToggleRow, button, flow, label, separator, set_role, tr

DIAG_HELP = ("Bring-up & diagnostics. 1) Flash CoreBringup/CoreBringup.ino (no libraries) and check LED + ping here, "
             "2) then flash DeskCompanion/DeskCompanion.ino. Everything below talks to the real pad - or to the simulator.")
TERM_COLORS = {"tx": "#3b82f6", "rx": "#10b981", "raw": "#f59e0b", "sys": "#8e8e9b", "err": "#ef4444"}


def build_diag_tab(page):
    e = page.engine
    shell = page.shell
    # ---------------------------------------------------------------- header card
    head = Card("Diagnostics", DIAG_HELP)
    state = label("Not connected", "warn", wrap=True)
    head.add(state)
    head.add(flow(button("Ping x5", "secondary", e.ping5), button("Device info", "secondary", e.show_info), button("Full self-test", "primary", e.full_selftest),
                  button("Copy diagnostic report", "secondary", e.report), button("Export report .zip", "secondary", lambda: e.export_zip())))

    def refresh_state(*_):
        t, k = e.dev_state_text()
        state.setText(t)
        set_role(state, k)
    for ev in ("connected", "disconnected", "selftest_done"):
        e.on(ev, refresh_state)

    # ---------------------------------------------------------------- tools card
    t = Card("Tools")
    # 0 flash
    t.add(label("0. Flash firmware (no Arduino IDE needed)", "h3"))
    img = combo(["core", "full"], "core", 6)
    t.add(flow(img, label("core = CoreBringup (step 1)   full = DeskCompanion (step 2)", "muted")))
    flash_btn = button("Flash to the board", "secondary", lambda: e.flash_firmware(val(img)))
    t.add(flow(flash_btn))
    t.add(label("Hold BOOT while plugging in the USB cable (download mode), then press the button. If the pad already runs DeskCompanion it is put into download mode automatically. "
                "Needs:  pip install esptool", "muted", wrap=True))
    e.on("flash_state", lambda busy: flash_btn.setEnabled(not busy))
    t.add(separator())
    # 1 ports
    t.add(label("1. Serial ports (is the pad visible to this PC?)", "h3"))
    table = QTableWidget(0, 3)
    table.setHorizontalHeaderLabels([tr("VID:PID"), tr("Port / description"), tr("Result")])
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().hide()
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setMinimumHeight(theme.px(110))
    table.setMaximumHeight(theme.px(160))
    t.add(table)
    port_hint = label("", "muted", wrap=True)
    t.add(port_hint)
    ports = {"rows": []}

    def refresh_ports():
        rows, hint = e.ports_table()
        ports["rows"] = rows
        table.setRowCount(len(rows))
        for i, (_dev, vp, desc, res, _p) in enumerate(rows):
            for j, txt in enumerate((vp, desc, res)):
                table.setItem(i, j, QTableWidgetItem(txt))
        port_hint.setText(hint)

    def selected():
        r = table.currentRow()
        if 0 <= r < len(ports["rows"]):
            p = ports["rows"][r][4]
            port_hint.setText(p["hint"] or ("Not an Espressif device." if not p["esp"] else ""))
    table.itemSelectionChanged.connect(selected)
    t.add(flow(button("Refresh", "secondary", refresh_ports), button("Probe all ports (send hello)", "secondary", e.probe_ports)))

    def probe_result(out):
        refresh_ports()
        for i, (dev, *_rest) in enumerate(ports["rows"]):
            if dev in out:
                table.setItem(i, 2, QTableWidgetItem(out[dev]))
    e.on("probe_result", probe_result)
    t.add(separator())
    # 2 LED
    t.add(label("2. Onboard RGB LED (WS2812, GPIO21 on the Waveshare ESP32-S3-Zero)", "h3", wrap=True))
    sliders = []
    swatch = Pill("LED colour", "muted")
    job = QTimer(t)
    job.setSingleShot(True)

    job.timeout.connect(lambda: e.led_color(*(s.value() for s in sliders)))

    def led_apply(immediate=False):
        job.start(0 if immediate else 120)
    for name in ("R", "G", "B"):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, 255)
        s.valueChanged.connect(lambda _v: led_apply())
        sliders.append(s)
        t.add(Field(name, s))

    def led_set(r, g, b):
        for s, v in zip(sliders, (r, g, b)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        led_apply(True)
    t.add(flow(*[button(n, "secondary", lambda v=v: led_set(*v)) for n, v in (("Red", (60, 0, 0)), ("Green", (0, 60, 0)), ("Blue", (0, 0, 60)), ("White", (50, 50, 50)), ("Off", (0, 0, 0)))]))
    t.add(flow(*[button(n, "ghost", lambda m=m: e.led_mode(m)) for n, m in (("Blink", "blink"), ("Rainbow", "rainbow"), ("Auto (status)", "auto"), ("LED off", "off"))]))
    del swatch
    t.add(separator())
    # 3 inputs
    t.add(label("3. Keys + encoder (press them on the pad - indicators follow live)", "h3", wrap=True))
    keys = [Pill(f"K{i + 1}", "muted") for i in range(5)]
    enc = Pill("ENC  pos 0", "muted")
    t.add(flow(*keys, enc))
    ev_sw = ToggleRow("Live events", False)
    ev_sw.toggled.connect(e.events_set)
    t.add(ev_sw)
    t.add(flow(button("Read now", "secondary", e.read_inputs)))
    t.add(label("Virtual presses run the key's real action (copy / paste / media ...) on THIS computer via the pad:", "muted", wrap=True))
    t.add(flow(*[button(f"Press K{i + 1}", "ghost", lambda k=i + 1: e.dreq({"cmd": "input", "k": k}, None, "Input failed")) for i in range(5)]))
    t.add(flow(*[button(n, "ghost", lambda m=m: e.dreq({"cmd": "input", **m}, None, "Input failed"))
                 for n, m in (("Dial left", {"turn": -1}), ("Dial right", {"turn": 1}), ("Dial click", {"click": True}), ("Dial hold", {"hold": True}))]))

    def show_keys(ks, sw, pos):
        for p, v in zip(keys, ks):
            p.set(p._text, "ok" if v else "muted")
        enc.set(f"ENC  pos {pos}", "ok" if sw else "muted")
    e.on("inputs", show_keys)

    def dev_event(m):
        evt = m.get("evt")
        if evt == "key" and 1 <= m.get("k", 0) <= 5:
            keys[m["k"] - 1].set(keys[m["k"] - 1]._text, "ok" if m.get("v", 0) else "muted")
        elif evt == "enc":
            enc.set(f"ENC  pos {m.get('pos', 0)}  ({'+' if m.get('d', 0) > 0 else '-'})", enc._kind)
        elif evt == "encsw":
            enc.set(enc._text, "ok" if m.get("v", 0) else "muted")
    e.on("dev_event", dev_event)
    t.add(separator())
    # 4 display
    t.add(label("4. Display (test patterns + what the frame buffer shows)", "h3", wrap=True))
    t.add(flow(*[button(n, "secondary", lambda m=m: e.dreq({"cmd": "display", **m}, None, "Display test failed")) for n, m in
                 (("Red", {"test": "fill", "r": 255}), ("Green", {"test": "fill", "g": 255}), ("Blue", {"test": "fill", "b": 255}),
                  ("White", {"test": "fill", "r": 255, "g": 255, "b": 255}), ("Black", {"test": "fill"}))]))
    t.add(flow(*[button(n, "ghost", lambda x=x: e.dreq({"cmd": "display", "test": x}, None, "Display test failed")) for n, x in
                 (("Colour bars", "bars"), ("Grid", "grid"), ("Text", "text"), ("Back to normal", "off"))]))
    snap = ImageView(240)
    snap.setMinimumHeight(theme.px(160))
    mirror = ToggleRow("Live mirror (every 3 s)", False)
    t.add(flow(button("Screenshot of the pad's screen", "primary", e.snapshot)))
    t.add(mirror)
    t.add(snap)
    t.add(label("Test patterns stay for 8 s. A screenshot shows the firmware's frame buffer (not GIF mode).", "muted", wrap=True))
    e.on("snapshot", snap.set_image)
    mstate = {"busy": False}
    mtimer = QTimer(t)

    def mirror_tick():
        if not mirror.isChecked() or e.closing:
            return
        if e.dev.connected and not e.dev.busy and not mstate["busy"]:
            mstate["busy"] = True

            def fin():
                mstate["busy"] = False

            def ok(img):
                fin()
                snap.set_image(img)

            def fail():
                fin()
                mirror.setChecked(False)
            e.mirror_busy_snapshot(ok, fail)
    mtimer.timeout.connect(mirror_tick)
    mirror.toggled.connect(lambda on: (mtimer.start(3000), mirror_tick()) if on else mtimer.stop())
    t.add(separator())
    # 5 HID
    t.add(label("5. Keyboard / media test (the pad types into whatever window has the focus)", "h3", wrap=True))
    hid = line("", "Hello from DeskCompanion!", 300)
    t.add(hid)
    t.add(flow(button("Type in 3 s", "primary", lambda: e.hid_later({"cmd": "run", "type": "text", "val": hid.text()}))))
    t.add(flow(*[button(n, "ghost", lambda x=x: e.dreq({"cmd": "run", "type": "media", "val": x}, None, "HID test failed")) for n, x in
                 (("Mute", "MUTE"), ("Vol +", "VOL_UP"), ("Vol -", "VOL_DOWN"), ("Play/Pause", "PLAY_PAUSE"), ("Next", "NEXT"))]))
    t.add(flow(button("Ctrl+A in 3 s", "ghost", lambda: e.hid_later({"cmd": "run", "type": "combo", "val": ["CTRL", "a"]})),
               button("Win key in 3 s", "ghost", lambda: e.hid_later({"cmd": "run", "type": "combo", "val": ["GUI"]}))))
    t.add(separator())
    # 6 GPIO
    t.add(label("6. GPIO tester (wiring check)", "h3"))
    pin = line("pin", "1", 70)
    t.add(flow(pin, *[button(n, "ghost", lambda o=o: e.gpio(pin.text(), o)) for n, o in (("Read", "read"), ("Pull-up", "pullup"), ("Drive low", "low"), ("Drive high", "high"))],
               button("Scan all", "secondary", e.gpio_scan)))
    gout = label("Pins used by the pad: K1-K5 = 1,2,4,5,6  ENC A/B/SW = 13,14,15  TFT SDA/SCL/RES/DC/CS/BLK = 11,12,10,9,8,7", "muted", wrap=True)
    t.add(gout)
    e.on("gpio_text", gout.setText)
    t.add(separator())
    # 7 system
    t.add(label("7. System", "h3"))
    t.add(flow(button("Reboot pad", "secondary", e.reboot), button("Reboot into download mode", "danger", lambda: e.reboot(True)), button("Verify keys on pad", "secondary", e.verify_pad_keys)))
    t.add(label("Download mode = the ROM flasher, same as holding BOOT while plugging in; no button needed for the next upload. The port will change - flash from the Arduino IDE, then unplug / re-plug.",
                "muted", wrap=True))
    t.stretch()

    # ---------------------------------------------------------------- terminal card
    term = Card("Terminal (everything on the wire)", scroll=False)
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setMaximumBlockCount(600)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    view.setProperty("role", "mono")
    term.add(view, 1)
    raw = line('raw JSON line, e.g. {"cmd":"ping"}')
    term.add(raw)
    counters = label("rx 0 B / tx 0 B", "muted")

    def send():
        if e.send_raw(raw.text()):
            raw.clear()
    raw.returnPressed.connect(send)
    term.add(flow(button("Send", "primary", send), button("Clear", "ghost", e.term_clear), button("Copy", "ghost", e.copy_term), counters))

    def add_line(direction, text):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(TERM_COLORS.get(direction, "#cccccc")))
        cur = view.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(text + "\n", fmt)
        view.setTextCursor(cur)
        view.ensureCursorVisible()
        counters.setText(f"rx {e.dev.rx_bytes} B / tx {e.dev.tx_bytes} B")
    e.on("term", add_line)
    e.on("term_clear", view.clear)
    for ln in e.term_lines:
        add_line("sys", ln)
    refresh_ports()
    refresh_state()
    e.on("connected", lambda *_: refresh_ports())
    e.on("disconnected", refresh_ports)
    return PageGrid({"head": head, "tools": t, "term": term},
                    {"wide": [[("head", 1), ("tools", 6)], [("term", 1)]], "medium": [[("head", 1), ("tools", 6)], [("term", 1)]]},
                    order=["head", "tools", "term"], titles={"head": "Status", "tools": "Tools", "term": "Terminal"}, tabs_factory=shell.make_tabs, wide_min=800, medium_min=800)
