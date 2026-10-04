"""Inspector tabs of the Keys page that build actions: key combo + text + computer actions, the macro sequence, gestures, alternate / random keys."""
from PySide6.QtWidgets import QLineEdit, QWidget

from core.base import ACTION_KINDS, MEDIA_CHOICES, MODIFIERS, KEY_CHOICES, describe_spec
from desk_lib import padconst, textops
from ui_qt.forms import RowList, combo, line, list_widget, text_edit, val
from ui_qt.layouts import vbox
from ui_qt.widgets import CheckBox, button, flow, label, tr


def build_builder(page):
    e = page.engine
    w = QWidget()
    l = vbox(w, spacing=10)
    l.addWidget(label("Text is typed as US-layout ASCII (see README).", "muted", wrap=True))

    # ---- key combination
    l.addWidget(label("Key combination (up to 4 modifiers + main key)", "h3", wrap=True))
    mods = [combo(MODIFIERS, "CTRL" if i == 0 else "-", 6) for i in range(4)]
    key = combo(KEY_CHOICES, "c", 6)
    key.setEditable(True)
    page.mod_boxes, page.key_box = mods, key
    l.addWidget(flow(*mods, key))

    def combo_keys():
        return e.combo_keys([val(m) for m in mods], key.currentText())
    l.addWidget(flow(
        button("Assign to key", "primary", lambda: e.guard(lambda: (lambda k: e.assign_spec(("combo", k), describe_spec(("combo", k))))(combo_keys()))),
        button("Add to sequence", "secondary", lambda: e.guard(lambda: e.seq_add({"combo": combo_keys()}))),
        button("Test", "good", lambda: e.guard(lambda: (lambda k: e.test_spec(("combo", k), describe_spec(("combo", k))))(combo_keys())))))

    # ---- text
    l.addWidget(label("Text snippet auto-typer", "h3"))
    text = text_edit("Text to type", 64)
    enter = CheckBox("Press Enter afterwards")
    page.text_box = text
    l.addWidget(text)
    l.addWidget(enter)

    def text_value():
        return e.text_value(text.toPlainText(), enter.isChecked())
    l.addWidget(flow(
        button("Assign to key", "primary", lambda: e.guard(lambda: (lambda t: e.assign_spec(("text", t), "Text: " + t.strip().replace("\n", " ")[:24]))(text_value()))),
        button("Add to sequence", "secondary", lambda: e.guard(lambda: e.seq_add({"text": text_value()}))),
        button("Test", "good", lambda: e.guard(lambda: e.test_spec(("text", text_value()), "Text snippet")))))

    # ---- computer / mouse / layer actions
    l.addWidget(label("Computer, mouse and layer actions", "h3"))
    l.addWidget(label("Open a website or program, run a command, type the clipboard, click or scroll with the pad's USB mouse, or switch the pad's layer. "
                      "The pad asks this app to open things, so the app must be running for those.", "muted", wrap=True))
    kinds = [k[0] for k in ACTION_KINDS]
    kind = combo(kinds, kinds[0], 18)
    arg = QLineEdit()
    arg.setPlaceholderText(tr(ACTION_KINDS[0][2]))
    transform = combo([v[0] for v in textops.TRANSFORMS.values()], textops.TRANSFORMS["upper"][0], 18)
    transform.hide()
    page.act_kind, page.act_arg, page.act_transform = kind, arg, transform

    def kind_changed():
        k = next(x for x in ACTION_KINDS if x[0] == val(kind))
        is_clip = k[1] == "clip"
        arg.setVisible(not is_clip)
        transform.setVisible(is_clip)
        arg.clear()
        arg.setPlaceholderText(tr(k[2]))
        arg.setEnabled(bool(k[3]))
    kind.currentTextChanged.connect(lambda _t: kind_changed())
    l.addWidget(flow(kind))
    l.addWidget(arg)
    l.addWidget(transform)

    def spec():
        return e.action_spec(val(kind), arg.text(), val(transform))
    l.addWidget(flow(
        button("Assign to key", "primary", lambda: e.guard(lambda: (lambda s: e.assign_spec(s, describe_spec(s)[:40]))(spec()))),
        button("Add to sequence", "secondary", lambda: e.guard(lambda: e.seq_add(e.action_step(spec())))),
        button("Test", "good", lambda: e.guard(lambda: e.test_spec(spec(), "Action")))))
    l.addStretch(1)
    return w


def build_sequence(page):
    e = page.engine
    w = QWidget()
    l = vbox(w, spacing=10)
    l.addWidget(label("Macro sequence (with delays)", "h3"))
    lst = list_widget(150, multi=True)
    page.seq_list = lst
    l.addWidget(lst)

    def refresh():
        lst.clear()
        lst.addItems(e.seq_lines())
    e.on("sequence", refresh)

    def move(d):
        rows = [i.row() for i in lst.selectedIndexes()]
        if rows:
            j = e.seq_move(rows[0], d)
            lst.setCurrentRow(j)
    l.addWidget(flow(button("Move up", "secondary", lambda: move(-1)), button("Move down", "secondary", lambda: move(1)),
                     button("Remove", "secondary", lambda: e.seq_remove([i.row() for i in lst.selectedIndexes()])), button("Clear", "secondary", e.seq_clear)))
    delay = line("ms", "200", 90)
    l.addWidget(flow(delay, button("Add delay (ms)", "secondary", lambda: e.guard(lambda: e.seq_add_delay(delay.text())))))
    media = combo(MEDIA_CHOICES, "PLAY_PAUSE", 12)
    l.addWidget(flow(media, button("Add media key", "secondary", lambda: e.guard(lambda: e.seq_add({"media": val(media)})))))
    mouse = CheckBox("also the mouse")
    rec = button("Record keystrokes", "secondary", lambda: e.rec_toggle(with_mouse=mouse.isChecked()))
    page.rec_btn = rec

    def recording(on):
        rec.setText(tr("Stop recording") if on else tr("Record keystrokes"))
        rec.set_variant("danger" if on else "secondary")
    e.on("recording", recording)
    l.addWidget(flow(rec, mouse))
    name = line("macro name", "My macro", 220)
    l.addWidget(flow(name))
    l.addWidget(flow(button("Assign sequence to key", "primary", lambda: e.guard(lambda: e.assign_spec(e.seq_spec(), name.text().strip() or "My macro"))),
                     button("Save to library only", "secondary", lambda: e.save_seq(name.text())),
                     button("Test sequence", "good", lambda: e.guard(lambda: e.test_spec(e.seq_spec(), "Sequence")))))
    l.addStretch(1)
    refresh()
    return w


