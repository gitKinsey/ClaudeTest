"""Display -> Info screen: the cards the pad shows on its sixth screen, notification badges, preview and send controls."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QSlider

from desk_lib import extras
from ui_qt.forms import RowList, combo, line, val
from ui_qt.layouts import PageGrid
from ui_qt.media import ImageView
from ui_qt.widgets import Card, CheckBox, Field, ToggleRow, button, flow, label, separator, tr


def build_info_tab(page):
    e = page.engine
    shell = page.shell
    info = e.cfg["info"]
    switches = {}

    def sw(card, text, key, subtitle):
        t = ToggleRow(text, bool(info.get(key)))
        t.toggled.connect(lambda v, k=key: e.info_update(**{k: bool(v)}))
        switches[key] = t
        card.add(t)
        card.add(label(subtitle, "muted", wrap=True))
        return t

    # ---------------------------------------------------------------- cards
    c = Card("Cards")
    sw(c, "Now playing", "music", "Title and artist of what plays on this computer (Spotify, browsers, media players).")
    music_lbl = label("", "muted", wrap=True)
    c.add(music_lbl)
    c.add(separator())
    sw(c, "Weather", "weather", "Free service (Open-Meteo), no key needed. Updated every 10 minutes.")
    city = line("city, e.g. Zurich", info.get("city", ""), 220)
    fahr = CheckBox("Fahrenheit", bool(info.get("fahrenheit")))
    fahr.toggled.connect(lambda v: e.info_update(fahrenheit=bool(v)))
    c.add(flow(city, button("Find", "secondary", lambda: e.info_find_city(city.text())), fahr))
    place = label(f"place: {info['label']}" if info.get("label") else "no place chosen yet", "muted", wrap=True)
    c.add(place)
    c.add(separator())
    sw(c, "Next calendar event", "event", "Reads a calendar file (.ics, e.g. exported from Google / Outlook / Apple Calendar) or a private .ics link. Repeating events are not expanded.")
    ics = line("path to a .ics file or an https:// link", info.get("ics", ""), 360)

    def browse():
        p = e.ui.ask_open("Calendar file", "Calendar files (*.ics);;All files (*)")
        if p:
            ics.setText(p)
            switches["event"].setChecked(True)
            e.info_update(event=True, ics=p)
    c.add(flow(ics, button("Browse...", "secondary", browse), button("Use", "primary", lambda: e.info_update(ics=ics.text().strip()))))
    event_lbl = label("", "muted", wrap=True)
    c.add(event_lbl)
    c.add(separator())
    sw(c, "Custom card", "custom", "Any text you like - a motto, a reminder, a status line (ASCII only, ~20 characters per line).")
    custom = {}
    fields = []
    for k, ph in (("c_label", "small heading"), ("c_t", "big text"), ("c_a", "line 1"), ("c_b", "line 2")):
        ed = line(ph, info.get(k, ""), 150)
        ed.textEdited.connect(lambda t, k=k: e.info_update(save_only=True, **{k: t}))
        custom[k] = ed
        fields.append(ed)
    c.add(flow(*fields))
    c.stretch()

    # ---------------------------------------------------------------- more cards + badges
    m = Card("More cards and badges")
    m.add(label("Countdowns, other time zones, a git repository's state, the latest GitHub Actions run, a crypto price. The pad shows up to 4 cards in total.", "muted", wrap=True))
    rows = RowList()
    m.add(rows)
    kind = combo(list(extras.KINDS.values()), extras.KINDS["countdown"], 18)
    elabel = line("label", "", 120)
    earg = line(e.EXTRA_HINTS["countdown"], "", 230)

    def kind_changed():
        earg.clear()
        earg.setPlaceholderText(tr(e.EXTRA_HINTS[e.extra_kind_key(val(kind))]))
    kind.activated.connect(lambda _i: kind_changed())

    def add():
        if e.extra_add(val(kind), elabel.text(), earg.text()):
            elabel.clear()
            earg.clear()
    m.add(flow(kind, elabel, earg, button("Add card", "primary", add)))

    def refresh_extras():
        rows.clear()
        items = e.cfg["info"]["extras"]
        if not items:
            rows.empty("None yet.")
        for i, it in enumerate(items):
            rows.add_row([label(extras.KINDS[it["type"]], "h3"), label((it["label"] + "  " if it["label"] else "") + it["arg"], "muted"),
                          button("Remove", "secondary", lambda i=i: e.extra_remove(i))])
    e.on("extras", refresh_extras)
    m.add(separator())
    m.add(label("Notification badges", "h2"))
    m.add(label("Small counters on the Info screen. Any script, IFTTT / Home-Assistant rule or mail filter can set one - the app listens on this computer only and needs the secret token:", "muted", wrap=True))
    url = line("", e.badges.url() if e.badges else "", 560)
    url.setReadOnly(True)
    m.add(url)
    m.add(flow(button("Copy", "secondary", e.info_copy_badge), button("Test badge", "primary", e.info_test_badge)))
    badge_lbl = label("no badges set", "muted", wrap=True)
    m.add(badge_lbl)
    m.stretch()

    # ---------------------------------------------------------------- preview
    p = Card("Preview")
    view = ImageView(240)
    p.add(view, 1)
    rot = QSlider(Qt.Orientation.Horizontal)
    rot.setRange(2, 30)
    rot.setValue(int(info.get("rot", 6)))
    rot.valueChanged.connect(lambda v: e.info_update(save_only=True, rot=int(v)))
    p.add(Field("Card rotation (seconds)", rot))
    p.add(flow(button("Send to the pad now", "primary", lambda: e.info_send_now(force=True)), button("Info screen on the pad", "secondary", e.info_show_on_pad)))
    p.add(flow(button("Album cover on the pad", "secondary", lambda: e.send_pad_image("art", "")),
               button("QR of the clipboard", "secondary", lambda: e.send_pad_image("qr", ""))))
    status = QLabel("")
    status.setWordWrap(True)
    status.setProperty("role", "muted")
    p.add(status)

    def done(d):
        music_lbl.setText(d["music"])
        event_lbl.setText(d["event"])
        badge_lbl.setText(d["badges"])
        status.setText(d["status"])
    e.on("info_done", done)

    def cfg_changed():
        i = e.cfg["info"]
        for k, t in switches.items():
            t.setChecked(bool(i.get(k)))
        place.setText(f"place: {i['label']} ({i['lat']:.2f}, {i['lon']:.2f})" if i.get("label") and i.get("lat") is not None else "no place chosen yet")
        city.setText(i.get("city", ""))
        ics.setText(i.get("ics", ""))
        fahr.setChecked(bool(i.get("fahrenheit")))
        for k, ed in custom.items():
            ed.setText(i.get(k, ""))
        rot.setValue(int(i.get("rot", 6)))
        refresh_extras()
    e.on("info_cfg", cfg_changed)

    tick = QTimer(p)
    tick.timeout.connect(lambda: view.isVisible() and view.set_image(e.info_preview_image()))
    tick.start(600)
    refresh_extras()
    return PageGrid({"cards": c, "more": m, "preview": p},
                    {"wide": [[("cards", 3)], [("more", 3)], [("preview", 3)]], "medium": [[("cards", 3)], [("more", 3), ("preview", 3)]]},
                    order=["cards", "more", "preview"], titles={"cards": "Cards", "more": "More cards", "preview": "Preview"},
                    tabs_factory=shell.make_tabs, wide_min=1000, medium_min=720)
