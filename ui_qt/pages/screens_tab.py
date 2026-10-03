"""Display -> Screens: which screens the dial cycles through, reminders and habits (firmware 1.4 / 1.5)."""
from ui_qt.forms import line
from ui_qt.layouts import PageGrid
from ui_qt.widgets import Card, CheckBox, button, flow, label, separator
from desk_lib import padconst


def build_screens_tab(page):
    e = page.engine
    shell = page.shell
    state = {"busy": False}

    # ---------------------------------------------------------------- screens in the cycle
    c = Card("Screens in the cycle")
    boxes = []
    widgets = []
    for i, name in enumerate(padconst.SCREEN_NAMES):
        cb = CheckBox(f"{i + 1} {name}", i < 6)
        cb.toggled.connect(lambda _v: changed())
        boxes.append(cb)
        widgets.append(cb)
    c.add(flow(*boxes, hspace=14))
    c.add(label("Screens 7-20 are drawn by the pad itself and use K1-K5 (for example K1 = start / stop on the stopwatch, the dial steers the games); "
                "your key actions are back on every other screen.", "muted", wrap=True))
    note = label("", "muted", wrap=True)
    c.add(note)
    c.stretch()

    def mask_value():
        return sum(1 << i for i, b in enumerate(boxes) if b.isChecked())

    def changed():
        if state["busy"] or not e.screens_ok():
            return
        mask = e.set_mode_mask(mask_value())
        if mask == 1 and not boxes[0].isChecked():
            state["busy"] = True
            boxes[0].setChecked(True)
            state["busy"] = False

    # ---------------------------------------------------------------- reminders + habits
    r = Card("Reminders and habits")
    r.add(label("Reminders", "h3"))
    rem = []
    for i in range(3):
        mins = line("min", "", 70)
        txt = line(("Drink water", "Stand up", "Look away 20 s")[i], "", 230)
        rem.append((mins, txt))
        r.add(flow(mins, label("minutes  -  show"), txt, button("Show now", "secondary", lambda i=i: e.test_reminder(i))))
        widgets += [mins, txt]
    r.add(flow(button("Save reminders", "primary", lambda: e.save_reminders([(m.text(), t.text()) for m, t in rem])),
               label("0 or empty = off. The pad shows the text full-screen and also tells this app (a notification).", "muted")))
    r.add(separator())
    r.add(label("Habits (screen 12): five things to tick off every day", "h3", wrap=True))
    hab = []
    for d in e.HABIT_DEFAULTS:
        ed = line(d, "", 120)
        hab.append(ed)
        widgets.append(ed)
    r.add(flow(*hab))
    r.add(flow(button("Save names", "primary", lambda: e.save_habits([h.text() for h in hab])), button("Refresh", "secondary", e.load_habits)))
    habit_lbl = label("", "muted", wrap=True)
    r.add(habit_lbl)
    r.stretch()

    # ---------------------------------------------------------------- availability and loading
    def refresh_state():
        ok = e.screens_ok()
        for w in widgets:
            w.setEnabled(ok)
        if ok and not e.pad_has("games"):
            for b in boxes[12:]:
                b.setEnabled(False)
        note.setText(e.screens_note())
    e.on("screens_state", refresh_state)
    e.on("connected", lambda *_: refresh_state())
    e.on("disconnected", refresh_state)

    def loaded(st, rm, hb):
        state["busy"] = True
        try:
            mask = int(st.get("mode_mask", 0x3F))
            for i, b in enumerate(boxes):
                b.setChecked(bool((mask >> i) & 1))
            for (mins, txt), item in zip(rem, rm.get("list", [])):
                mins.setText(str(item["m"]) if item.get("m") else "")
                txt.setText(item.get("t", "") if item.get("m") else "")
            for ed, nm in zip(hab, hb.get("names", [])):
                ed.setText(nm)
        finally:
            state["busy"] = False
        habit_lbl.setText(e.habit_text(hb))
    e.on("screens_loaded", loaded)
    e.on("habits", habit_lbl.setText)
    refresh_state()
    return PageGrid({"cycle": c, "reminders": r}, {"wide": [[("cycle", 3)], [("reminders", 3)]], "medium": [[("cycle", 3)], [("reminders", 3)]]},
                    order=["cycle", "reminders"], titles={"cycle": "Screens", "reminders": "Reminders"}, tabs_factory=shell.make_tabs, wide_min=720, medium_min=720)
