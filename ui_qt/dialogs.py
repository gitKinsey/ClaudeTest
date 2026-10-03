"""Windows beside the main one: setup wizard, guided hardware test, command palette, automatic backups, the pop-out mini pad."""
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QProgressBar, QWidget

from core.base import LAYERS, MODE_CHOICES, list_serial_ports
from desk_lib.hwreport import FAIL, PASS, SKIP
from ui_qt.layouts import hbox, vbox
from ui_qt.padview import PadViewQt
from ui_qt.theme import theme
from ui_qt.widgets import Pill, button, flow, label, set_role, tr


class _Dialog(QDialog):
    def __init__(self, shell, title, w=680, h=520):
        super().__init__(shell)
        self.shell, self.engine = shell, shell.engine
        self.setWindowTitle(tr(title))
        self.resize(w, h)
        self.setMinimumSize(min(w, 460), min(h, 360))
        self.setObjectName("dialog")


# ---------------------------------------------------------------------------------------------- hardware test
class HardwareTest(_Dialog):
    """Walks through LED, display, keys, dial, keyboard output and the self-test; every step is PASS / FAIL / SKIP."""
    STEPS = ("Connection", "LED", "Display", "Keys K1-K5", "Encoder", "USB keyboard", "Self-test")

    def __init__(self, shell):
        super().__init__(shell, "Guided hardware test", 680, 560)
        e = self.engine
        self.results, self.i, self.seen, self.want, self.name, self.lbls = [], -1, set(), set(), "", {}
        self._deadline = 0
        l = vbox(self, margins=(24, 20, 24, 18), spacing=8)
        self.title_lbl = label("", "h1")
        self.text = label("", "muted", wrap=True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.body = QWidget()
        self.bl = vbox(self.body, spacing=6)
        self.btns = QWidget()
        self.btl = hbox(self.btns, spacing=8)
        for w in (self.title_lbl, self.text, self.progress):
            l.addWidget(w)
        l.addWidget(self.body, 1)
        l.addWidget(self.btns)
        e.hw_listener = self.on_event
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self.finished.connect(self._closed)
        self.next_step()

    def _closed(self):
        if self.engine.hw_listener == self.on_event:
            self.engine.hw_listener = None
        self._tick_timer.stop()

    def record(self, name, res, detail=""):
        self.results.append((name, res, detail))
        self.next_step()

    def clear(self):
        from ui_qt.forms import clear_layout
        clear_layout(self.bl)
        clear_layout(self.btl)
        self.lbls = {}

    def buttons(self, *spec):
        for text, cmd, kind in spec:
            self.btl.addWidget(button(text, "primary" if kind == "pri" else "secondary", cmd))
        self.btl.addStretch(1)

    def yes_no(self, name):
        self.buttons(("Yes, works", lambda: self.record(name, PASS, "you confirmed it"), "pri"), ("No", lambda: self.record(name, FAIL, "you reported a problem"), "sec"),
                     ("Skip", lambda: self.record(name, SKIP, "skipped"), "sec"))

    def req(self, cmd, then=None, timeout=6):
        self.engine.bg(lambda: self.engine.dev.request(cmd, timeout=timeout), then or (lambda _r: None), "Pad did not answer")

    def next_step(self):
        self.i += 1
        self.clear()
        self.progress.setValue(int(min(1.0, self.i / len(self.STEPS)) * 100))
        if self.i >= len(self.STEPS):
            return self.summary()
        name = self.STEPS[self.i]
        self.title_lbl.setText(f"{self.i + 1}/{len(self.STEPS)}  {tr(name)}")
        getattr(self, "step_" + ["conn", "led", "display", "keys", "enc", "hid", "self"][self.i])(name)

    def step_conn(self, name):
        e = self.engine
        info = e.dev.info if e.dev.connected else None
        if not info:
            self.text.setText(tr("The pad is not connected. Plug it in (or use 'Simulate pad' on Overview), then press Re-check."))
            self.buttons(("Re-check", self._redo, "pri"), ("Skip", lambda: self.record(name, SKIP, "pad not connected"), "sec"))
            return
        self.text.setText(f"Connected - firmware {info.get('fw')}.")
        self.record(name, PASS, f"firmware {info.get('fw')}, safe mode: {info.get('safe', False)}")

    def _redo(self):
        self.i -= 1
        self.next_step()

    def step_led(self, name):
        e = self.engine
        self.text.setText(tr("Watch the small LED next to the USB port: it will show red, green, then blue."))

        def play():
            for r, g, b in ((60, 0, 0), (0, 60, 0), (0, 0, 60)):
                e.dev.request({"cmd": "led", "r": r, "g": g, "b": b})
                time.sleep(0.7)
            e.dev.request({"cmd": "led", "mode": "auto"})
        e.bg(play, lambda _r: (self.text.setText(tr("Did the LED show red, green and blue?")), self.yes_no(name)), "LED test failed")

    def step_display(self, name):
        e = self.engine
        if not e.dev.info.get("disp", False):
            self.text.setText(tr("The pad reports no working display (safe mode or the display is switched off)."))
            return self.record(name, SKIP, "display not available on this pad")
        self.text.setText(tr("The screen will show red, green, blue, colour bars and a grid with a circle."))

        def play():
            for r, g, b in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
                e.dev.request({"cmd": "display", "test": "fill", "r": r, "g": g, "b": b, "hold": 1500})
                time.sleep(0.9)
            e.dev.request({"cmd": "display", "test": "bars", "hold": 2500})
            time.sleep(1.2)
            e.dev.request({"cmd": "display", "test": "grid", "hold": 4000})
        e.bg(play, lambda _r: (self.text.setText(tr("Were the colours right (not swapped or inverted) and is the grid circle round and centred?")), self.yes_no(name)), "Display test failed")

    def _wait_events(self, name, want, lab, timeout=30):
        self.seen.clear()
        self.want, self.name = set(want), name
        fl = []
        for k in want:
            p = Pill(lab(k), "muted")
            self.lbls[k] = p
            fl.append(p)
        self.bl.addWidget(flow(*fl))
        self.req({"cmd": "events", "val": True})
        self.buttons(("Skip", lambda: self._finish_events(SKIP, "skipped"), "sec"),
                     ("Give up (fail)", lambda: self._finish_events(FAIL, "not detected: " + ", ".join(sorted(map(str, self.want - self.seen)))), "sec"))
        self._deadline = time.time() + timeout
        self._tick_timer.start(300)

    def _tick(self):
        if self.want and time.time() > self._deadline:
            return self._finish_events(FAIL, "timed out waiting for: " + ", ".join(sorted(map(str, self.want - self.seen))))
        for k in list(self.seen):
            if k in self.lbls:
                self.lbls[k].set(self.lbls[k]._text, "ok")
        if self.want and self.want <= self.seen:
            return self._finish_events(PASS, "all inputs detected")

    def _finish_events(self, res, detail):
        if not self.want:
            return
        self._tick_timer.stop()
        self.want = set()
        self.req({"cmd": "events", "val": False})
        self.record(self.name, res, detail)

    def on_event(self, m):
        """Reader thread: called for every pad event while this window is open."""
        evt = m.get("evt")
        key = None
        if evt == "key" and m.get("v") == 1:
            key = f"K{m.get('k')}"
        elif evt == "enc" and m.get("d"):
            key = "right" if m["d"] > 0 else "left"
        elif evt == "encsw" and m.get("v") == 1:
            key = "press"
        if key and key in getattr(self, "lbls", {}):
            self.seen.add(key)

    def step_keys(self, name):
        self.text.setText(tr("Press the five keys K1 ... K5 on the pad, in any order. Each box turns green when its press arrives."))
        self._wait_events(name, [f"K{i}" for i in range(1, 6)], lambda k: k)

    def step_enc(self, name):
        self.text.setText(tr("Turn the dial left, then right, then press it once."))
        self._wait_events(name, ["left", "right", "press"], lambda k: {"left": "turn left", "right": "turn right", "press": "press"}[k])

    def step_hid(self, name):
        e = self.engine
        if not e.dev.info.get("hid", False):
            self.text.setText(tr("This firmware build has no USB keyboard (Tools > USB Mode must be 'USB-OTG (TinyUSB)')."))
            return self.record(name, FAIL, "no HID in this firmware build")
        self.text.setText(tr("Click into any text field (Notepad, a browser search box ...). Press 'Type it' - the pad types 'DeskCompanion OK' after 3 seconds."))

        def go():
            self.text.setText(tr("Typing in 3 seconds - click into a text field now ..."))
            QTimer.singleShot(3000, lambda: self.req({"cmd": "run", "type": "text", "val": "DeskCompanion OK"}, lambda _r: (
                self.text.setText(tr("Did 'DeskCompanion OK' appear?")), self.clear(), self.yes_no(name))))
        self.buttons(("Type it", go, "pri"), ("Skip", lambda: self.record(name, SKIP, "skipped"), "sec"))

    def step_self(self, name):
        self.text.setText(tr("Running the pad's own self-test (storage, settings memory, memory, LED, display) ..."))

        def done(r):
            ok = all(bool(r.get(k)) for k in ("nvs", "fs", "heap_ok"))
            self.record(name, PASS if ok else FAIL, f"settings memory {r.get('nvs')}, storage {r.get('fs')}, heap {r.get('heap')} bytes, display {r.get('display')}")
        self.req({"cmd": "selftest"}, done, timeout=25)

    def summary(self):
        self.clear()
        n_fail = sum(r == FAIL for _n, r, _d in self.results)
        self.title_lbl.setText(tr("Result: ") + (tr("all checks passed") if not n_fail else f"{n_fail} {tr('check(s) failed')}"))
        self.text.setText(tr("Save the report as a page you can print, or copy it to share."))
        for n, r, d in self.results:
            row = QWidget()
            rl = hbox(row, spacing=10)
            rl.addWidget(Pill(r, {"PASS": "ok", "FAIL": "err", "SKIP": "muted"}[r]))
            rl.addWidget(label(n, "h3"))
            rl.addWidget(label(d, "muted", wrap=True), 1)
            self.bl.addWidget(row)
        self.bl.addStretch(1)
        self.buttons(("Save HTML report", self.save, "pri"), ("Copy as text", self.copy, "sec"), ("Close", self.close, "sec"))
        self.progress.setValue(100)

    def text_report(self):
        return "\n".join(f"{r:5s} {n}: {d}" for n, r, d in self.results)

    def copy(self):
        self.engine.ui.clipboard_set(self.text_report())
        self.engine.set_status("Hardware test result copied")

    def save(self):
        path = self.engine.hwtest_save(self.results)
        try:
            webbrowser.open(path.as_uri())
        except Exception:                                    # noqa: BLE001
            pass


# ---------------------------------------------------------------------------------------------- setup wizard
class SetupWizard(_Dialog):
    PAGES = ("Welcome", "Connect", "Firmware", "Check", "GIF", "Done")

    def __init__(self, shell):
        super().__init__(shell, "Setup wizard", 700, 540)
        self.i = -1
        l = vbox(self, margins=(26, 22, 26, 20), spacing=8)
        self.head = label("", "h1")
        self.sub = label("", "muted", wrap=True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.body = QWidget()
        self.bl = vbox(self.body, spacing=6)
        self.nav = QWidget()
        self.nl = hbox(self.nav, spacing=8)
        for w in (self.head, self.sub, self.bar):
            l.addWidget(w)
        l.addWidget(self.body, 1)
        l.addWidget(self.nav)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._connect_refresh)
        self.status = None
        self.rejected.connect(self.finish)
        self.go(1)

    def finish(self):
        self.engine.cfg["wizard_done"] = True
        self.engine.save_cfg()
        self._poll.stop()
        self.accept()

    def go(self, d):
        from ui_qt.forms import clear_layout
        self._poll.stop()
        self.i = max(0, min(len(self.PAGES) - 1, self.i + d))
        clear_layout(self.bl)
        clear_layout(self.nl)
        self.bar.setValue(int((self.i + 1) / len(self.PAGES) * 100))
        getattr(self, "page_" + self.PAGES[self.i].lower())()
        if self.i > 0:
            self.nl.addWidget(button("Back", "secondary", lambda: self.go(-1)))
        self.nl.addStretch(1)
        if self.i < len(self.PAGES) - 1:
            self.nl.addWidget(button("Next", "primary", lambda: self.go(1)))
        else:
            self.nl.addWidget(button("Finish", "primary", self.finish))

    def line(self, text, role=None):
        l = label(text, role, wrap=True)
        self.bl.addWidget(l)
        return l

    def page_welcome(self):
        self.head.setText(tr("Welcome to Desk Companion"))
        self.sub.setText(tr("A few steps to get your pad running. You can skip anything and come back from Overview -> Setup wizard."))
        for t in ("1. Connect the pad and check its firmware", "2. Test the LED and the display", "3. Pick an animation for the round screen", "", "Nothing is changed on the pad until you press a button that says so."):
            self.line(t, "muted" if t.startswith("Nothing") else None)
        self.bl.addStretch(1)

    def page_connect(self):
        self.head.setText(tr("Connect the pad"))
        self.sub.setText(tr("Plug in the USB cable (use the USB-C port of the ESP32-S3-Zero). The app finds it by itself."))
        self.status = self.line("")
        self.bl.addWidget(flow(button("Look again", "primary", self._connect_refresh), button("No pad yet - simulate one", "secondary", self.engine.toggle_simulate)))
        self.bl.addStretch(1)
        self._connect_refresh()
        self._poll.start(1000)

    def _connect_refresh(self):
        if self.status is None or self.PAGES[self.i] != "Connect":
            return
        e = self.engine
        if e.dev.connected:
            self.status.setText(f"Connected: firmware {e.dev.info.get('fw', '?')}")
            set_role(self.status, "ok")
        else:
            esp = [p for p in list_serial_ports() if p["esp"]]
            self.status.setText(("An Espressif device is visible (" + esp[0]["device"] + ") - connecting ...") if esp else
                                tr("No pad found yet. Try another cable (many are charge-only) or hold BOOT while plugging in for the first flash."))
            set_role(self.status, "warn")

    def page_firmware(self):
        e = self.engine
        self.head.setText(tr("Firmware"))
        self.sub.setText(tr("The pad needs the firmware that matches this app."))
        kind, txt = e.fw_status()
        self.line(txt, {"ok": "ok", "off": "muted"}.get(kind, "warn"))
        if kind == "off":
            self.line("Not connected: if the pad is new, flash it now. Hold BOOT while plugging in USB, then press the button.", "muted")
        elif kind != "ok":
            self.line("Updating takes about a minute. The app reconnects by itself afterwards.", "muted")
        self.bl.addWidget(flow(button("Flash the bundled firmware", "primary", lambda: e.flash_firmware("full")), button("Skip (already up to date)", "secondary", lambda: self.go(1))))
        self.bl.addStretch(1)

    def page_check(self):
        e = self.engine
        self.head.setText(tr("Quick check"))
        self.sub.setText(tr("Light the LED and put colours on the screen to see that everything answers."))

        def req(cmd):
            if not e.dev.connected:
                return e.set_status("Connect the pad first", error=True)
            e.bg(lambda: e.dev.request(cmd), None, "Pad did not answer")
        self.bl.addWidget(flow(button("Blink the LED", "primary", lambda: req({"cmd": "led", "hex": "#00c8ff"})), button("LED back to normal", "secondary", lambda: req({"cmd": "led", "mode": "auto"})),
                               button("Show screen test", "secondary", lambda: req({"cmd": "display", "test": "grid", "hold": 6000}))))
        self.line("For a thorough check of keys, dial and keyboard use Overview -> Guided hardware test.", "muted")
        self.bl.addWidget(flow(button("Open the guided hardware test", "secondary", lambda: e.emit("open_hwtest"))))
        self.bl.addStretch(1)

    def page_gif(self):
        self.head.setText(tr("Pick an animation"))
        self.sub.setText(tr("16 built-in animations, your own GIFs, or search Tenor / GIPHY."))
        self.bl.addWidget(flow(button("Open the GIF library", "primary", lambda: (self.engine.goto("display", "gifs"), self.finish()))))
        self.line("You can also do this later from the Display page.", "muted")
        self.bl.addStretch(1)

    def page_done(self):
        self.head.setText(tr("You're set"))
        self.sub.setText(tr("Everything is on the sidebar. Tips:"))
        for t in ("Keys: drag actions onto the keys; three layers switch with the dial menu or an action key.", "Rules: let the layer follow the program you are using.",
                  "Display: now playing, weather, calendar on the pad's 6th screen.", "Ctrl+K opens the command palette from anywhere."):
            self.line("-  " + t)
        self.bl.addStretch(1)


# ---------------------------------------------------------------------------------------------- command palette
class CommandPalette(_Dialog):
    def __init__(self, shell, commands):
        super().__init__(shell, "Command palette", 560, 420)
        self.commands, self.shown = commands, []
        l = vbox(self, margins=(16, 16, 16, 16), spacing=8)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(tr("Type a command ..."))
        self.list = QListWidget()
        l.addWidget(self.entry)
        l.addWidget(self.list, 1)
        self.entry.textChanged.connect(self._filter)
        self.entry.returnPressed.connect(self._run)
        self.list.itemActivated.connect(lambda _i: self._run())
        self._filter()
        self.entry.setFocus()

    def keyPressEvent(self, e):
        d = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1}.get(e.key())
        if d:
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, self.list.currentRow() + d)))
        else:
            super().keyPressEvent(e)

    def _filter(self, _t=None):
        q = self.entry.text().strip().lower()
        self.shown = [c for c in self.commands if all(w in c[0].lower() for w in q.split())]
        self.list.clear()
        self.list.addItems(["  " + c[0] for c in self.shown])
        if self.shown:
            self.list.setCurrentRow(0)

    def _run(self):
        r = self.list.currentRow()
        if r < 0:
            return
        fn = self.shown[r][1]
        self.accept()
        QTimer.singleShot(60, fn)


