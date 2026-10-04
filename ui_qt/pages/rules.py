"""Rules: programs (the pad's layer follows the focused program), schedules, computer actions, and - in Advanced - the local API and plugins."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from desk_lib import autorules, presets
from ui_qt.forms import RowList, cfg_switch, combo, line, val
from ui_qt.layouts import PageGrid
from ui_qt.media import TabArea
from ui_qt.pages.base import Page
from ui_qt.widgets import Card, CheckBox, Field, ToggleRow, button, flow, label, separator, set_role, tr

LAYERS3 = ["Layer 1", "Layer 2", "Layer 3"]


class RulesPage(Page):
    id, title, subtitle, icon = "rules", "Rules", "Programs, schedules and computer actions", "rules"

    def __init__(self, shell):
        super().__init__(shell)
        self.programs = self._programs()
        self.schedules = self._schedules()
        self.computer = self._computer()
        self.api = self._api()
        self.plugins = self._plugins()
        self.tabs = TabArea({"Programs": self.programs, "Schedules": self.schedules, "Computer": self.computer, "API & CLI": self.api, "Plugins": self.plugins}, "Programs")
        self.root.addWidget(self.tabs, 1)
        self.apply_advanced(bool(self.engine.cfg.get("advanced")))

    def apply_advanced(self, on):
        for k in ("API & CLI", "Plugins"):
            self.tabs.set_tab_visible(k, on)

    def show_section(self, section):
        m = {"programs": "Programs", "schedules": "Schedules", "computer": "Computer", "api": "API & CLI", "plugins": "Plugins"}.get(section)
        if m:
            self.tabs.show_tab(m)

    # ------------------------------------------------------------------ programs
    def _programs(self):
        e, shell = self.engine, self.shell
        # ---- automatic layer
        a = Card("Automatic layer")
        on = ToggleRow("Switch the pad's layer automatically", bool(e.cfg.get("profiles_on")))
        on.toggled.connect(e.profiles_set)
        a.add(on)
        a.add(label("The app watches which program has the keyboard focus. The first matching rule decides the layer; your key maps for each layer are edited on the Keys page.", "muted", wrap=True))
        default = combo(["keep the current layer"] + LAYERS3, e.default_label(e.cfg.get("profile_default", 0)), 20)
        default.activated.connect(lambda _i: e.profile_default_set(val(default)))
        a.add(Field("When no rule matches:", default))
        live = label("", "accent", wrap=True)
        a.add(live)
        e.on("profile_label", live.setText)
        live.setText(e._profile_state["text"])
        a.add(cfg_switch(e, "Show the program's name on the pad when the layer switches (firmware 1.6)", "pad_ctx", True))
        remember = CheckBox("remember my layer per program (when no rule matches)", bool(e.cfg.get("remember_layers")))
        remember.toggled.connect(e.remember_set)
        a.add(remember)
        a.stretch()
        # ---- presets
        pr = Card("Ready-made key layouts")
        pr.add(label("Fills one layer with the usual shortcuts of a program (meetings, drawing, video editing, spreadsheets, writing, 3D, coding) - and can add the program rule. "
                     "Uses each program's default shortcuts.", "muted", wrap=True))
        pset = combo(list(presets.PRESETS), list(presets.PRESETS)[0], 24)
        player = combo(LAYERS3, "Layer 3", 8)
        prule = CheckBox("also add the program rule", True)
        pr.add(Field("Layout", pset))
        pr.add(Field("onto", player))
        pr.add(prule)
        pr.add(flow(button("Apply", "primary", lambda: e.apply_preset(val(pset), int(val(player).split()[-1]) - 1, prule.isChecked()))))
        pr.stretch()
        # ---- rules
        r = Card("Rules (first match wins)")
        rows = RowList()
        r.add(rows)
        name, match = line("name", "", 140), line("program or window text", "", 200)
        kind = combo(["either", "process", "title"], "either", 8)
        layer = combo(LAYERS3, "Layer 3", 8)
        tw, days = line("time 09:00-17:00", "", 150), line("days mon-fri", "", 120)
        game = CheckBox("game mode")

        def add_rule(n=None, m=None, k=None, ly=None):
            ok = e.profile_add(n if n is not None else name.text(), m if m is not None else match.text(), k or val(kind),
                               ly if ly is not None else int(val(layer).split()[-1]) - 1, tw.text(), days.text(), game.isChecked())
            if ok and n is None:
                name.clear()
                match.clear()
        r.add(label("Add a rule", "h3"))
        r.add(flow(name, match, kind, layer))
        r.add(label("Options for the next rule you add:", "muted"))
        r.add(flow(tw, days, game))
        r.add(flow(button("Add rule", "primary", add_rule), button("Use the program I focus in 3 s", "secondary", e.profile_capture)))
        quick = [button(n_, "secondary", lambda a=(n_, m_, k_, l_): add_rule(*a)) for n_, m_, k_, l_ in
                 (("VS Code", "code", "process", 2), ("Browser", "firefox", "process", 2), ("Spotify", "spotify", "process", 1), ("Zoom", "zoom", "process", 1))]
        choices = presets.profile_choices()
        more = combo(["More programs..."] + [c[0] for c in choices], "More programs...", 16)

        def more_picked():
            n_ = val(more)
            c = next((c for c in choices if c[0] == n_), None)
            if c:
                add_rule(c[0], c[1], c[2], int(val(layer).split()[-1]) - 1)
            more.setCurrentIndex(0)
        more.activated.connect(lambda _i: more_picked())
        r.add(label("Quick add:", "muted"))
        r.add(flow(*quick, more))
        r.stretch()

        def refresh():
            rows.clear()
            rules = e.cfg["profiles"]
            if not rules:
                rows.empty("No rules yet. Add one below, or use 'Quick add'.")
            for i, ru in enumerate(rules):
                en = CheckBox("", bool(ru.get("enabled", True)))
                en.toggled.connect(lambda v, i=i: e.profile_edit(i, enabled=bool(v)))
                extra = "".join([f"  {ru['time']}" if ru.get("time") else "", f"  {ru['days']}" if ru.get("days") else "", "  GAME" if ru.get("game") else ""])
                lay = combo(LAYERS3, f"Layer {int(ru.get('layer', 0)) + 1}", 8)
                lay.activated.connect(lambda _i, i=i, lay=lay: e.profile_edit(i, layer=int(val(lay).split()[-1]) - 1))
                rows.add_row([en, label(ru.get("name") or ru["match"], "h3"), label(f"{ru.get('kind', 'either')}: \"{ru['match']}\"{extra}", "muted"), lay,
                              button("↑", "ghost", lambda i=i: e.profile_move(i, -1)), button("↓", "ghost", lambda i=i: e.profile_move(i, 1)),
                              button("Remove", "secondary", lambda i=i: e.profile_remove(i))])
        e.on("profiles", refresh)
        refresh()

        def captured(proc, title):
            match.setText(proc or title)
            from ui_qt.forms import set_val
            set_val(kind, "process" if proc else "title")
            name.setText((proc or title)[:20])
        e.on("profile_captured", captured)
        return PageGrid({"auto": a, "presets": pr, "rules": r}, {"wide": [[("auto", 3), ("presets", 3)], [("rules", 3)]], "medium": [[("auto", 3), ("presets", 3)], [("rules", 3)]]},
                        order=["auto", "rules", "presets"], titles={"auto": "Automatic", "rules": "Rules", "presets": "Layouts"}, tabs_factory=shell.make_tabs, wide_min=800, medium_min=800)

    # ------------------------------------------------------------------ schedules
    def _schedules(self):
        e, shell = self.engine, self.shell
        l = Card("Scheduled actions", "Do something at a set time while this app is running: switch the layer when work starts, dim the screen in the evening, open your daily page, "
                 "remind you to stretch. A rule that was due while the app was closed is skipped, never run late.")
        rows = RowList()
        l.add(rows)
        l.stretch()

        def refresh():
            rows.clear()
            rules = e.cfg["schedules"]
            if not rules:
                rows.empty("No rules yet.")
            for i, ru in enumerate(rules):
                en = CheckBox("", bool(ru.get("enabled", True)))
                en.toggled.connect(lambda v, i=i: e.schedule_enable(i, bool(v)))
                from desk_lib import scheduler
                nxt = e.schedule_next(ru)
                rows.add_row([en, label(ru.get("name") or "Rule", "h3"), label(scheduler.describe(ru), "muted"), label(f"next: {nxt}" if nxt else "off", "muted"),
                              button("Run now", "secondary", lambda ru=ru: e.run_schedule(ru, manual=True)), button("Remove", "secondary", lambda i=i: e.schedule_remove(i))])
        e.on("schedules", refresh)
        refresh()
        a = Card("Add a rule")
        when = combo(autorules.WHEN_KINDS, autorules.WHEN_KINDS[1], 16)
        when_arg = line("09:00", "", 170)
        when_hint = label("time, 24 h", "muted")
        do = combo(list(autorules.DO_KINDS), "Switch layer", 18)
        do_arg = line(autorules.DO_KINDS["Switch layer"][2], "", 330)
        nm = line("name (optional)", "", 190)

        def when_changed():
            hint = {"Every N minutes": ("30", "minutes (1 to 1440)"), "Once": ("2026-10-03 15:00", "date and time")}.get(val(when), ("09:00", "time, 24 h"))
            when_arg.clear()
            when_arg.setPlaceholderText(hint[0])
            when_hint.setText(tr(hint[1]))

        def do_changed():
            do_arg.clear()
            do_arg.setPlaceholderText(tr(autorules.DO_KINDS[val(do)][2]))
        when.activated.connect(lambda _i: when_changed())
        do.activated.connect(lambda _i: do_changed())

        def add():
            if e.schedule_add(val(when), when_arg.text() or when_arg.placeholderText(), val(do), do_arg.text(), nm.text()):
                nm.clear()
                do_arg.clear()
        a.add(Field("When", when))
        a.add(flow(when_arg, when_hint))
        a.add(Field("Do", do))
        a.add(do_arg)
        a.add(nm)
        a.add(flow(button("Add rule", "primary", add)))
        a.stretch()
        return PageGrid({"list": l, "add": a}, {"wide": [[("list", 3)], [("add", 2)]], "medium": [[("list", 3)], [("add", 2)]]}, order=["list", "add"],
                        titles={"list": "Rules", "add": "Add"}, tabs_factory=shell.make_tabs, wide_min=800, medium_min=800)

    # ------------------------------------------------------------------ computer actions
    def _computer(self):
        e, shell = self.engine, self.shell
        ai = Card("AI action", "The 'Ask the AI' key action sends your prompt (and the clipboard, if you use {clipboard}) to the Claude API with YOUR key and types the answer. "
                  "The key is stored in this app's settings file in plain text.")
        key = line("Anthropic API key (sk-ant-...)", e.cfg["ai"].get("key", ""), 400, password=True)
        model = line("model", e.cfg["ai"].get("model", ""), 300)
        ai.add(key)
        ai.add(model)
        ai.add(flow(button("Save", "primary", lambda: e.save_ai(key.text(), model.text()))))
        ai.stretch()
        sh = Card("Screenshots")
        folder = line("folder (empty = your Pictures folder)", e.cfg.get("shot_dir", ""), 500)
        sh.add(folder)
        sh.add(flow(button("Save", "secondary", lambda: e.save_shot(folder.text())), button("Take one now", "primary", lambda: e.shot_now(folder.text()))))
        sh.stretch()
        ch = Card("Clipboard history")
        sw = ToggleRow("Remember the last 9 copied texts (memory only, never saved)", bool(e.cfg.get("cliphist_on")))
        sw.toggled.connect(e.hist_set)
        ch.add(sw)
        ch.add(flow(button("Forget them", "secondary", lambda: e.cliphist.clear())))
        ch.stretch()
        wl = Card("Window layouts")
        lname = line("layout name, e.g. work", "", 220)
        wl.add(lname)
        wl.add(flow(button("Save the current windows", "primary", lambda: e.layout_save(lname.text())), button("Restore", "secondary", lambda: e.layout_restore(lname.text())),
                    button("Delete", "secondary", lambda: e.layout_delete(lname.text()))))
        lt = label(e.layouts_text(), "muted", wrap=True)
        wl.add(lt)
        e.on("layouts", lambda: lt.setText(e.layouts_text()))
        wl.stretch()
        return PageGrid({"ai": ai, "shots": sh, "hist": ch, "layouts": wl}, {"wide": [[("ai", 3), ("shots", 2)], [("hist", 2), ("layouts", 3)]], "medium": [[("ai", 3), ("shots", 2)], [("hist", 2), ("layouts", 3)]]},
                        order=["ai", "shots", "hist", "layouts"], titles={"ai": "AI", "shots": "Screenshots", "hist": "Clipboard", "layouts": "Windows"},
                        tabs_factory=shell.make_tabs, wide_min=800, medium_min=800)

    # ------------------------------------------------------------------ local API (Advanced)
    def _api(self):
        e, shell = self.engine, self.shell
        c = Card("Local API and command line", "Lets scripts, shortcuts and other programs on THIS computer drive the pad while the app runs (switch layer, set the LED, show a custom card, "
                 "press a key, send a notification). Off by default; protected by a secret token; never reachable from the network or from web pages.")
        on = ToggleRow("Enable the local API", bool(e.cfg["api"]["on"]))
        port = line("port", str(e.cfg["api"]["port"]), 90)
        on.toggled.connect(lambda v: e.api_enable(bool(v), port.text()))
        c.add(on)
        c.add(Field("Port", port))
        c.add(flow(button("New token", "secondary", e.api_new_token), button("Copy token", "secondary", e.api_copy_token)))
        state = label("", "muted", wrap=True)
        helptext = QLabel("")
        helptext.setProperty("role", "mono")
        helptext.setWordWrap(True)
        helptext.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        c.add(state)
        c.add(helptext)
        c.stretch()

        def refresh():
            text, kind, h = e.api_text()
            state.setText(text)
            set_role(state, kind)
            helptext.setText(h)
            on.setChecked(bool(e.api))
        e.on("api_state", refresh)
        refresh()
        return PageGrid({"api": c}, {"wide": [[("api", 1)]], "medium": [[("api", 1)]]}, order=["api"], titles={"api": "API"}, tabs_factory=shell.make_tabs, wide_min=300, medium_min=300)

    # ------------------------------------------------------------------ plugins (Advanced)
    def _plugins(self):
        e, shell = self.engine, self.shell
        c = Card("Plugins", "Python files that add key actions. A plugin can do anything this app can - only allow the ones you trust.")
        on = ToggleRow("Allow plugins (Python files that add key actions)", bool(e.cfg.get("plugins_on")))
        on.toggled.connect(e.plugins_set)
        c.add(on)
        c.add(flow(button("Open the folder", "secondary", e.plugins_folder), button("Add an example", "secondary", e.plugins_example), button("Reload", "secondary", e.plugins_reload)))
        note = label(e.plugins_text(), "muted", wrap=True)
        c.add(note)
        c.add(separator())
        c.stretch()
        e.on("plugins", lambda: note.setText(e.plugins_text()))
        return PageGrid({"plugins": c}, {"wide": [[("plugins", 1)]], "medium": [[("plugins", 1)]]}, order=["plugins"], titles={"plugins": "Plugins"}, tabs_factory=shell.make_tabs,
                        wide_min=300, medium_min=300)
