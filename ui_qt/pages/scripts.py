"""Scripts (Advanced): write macro scripts with loops, conditions and variables, check them, dry-run them, run them, put them on a key."""
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit

from desk_lib import scriptconst, scripting
from ui_qt.forms import TargetKeyCombo, combo, line, val
from ui_qt.layouts import PageGrid
from ui_qt.pages.base import Page
from ui_qt.theme import theme
from ui_qt.widgets import Card, Field, button, flow, label, tr


class ScriptsPage(Page):
    id, title, subtitle, icon, advanced = "scripts", "Scripts", "Loops, conditions and variables for your keys", "scripts", True

    def __init__(self, shell):
        super().__init__(shell)
        e = self.engine
        # ---------------------------------------------------------------- editor card
        c = Card("Macro scripts", "A script is a small program for one key: repeat things, do different things depending on the program in front, remember values, call other "
                 "scripts. The app runs it, so it must be running; the pad only asks for it.")
        self.pick = combo([""], "", 20)
        self.name = line("script name", "", 220)
        self.pick.activated.connect(lambda _i: self.load(val(self.pick)))
        c.add(flow(self.pick, self.name))
        c.add(flow(button("New", "secondary", self.new), button("Save", "primary", self.save), button("Delete", "secondary", lambda: e.script_delete(val(self.pick)))))
        self.editor = QPlainTextEdit()
        f = QFont("DejaVu Sans Mono")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPixelSize(theme.px(13))
        self.editor.setFont(f)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setMinimumHeight(theme.px(200))
        c.add(self.editor, 1)
        self.pretend = line("dry run: pretend this window is focused", "", 260)
        c.add(flow(button("Check", "secondary", self.check), button("Dry run", "secondary", self.dry), button("Run in 3 s", "good", self.run_now),
                   button("Stop", "secondary", e.script_stop_request)))
        c.add(self.pretend)
        c.add(flow(TargetKeyCombo(e), button("Assign to the target key", "primary", lambda: e.script_assign(self.name.text().strip()))))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(theme.px(90))
        c.add(label("Result", "muted"))
        c.add(self.log)

        # ---------------------------------------------------------------- tools card
        t = Card("Templates, versions and sharing")
        self.tpl = combo(list(scripting.TEMPLATES), None, 24)
        self.tpl.insertItem(0, tr("Start from a ready-made script..."), "")
        self.tpl.setCurrentIndex(0)
        self.tpl.activated.connect(lambda _i: self.use_template(val(self.tpl)))
        t.add(Field("Ready-made script", self.tpl))
        self.hist = combo(["(none)"], None, 20)
        t.add(Field("Earlier versions", self.hist))
        t.add(flow(button("Restore that version", "secondary", self.restore_version)))
        self.url = line("https:// address of a shared script (plain text)", "", 420)
        t.add(Field("Import", self.url))
        t.add(flow(button("Import from the address", "secondary", self.import_url)))
        t.add(label(scriptconst.HELP, "muted", wrap=True))
        t.stretch()

        self.grid = PageGrid({"script": c, "tools": t}, {"wide": [[("script", 3)], [("tools", 2)]], "medium": [[("script", 3)], [("tools", 2)]]},
                             order=["script", "tools"], titles={"script": "Script", "tools": "Tools"}, tabs_factory=shell.make_tabs, wide_min=800, medium_min=800)
        self.root.addWidget(self.grid, 1)
        e.on("scripts", self.refresh)
        e.on("cfg_replaced", self.refresh)
        self.refresh()

    # ---- storage
    def refresh(self, select=None):
        e = self.engine
        names = e.script_names()
        typed = self.name.text().strip()
        cur = typed if typed in names else val(self.pick) if val(self.pick) in names else (names[0] if names else "")
        if isinstance(select, str) and select in names:
            cur = select
        self.pick.blockSignals(True)
        self.pick.clear()
        for n in names or [""]:
            self.pick.addItem(n, n)
        self.pick.setCurrentIndex(max(0, self.pick.findData(cur)))
        self.pick.blockSignals(False)
        self.load(cur)

    def load(self, name):
        e = self.engine
        self.name.setText(name)
        self.editor.setPlainText(e.script_source(name))
        items = e.script_history(name)
        self.hist.clear()
        for h in items or [{"t": "(none)"}]:
            self.hist.addItem(h["t"], h["t"])

    def new(self):
        self.pick.setCurrentIndex(-1)
        self.name.clear()
        self.editor.setPlainText(scriptconst.EXAMPLE)
        self.name.setFocus()

    def save(self):
        n = self.engine.script_save(self.name.text(), self.editor.toPlainText())
        if n:
            self.refresh(n)

    def use_template(self, key):
        if not key:
            return
        self.new()
        self.editor.setPlainText(scripting.TEMPLATES[key])
        self.name.setText("".join(ch for ch in key.split(":")[0] if ch.isalnum() or ch in " _-.").strip()[:30])
        self.engine.set_status("Template loaded - change it and press Save")
        self.tpl.setCurrentIndex(0)

    def restore_version(self):
        src = self.engine.script_version(val(self.pick), val(self.hist))
        if src is not None:
            self.editor.setPlainText(src)

    def import_url(self):
        def done(text, leaf):
            self.new()
            self.editor.setPlainText(text)
            self.name.setText(leaf)
        self.engine.script_import_url(self.url.text(), done)

    # ---- actions
    def say(self, lines):
        self.log.setPlainText("\n".join(lines))

    def check(self):
        ok, msg = self.engine.script_check(self.editor.toPlainText())
        self.say([msg])

    def dry(self):
        self.say(self.engine.script_dry(self.editor.toPlainText(), self.pretend.text()))

    def run_now(self):
        self.engine.script_run_later(self.name.text().strip(), self.editor.toPlainText(), self.say)