def palette_commands(shell):
    e = shell.engine
    cmds = [(f"Go to {shell.pages[p].title}  -  {shell.pages[p].subtitle}", lambda p=p: e.goto(p)) for p in shell.page_order if e.cfg.get("advanced") or not shell.pages[p].advanced]
    cmds += [("Upload everything to the pad", e.upload_all), ("Connect / disconnect the simulated pad", e.toggle_simulate),
             ("Rescan serial ports", lambda: shell.pages["overview"].refresh_ports()), ("Verify the keys stored on the pad", e.verify_pad_keys),
             ("Toggle light / dark theme", shell.toggle_theme), ("Run the guided hardware test", lambda: e.emit("open_hwtest")), ("Run the setup wizard", lambda: e.emit("open_wizard")),
             ("Back up everything...", e.backup_export), ("Restore from a backup...", e.backup_import),
             ("Show the Info screen on the pad", e.info_show_on_pad), ("Send info cards now", lambda: e.info_send_now(force=True)),
             ("Check the pad's state (safe mode?)", e.recovery_check), ("Undo the last key assignment  (Ctrl+Z)", e.edit_undo), ("Redo  (Ctrl+Y)", e.edit_redo),
             ("Check for app updates", e.check_updates), ("Latency test", e.latency_test),
             ("Toggle Advanced mode", lambda: shell.set_advanced(not e.cfg.get("advanced"))), ("Open the mini pad", shell.open_mini)]
    for n in range(LAYERS):
        cmds.append((f"Pad: switch to layer {n + 1}", lambda n=n: (e.set_edit_layer(n), e.show_layer_on_pad())))
    for i, lab in enumerate(MODE_CHOICES, start=1):
        cmds.append((f"Pad: show screen {lab}", lambda i=i: (e.pad.set_mode(i), e.dev.connected and e.bg(lambda: e.dev.request({"cmd": "mode", "val": i}), None, "Mode switch failed"))))
    return cmds