def build_gestures(page):
    e = page.engine
    w = QWidget()
    l = vbox(w, spacing=10)
    l.addWidget(label("Key gestures: hold, double-tap, triple-tap, chords, dial clicks", "h3", wrap=True))
    l.addWidget(label("Give K1 to K5 more actions. A key that has a hold, double-tap or triple-tap action reacts when you let go (a multi-tap waits "
                      "a quarter of a second to see whether another tap follows); keys without one still act the instant you press them. A chord is two neighbouring keys pressed "
                      "together (those two keys wait 45 ms to see whether the other follows). Hold / double-tap need firmware 1.3, the rest 1.5.", "muted", wrap=True))
    rows = RowList()
    l.addWidget(rows)

    def refresh():
        rows.clear()
        items = e.gesture_items()
        if not items:
            rows.empty("No gestures yet.")
        for k, m in items:
            layer, slot, g = padconst.parse_gesture_key(k)
            rows.add_row([label(f"Layer {layer + 1}  {padconst.gesture_text(slot, g)}", "h3"), label(f"{m['cat']}: {m['action']}", "muted"),
                          button("Remove", "secondary", lambda k=k: e.gesture_remove(k))])
    e.on("gestures", refresh)
    layer = combo(["Layer 1", "Layer 2", "Layer 3"], "Layer 1", 8)
    slot = combo(["K1", "K2", "K3", "K4", "K5"] + list(padconst.DIAL_PRESS), "K1", 16)
    gest = combo([n for n, _g in padconst.GESTURES], padconst.GESTURES[0][0], 14)
    cat, action = _cat_action_pair(e)
    l.addWidget(label("Add a gesture", "h3"))
    l.addWidget(flow(layer, slot, gest))
    l.addWidget(label("(the gesture choice only applies to K1-K5; chords and dial clicks need firmware 1.5)", "muted", wrap=True))
    l.addWidget(flow(cat, action, button("Assign", "primary", lambda: e.guard(lambda: e.gesture_assign(
        int(val(layer).split()[-1]) - 1, val(slot), val(gest), val(cat), val(action))))))

    # ---- alternate / random
    l.addWidget(label("Alternating and random keys", "h3"))
    l.addWidget(label("Alternate: every press runs the other of two actions (mute / unmute, play / next ...). Random: every press runs one of 2-6 actions "
                      "(a different greeting, a random emoji). Pick the actions below, then assign the result to the target key.", "muted", wrap=True))
    kind = combo(["Alternate between two actions", "Pick one at random"], "Alternate between two actions", 24)
    cat2, action2 = _cat_action_pair(e)
    picks = []
    lbl = label("", wrap=True)

    def is_alt():
        return kind.currentIndex() == 0

    def show_picks():
        lim = 2 if is_alt() else 6
        lbl.setText(("  /  ".join(f"{i + 1}. {a}" for i, (_c, a, _s) in enumerate(picks))) or f"(add {lim} action{'s' if lim > 1 else ''})")

    def kind_changed():
        del picks[(2 if is_alt() else 6):]
        show_picks()
    kind.currentIndexChanged.connect(lambda _i: kind_changed())

    def add():
        try:
            picks.append(e.choice_pick(picks, is_alt(), val(cat2), val(action2)))
        except ValueError as ex:
            return e.set_status(str(ex), error=True)
        show_picks()

    def assign():
        e.guard(lambda: e.choice_assign(picks, is_alt()))
    l.addWidget(flow(kind))
    l.addWidget(flow(cat2, action2, button("Add", "secondary", add)))
    l.addWidget(lbl)
    l.addWidget(flow(button("Assign to the target key", "primary", assign), button("Clear the list", "secondary", lambda: (picks.clear(), show_picks()))))
    show_picks()
    refresh()
    l.addStretch(1)
    return w


def _cat_action_pair(e):
    """Category + action combos that follow the library (custom actions appear / disappear)."""
    cats = e.categories()
    cat = combo(cats, cats[0], 12)
    action = combo(e.names_for(cats[0]), None, 18)

    def cat_changed():
        names = e.names_for(val(cat)) or [""]
        action.clear()
        for n in names:
            action.addItem(n, n)

    def reload():
        cur = val(cat)
        cats2 = e.categories()
        cat.blockSignals(True)
        cat.clear()
        for c_ in cats2:
            cat.addItem(c_, c_)
        cat.blockSignals(False)
        if cur in cats2:
            cat.setCurrentIndex(cats2.index(cur))
        cat_changed()
    cat.activated.connect(lambda _i: cat_changed())
    e.on("library", reload)
    return cat, action


__all__ = ["build_builder", "build_sequence", "build_gestures"]
