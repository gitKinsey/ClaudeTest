"""Pad & App: behaviour, firmware, recovery, backup, this app, diagnostics (Advanced) and the activity log."""
from PySide6.QtWidgets import QPlainTextEdit

from core.base import FW_BUNDLED, LAYOUTS
from desk_lib import appconst, autostart, hotkey, i18n, tokens, tray
from ui_qt.forms import cfg_switch, combo, line, val
from ui_qt.layouts import PageGrid
from ui_qt.media import TabArea
from ui_qt.pages.base import Page
from ui_qt.pages.diag_tab import build_diag_tab
from ui_qt.widgets import Bar, Card, Field, Segmented, ToggleRow, button, flow, label, separator, set_role


class PadAppPage(Page):
    id, title, subtitle, icon = "padapp", "Pad & App", "Behaviour, firmware, backup and this app", "padapp"

    def __init__(self, shell):
        super().__init__(shell)
        self.behaviour = self._behaviour()
        self.firmware = self._firmware()
        self.recovery = self._recovery()
        self.backup = self._backup()
        self.app = self._app()
        self.diagnostics = build_diag_tab(self)
        self.log = self._log()
        self.names = {"Behaviour": self.behaviour, "Firmware": self.firmware, "Recovery": self.recovery, "Backup": self.backup, "This app": self.app,
                      "Diagnostics": self.diagnostics, "Log": self.log}
        self.tabs = TabArea(self.names, "Behaviour")
        self.root.addWidget(self.tabs, 1)
        self.apply_advanced(bool(self.engine.cfg.get("advanced")))
        self.engine.on("show_log", lambda: self.engine.goto("padapp", "log"))

    def apply_advanced(self, on):
        self.tabs.set_tab_visible("Diagnostics", on)

    def show_section(self, section):
        m = {"behaviour": "Behaviour", "firmware": "Firmware", "recovery": "Recovery", "backup": "Backup", "app": "This app", "diagnostics": "Diagnostics", "log": "Log"}.get(section)
        if m:
            self.tabs.show_tab(m)

    def on_show(self):
        self.engine.refresh_fw_status()

    # ------------------------------------------------------------------ behaviour
    def _behaviour(self):
        e, shell = self.engine, self.shell
        c = Card("Pad and PC")
        os_ = combo(["win", "mac", "linux"], e.cfg["os"], 8)
        os_.activated.connect(lambda _i: e.set_os(val(os_)))
        lay = combo(["auto"] + LAYOUTS, e.cfg.get("layout", "auto"), 8)
        lay.activated.connect(lambda _i: e.set_layout(val(lay)))
        c.add(Field("Host OS", os_))
        c.add(Field("Keyboard layout", lay))
        c.add(label("The pad presses keys for this layout (QWERTZ swaps Z/Y). 'auto' follows this PC.", "muted", wrap=True))
        c.add(flow(button("Sync time now", "secondary", e.sync_time)))
        c.add(separator())
        c.add(cfg_switch(e, "Popup + sound when the pad connects", "notify", True))
        hm = cfg_switch(e, "Mirror this PC's volume / playback on the pad", "host_media_sync", True)
        if not e.hostmedia.available:
            hm.setChecked(False)
            hm.setEnabled(False)
            hm.setText("Mirror PC volume: not available here (Linux: pactl/amixer, macOS: built in, Windows: pip install pycaw)")
        c.add(hm)
        auto = ToggleRow("Start this app when I log in (minimised)", autostart.is_enabled())
        auto.toggled.connect(lambda v: auto.setChecked(v if e.autostart_set(bool(v)) else not v))
        c.add(auto)
        tr_sw = ToggleRow("Keep running in the system tray when the window is closed", bool(e.cfg.get("tray")))
        tr_sw.toggled.connect(lambda v: tr_sw.setChecked(v if e.tray_set(bool(v)) else False))
        if not tray.available():
            tr_sw.setEnabled(False)
            tr_sw.setText("Tray icon: not available (pip install pystray)")
        c.add(tr_sw)
        shell_sw = ToggleRow("Allow the pad to run shell commands on this PC (only commands in your own key maps; off by default)", bool(e.cfg.get("allow_shell")))
        shell_sw.toggled.connect(lambda v: shell_sw.setChecked(e.shell_set(bool(v))))
        c.add(shell_sw)
        c.stretch()
        return PageGrid({"b": c}, {"wide": [[("b", 1)]], "medium": [[("b", 1)]]}, order=["b"], titles={"b": "Behaviour"}, tabs_factory=shell.make_tabs, wide_min=300, medium_min=300)

    # ------------------------------------------------------------------ firmware
    def _firmware(self):
        e, shell = self.engine, self.shell
        c = Card("Firmware", "Version check, one-click update over USB, and an experimental Wi-Fi update.")
        fw = label("pad not connected", "h3", wrap=True)
        c.add(fw)
        c.add(label(f"This app ships firmware {FW_BUNDLED} (firmware/DeskCompanion.bin).", "muted", wrap=True))
        upd = button(f"Update the pad to {FW_BUNDLED}", "primary", lambda: e.flash_firmware("full"))
        core = button("Flash CoreBringup (diagnostic)", "secondary", lambda: e.flash_firmware("core"))
        wifi = button("Flash the Wi-Fi build", "secondary", lambda: e.flash_firmware("wifi"))
        other = button("Flash another .bin...", "secondary", lambda: e.flash_other())
        self.adv_flash = [core, wifi, other]
        c.add(flow(upd, core, wifi, other))
        c.add(label("Needs:  pip install esptool.  The pad is put into download mode automatically when it runs DeskCompanion; otherwise hold BOOT while plugging in USB.", "muted", wrap=True))
        c.stretch()

        def fw_status(kind, text, banner):
            fw.setText(text)
            set_role(fw, {"ok": "ok", "off": "muted"}.get(kind, "warn"))
            wf.note.setText(e.wifi_note())
            for w in wf.widgets:
                w.setEnabled(e.wifi_supported())
        e.on("fw_status", fw_status)

        class W:                                      # the Wi-Fi card
            pass
        wf = W()
        w = Card("Wi-Fi update (optional, experimental)")
        wf.note = label("", "muted", wrap=True)
        w.add(wf.note)
        pw = line("OTA password", "", 200, password=True)
        w.add(pw)
        en = button("Enable OTA on the pad", "secondary", lambda: e.ota_enable(True, pw.text()))
        dis = button("Disable", "secondary", lambda: e.ota_enable(False, pw.text()))
        ota = button("Update over Wi-Fi", "primary", lambda: e.ota_update(pw.text()))
        w.add(flow(en, dis, ota))
        bar = Bar()
        w.add(bar)
        ssid, pas = line("SSID", "", 200), line("Password", "", 200, password=True)
        save = button("Save Wi-Fi", "primary", lambda: e.wifi_save(ssid.text(), pas.text()))
        w.add(label("Optional Wi-Fi / NTP time sync", "h3"))
        w.add(label("The pad is a cable device and keeps its time through this app. Wi-Fi is an optional extra that is compiled into the firmware only when DC_ENABLE_WIFI is 1 (top of DeskCompanion.ino).", "muted", wrap=True))
        w.add(ssid)
        w.add(pas)
        w.add(flow(save))
        w.stretch()
        wf.widgets = [pw, en, dis, ota, ssid, pas, save]
        e.on("ota_progress", bar.set)
        e.on("ota_state", lambda busy: ota.setEnabled(not busy))
        e.on("flash_state", lambda busy: [b.setEnabled(not busy) for b in [upd] + self.adv_flash])
        wf.note.setText(e.wifi_note())
        return PageGrid({"fw": c, "wifi": w}, {"wide": [[("fw", 2)], [("wifi", 3)]], "medium": [[("fw", 2)], [("wifi", 3)]]}, order=["fw", "wifi"],
                        titles={"fw": "Firmware", "wifi": "Wi-Fi"}, tabs_factory=shell.make_tabs, wide_min=800, medium_min=800)

    # ------------------------------------------------------------------ recovery
    def _recovery(self):
        e, shell = self.engine, self.shell
        c = Card("Recovery and safety", "If the pad boots into safe mode (red double-blink) or misbehaves.")
        st = label("pad not connected", wrap=True)
        c.add(st)
        e.on("safe_text", lambda t, k: (st.setText(t), set_role(st, k)))
        c.add(flow(button("Check pad state", "primary", e.recovery_check), button("Retry normal boot", "secondary", lambda: e.recovery("safe_retry")),
                   button("Boot without display", "secondary", lambda: e.recovery("nodisp")), button("Re-enable display", "secondary", lambda: e.recovery("disp"))))
        c.add(flow(button("Reset key maps on the pad", "danger", lambda: e.recovery("keys")), button("Reset all pad settings", "danger", lambda: e.recovery("settings")),
                   button("Delete stored GIFs", "danger", lambda: e.recovery("gifs"))))
        c.add(flow(button("Boot report", "secondary", e.boot_report), button("Roll back to the previous firmware", "danger", e.rollback_fw)))
        c.add(label("(the rollback only works after a Wi-Fi update, when the old firmware is still in the other slot)", "muted", wrap=True))
        c.stretch()
        return PageGrid({"r": c}, {"wide": [[("r", 1)]], "medium": [[("r", 1)]]}, order=["r"], titles={"r": "Recovery"}, tabs_factory=shell.make_tabs, wide_min=300, medium_min=300)

    # ------------------------------------------------------------------ backup
    def _backup(self):
        e, shell = self.engine, self.shell
        c = Card("Backup and restore", "Key maps of all layers, macros, profiles, info settings, app settings and your GIF library in one .zip.")
        c.add(flow(button("Back up everything...", "primary", lambda: e.backup_export()), button("Restore from a backup...", "secondary", lambda: e.backup_import()),
                   button("Export one macro...", "secondary", lambda: e.macro_export()), button("Import macros...", "secondary", lambda: e.macro_import()),
                   button("Automatic backups...", "secondary", lambda: e.emit("open_autobackup"))))
        c.add(separator())
        c.add(label("Share a layer", "h3"))
        code = line("paste a share code (DC1:...)", "", 400)
        c.add(flow(button("Copy a share code of this layer", "secondary", e.share_copy)))
        c.add(code)
        c.add(flow(button("Replace this layer with it", "secondary", lambda: e.share_import(code.text()))))
        c.stretch()
        return PageGrid({"b": c}, {"wide": [[("b", 1)]], "medium": [[("b", 1)]]}, order=["b"], titles={"b": "Backup"}, tabs_factory=shell.make_tabs, wide_min=300, medium_min=300)

    # ------------------------------------------------------------------ this app
    def _app(self):
        e, shell = self.engine, self.shell
        cfg = e.cfg
        a = Card("Appearance")
        theme_seg = Segmented(["dark", "light", "system"], cfg.get("appearance", "dark"))
        theme_seg.valueChanged.connect(e.set_appearance)
        e.on("appearance", lambda m: theme_seg.set_current(m))
        a.add(theme_seg)
        accent = combo(list(tokens.ACCENTS), cfg.get("accent", "cyan"), 8)
        accent.activated.connect(lambda _i: e.set_pref("accent", val(accent)))
        scale = combo(list(appconst.SCALES), next((k for k, v in appconst.SCALES.items() if abs(v - float(cfg.get("ui_scale", 1.0))) < 0.01), "100 %"), 8)
        scale.activated.connect(lambda _i: e.set_pref("ui_scale", appconst.SCALES[val(scale)], restart=True))
        lang = combo(list(i18n.LANGS.values()), i18n.LANGS.get(cfg.get("language", "en"), "English"), 10)
        lang.activated.connect(lambda _i: e.set_pref("language", next((k for k, v in i18n.LANGS.items() if v == val(lang)), "en"), restart=True))
        a.add(Field("Accent colour", accent))
        a.add(Field("Size", scale))
        a.add(Field("Language", lang))
        a.add(cfg_switch(e, "Reduce motion (no sliding or fading)", "reduce_motion"))
        adv = ToggleRow("Advanced mode (diagnostics, scripts, API and plugins, core flashing)", bool(cfg.get("advanced")))
        adv.toggled.connect(shell.set_advanced)
        e.on("advanced", lambda on: adv.setChecked(on))
        a.add(adv)
        a.add(label("Accent colour and reduce-motion apply at once. Size and language apply the next time the app starts. Language covers navigation, buttons and headings, not every sentence.", "muted", wrap=True))
        a.stretch()

        h = Card("Hotkey, updates and tips")
        hk = ToggleRow("Open the command palette from anywhere with", bool(cfg.get("hotkey_on")))
        combo_edit = line("", cfg.get("hotkey", "ctrl+alt+k"), 160)
        if not hotkey.available():
            hk.setEnabled(False)
            hk.setText("Global hotkey: not available here (pip install pynput; Wayland blocks it)")

        def hk_apply():
            if not e.hotkey_apply(hk.isChecked(), combo_edit.text().strip()):
                hk.setChecked(False)
        hk.toggled.connect(lambda _v: hk_apply())
        combo_edit.returnPressed.connect(hk_apply)
        h.add(hk)
        h.add(combo_edit)
        h.add(separator())
        upd_lbl = label("", "muted", wrap=True)
        h.add(flow(button("Check for updates", "secondary", e.check_updates)))
        h.add(cfg_switch(e, "Check at start (asks GitHub; sends nothing about you)", "update_check"))
        h.add(cfg_switch(e, "Show tips on the Overview page", "tips", True, after=lambda _v: e.refresh_tip()))
        h.add(upd_lbl)

        def upd_result(r):
            upd_lbl.setText(r["message"] + (f"\n{r['url']}" if r["newer"] else ""))
            if r["newer"] and e.ui.confirm("Desk Companion", f"{r['message']}\n\nOpen the download page?"):
                import webbrowser
                webbrowser.open(r["url"])
        e.on("update_result", upd_result)
        h.add(separator())
        h.add(label("Link quality and power", "h3"))
        pw = label(e.power_text(), "muted", wrap=True)
        h.add(flow(button("Latency test (20 pings)", "secondary", e.latency_test)))
        h.add(pw)
        e.on("latency", upd_lbl.setText)
        e.on("pad_state", lambda _k: pw.setText(e.power_text()))
        h.add(label("Several pads on one PC: start one app per pad with   --config pad2.json --port COM7 --api-port 8766 .", "muted", wrap=True))
        h.stretch()
        return PageGrid({"look": a, "misc": h}, {"wide": [[("look", 3)], [("misc", 3)]], "medium": [[("look", 3)], [("misc", 3)]]}, order=["look", "misc"],
                        titles={"look": "Appearance", "misc": "Hotkey & updates"}, tabs_factory=shell.make_tabs, wide_min=800, medium_min=800)

    # ------------------------------------------------------------------ activity log
    def _log(self):
        e, shell = self.engine, self.shell
        c = Card("Activity", scroll=False)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setMaximumBlockCount(500)
        view.setPlainText("\n".join(e.log_lines))
        c.add(view, 1)
        c.add(flow(button("Clear", "ghost", lambda: (e.log_lines.clear(), view.clear()))))
        e.on("log", view.appendPlainText)
        return PageGrid({"l": c}, {"wide": [[("l", 1)]], "medium": [[("l", 1)]]}, order=["l"], titles={"l": "Activity"}, tabs_factory=shell.make_tabs, wide_min=300, medium_min=300)