# ---------------------------------------------------------------------------------------------- automatic backups
class AutoBackupDialog(_Dialog):
    def __init__(self, shell):
        super().__init__(shell, "Automatic backups", 560, 440)
        l = vbox(self, margins=(18, 16, 18, 16), spacing=8)
        l.addWidget(label("Automatic backups of your settings", "h2"))
        l.addWidget(label("Made by the app after changes to key maps, macros, scripts, profiles and settings (at most one every two minutes, newest 15 kept). "
                          "Restoring replaces the settings in this app; press 'Upload to pad' afterwards to send them to the device.", "muted", wrap=True))
        l.addWidget(flow(button("Back up now", "primary", self.now)))
        from ui_qt.forms import RowList
        from ui_qt.layouts import InnerScroll
        self.rows = RowList()
        l.addWidget(InnerScroll(self.rows), 1)
        self.refresh()

    def refresh(self):
        self.rows.clear()
        files = self.engine.autobackup_list()
        if not files:
            self.rows.empty("No automatic backup yet - change something, or press 'Back up now'.")
        for path, mtime, size in files:
            self.rows.add_row([label(time.strftime("%a %d %b %Y  %H:%M:%S", time.localtime(mtime)), "h3"), label(f"{size // 1024 + 1} KB", "muted"),
                               button("Restore", "secondary", lambda p=path: self.restore(p))])

    def now(self):
        self.engine.autobackup_now()
        self.refresh()

    def restore(self, path):
        if self.engine.autobackup_restore(path):
            self.accept()


# ---------------------------------------------------------------------------------------------- mini pad
class MiniPad(QWidget):
    """The pad twin in a small always-on-top window of its own."""

    def __init__(self, shell):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.shell, e = shell, shell.engine
        self.setWindowTitle(tr("Desk Companion pad"))
        l = vbox(self, margins=(8, 8, 8, 8), spacing=6)
        self.view = PadViewQt(e)
        shell.add_twin(self.view)
        l.addWidget(self.view, 1)
        self.resize(theme.px(300), theme.px(344))
        self.setMinimumSize(theme.px(180), theme.px(210))
        st = (e.cfg.get("ui_qt") or {}).get("mini")
        if st:
            self.setGeometry(*st)

    def closeEvent(self, e):
        g = self.geometry()
        self.shell.engine.cfg.setdefault("ui_qt", {})["mini"] = [g.x(), g.y(), g.width(), g.height()]
        self.shell.engine.save_cfg()
        if self.view in self.shell.twins:
            self.shell.twins.remove(self.view)
        super().closeEvent(e)

