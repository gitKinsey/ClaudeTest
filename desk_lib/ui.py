"""Visual language of the app: near-black / white surfaces, one cyan accent (the pad's own colour) plus pink for emphasis.
Every colour is a (light, dark) pair, so the whole app flips with ctk.set_appearance_mode()."""
import json
import os
import tempfile

import customtkinter as ctk

from desk_lib import i18n

from desk_lib.tokens import (BG, SIDE, CARD, CARD2, CARD3, LINE, TEXT, MUTED, FAINT, ACCENT, ACCENT_FILL, ACCENT_FILL_H, PINK, OK, OK_FILL, WARN, WARN_FILL, ERR, ERR_FILL, WHITE, ACCENTS, DARK)
_RE_EXPORTED = (PINK, OK, WARN, ERR)                  # re-exported for the Tk app (`from desk_lib.ui import OK, ...`)


def pick(pair, mode=None):
    mode = mode or ctk.get_appearance_mode()
    return pair[1] if mode == "Dark" else pair[0]


def _l(*pair):
    return list(pair)


def build_theme():
    """The customtkinter theme as a dict (same schema as customtkinter/assets/themes/*.json)."""
    return {
        "CTk": {"fg_color": _l(*BG)},
        "CTkToplevel": {"fg_color": _l(*BG)},
        "CTkFrame": {"corner_radius": 12, "border_width": 0, "fg_color": _l(*CARD), "top_fg_color": _l(*CARD2), "border_color": _l(*LINE)},
        "CTkButton": {"corner_radius": 8, "border_width": 0, "fg_color": _l(*ACCENT_FILL), "hover_color": _l(*ACCENT_FILL_H),
                      "border_color": _l(*LINE), "text_color": [WHITE, WHITE], "text_color_disabled": _l(*FAINT)},
        "CTkLabel": {"corner_radius": 0, "fg_color": "transparent", "text_color": _l(*TEXT)},
        "CTkEntry": {"corner_radius": 8, "border_width": 1, "fg_color": _l(*CARD2), "border_color": _l(*LINE), "text_color": _l(*TEXT),
                     "placeholder_text_color": _l(*FAINT)},
        "CTkCheckBox": {"corner_radius": 5, "border_width": 2, "fg_color": _l(*ACCENT_FILL), "border_color": _l(*FAINT),
                        "hover_color": _l(*ACCENT_FILL_H), "checkmark_color": [WHITE, WHITE], "text_color": _l(*TEXT),
                        "text_color_disabled": _l(*FAINT)},
        "CTkSwitch": {"corner_radius": 1000, "border_width": 3, "button_length": 0, "fg_color": _l(*CARD3), "progress_color": _l(*ACCENT_FILL),
                      "button_color": [WHITE, "#e8e8ee"], "button_hover_color": ["#f0f0f0", WHITE], "text_color": _l(*TEXT),
                      "text_color_disabled": _l(*FAINT)},
        "CTkRadioButton": {"corner_radius": 1000, "border_width_checked": 6, "border_width_unchecked": 2, "fg_color": _l(*ACCENT_FILL),
                           "border_color": _l(*FAINT), "hover_color": _l(*ACCENT_FILL_H), "text_color": _l(*TEXT),
                           "text_color_disabled": _l(*FAINT)},
        "CTkProgressBar": {"corner_radius": 1000, "border_width": 0, "fg_color": _l(*CARD3), "progress_color": _l(*ACCENT), "border_color": _l(*LINE)},
        "CTkSlider": {"corner_radius": 1000, "button_corner_radius": 1000, "border_width": 6, "button_length": 0, "fg_color": _l(*CARD3),
                      "progress_color": _l(*ACCENT), "button_color": _l(*ACCENT), "button_hover_color": [WHITE, WHITE]},
        "CTkOptionMenu": {"corner_radius": 8, "fg_color": _l(*CARD2), "button_color": _l(*CARD3), "button_hover_color": _l(*FAINT),
                          "text_color": _l(*TEXT), "text_color_disabled": _l(*FAINT)},
        "CTkComboBox": {"corner_radius": 8, "border_width": 1, "fg_color": _l(*CARD2), "border_color": _l(*LINE), "button_color": _l(*CARD3),
                        "button_hover_color": _l(*FAINT), "text_color": _l(*TEXT), "text_color_disabled": _l(*FAINT)},
        "CTkScrollbar": {"corner_radius": 1000, "border_spacing": 4, "fg_color": "transparent", "button_color": _l(*CARD3),
                         "button_hover_color": _l(*FAINT)},
        "CTkSegmentedButton": {"corner_radius": 8, "border_width": 2, "fg_color": ["#7a7a86", "#25252c"], "selected_color": _l(*ACCENT_FILL),
                               "selected_hover_color": _l(*ACCENT_FILL_H), "unselected_color": ["#7a7a86", "#25252c"],
                               "unselected_hover_color": ["#62626d", "#34343c"], "text_color": [WHITE, "#f4f4f6"], "text_color_disabled": _l(*FAINT)},
        "CTkTextbox": {"corner_radius": 8, "border_width": 1, "fg_color": _l(*CARD2), "border_color": _l(*LINE), "text_color": _l(*TEXT),
                       "scrollbar_button_color": _l(*CARD3), "scrollbar_button_hover_color": _l(*FAINT)},
        "CTkScrollableFrame": {"label_fg_color": _l(*CARD2)},
        "DropdownMenu": {"fg_color": _l(*CARD), "hover_color": _l(*CARD3), "text_color": _l(*TEXT)},
        "CTkFont": {"macOS": {"family": "SF Pro Display", "size": 13, "weight": "normal"},
                    "Windows": {"family": "Segoe UI", "size": 13, "weight": "normal"},
                    "Linux": {"family": "Roboto", "size": 13, "weight": "normal"}},
    }


def set_accent(name):
    """Choose the accent colour. Must be called before install_theme() (the theme file is written once). Unknown names -> cyan."""
    global ACCENT, ACCENT_FILL, ACCENT_FILL_H
    ACCENT, ACCENT_FILL, ACCENT_FILL_H = ACCENTS.get(name, ACCENTS["cyan"])
    DARK["accent"] = ACCENT[1]
    return name if name in ACCENTS else "cyan"


_installed = False


def install_theme():
    """Call once, before the first widget is created."""
    global _installed
    if _installed:
        return
    path = os.path.join(tempfile.gettempdir(), "deskcompanion_theme.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_theme(), f)
    ctk.set_default_color_theme(path)
    _patch_widgets()
    _installed = True


# fg colours the old code used for "secondary" buttons -> one neutral style
_NEUTRAL_OLD = {"#555", "#555555", "#3a3f48", "#2b2f36", "#3a3f49", "#2e323a", "#2a2e36", "#1d2026", "#1c1f24", "#525966", "gray30", "gray25"}
_GREEN_OLD = {"#2f7d4f", "#2e7d4f"}
_AMBER_OLD = {"#7a4a1f", "#d9822b", "#ff9f1a"}
_RED_OLD = {"#7a1f1f", "#a33", "#c0392b"}


def _patch_widgets():
    """Give plain ctk widgets the app's look without touching every call site: bordered cards, neutral secondary buttons."""
    base_frame, base_button = ctk.CTkFrame, ctk.CTkButton

    class Frame(base_frame):
        def __init__(self, master, *a, **kw):
            if kw.get("fg_color") != "transparent" and "border_width" not in kw:
                kw["border_width"] = 1
                kw.setdefault("border_color", LINE)
            super().__init__(master, *a, **kw)

    class Button(base_button):
        def __init__(self, master, *a, **kw):
            fg = kw.get("fg_color")
            if isinstance(fg, str) and fg != "transparent":
                low = fg.lower()
                if low in _NEUTRAL_OLD:
                    kw["fg_color"] = CARD3
                    kw.setdefault("hover_color", FAINT)
                    kw.setdefault("text_color", TEXT)
                elif low in _GREEN_OLD:
                    kw["fg_color"] = OK_FILL
                elif low in _AMBER_OLD:
                    kw["fg_color"] = WARN_FILL
                elif low in _RED_OLD:
                    kw["fg_color"] = ERR_FILL
            elif fg == "transparent":
                kw.setdefault("text_color", TEXT)
            super().__init__(master, *a, **kw)

    base_label, base_switch = ctk.CTkLabel, ctk.CTkSwitch

    def _tr_kw(kw):
        if isinstance(kw.get("text"), str):
            kw["text"] = i18n.tr(kw["text"])
        return kw

    class Label(base_label):
        def __init__(self, master, *a, **kw):
            super().__init__(master, *a, **_tr_kw(kw))

        def configure(self, require_redraw=False, **kw):
            super().configure(require_redraw=require_redraw, **_tr_kw(kw))

    class Switch(base_switch):
        def __init__(self, master, *a, **kw):
            super().__init__(master, *a, **_tr_kw(kw))

    ButtonBase = Button

    class TButton(ButtonBase):
        def __init__(self, master, *a, **kw):
            super().__init__(master, *a, **_tr_kw(kw))

    ctk.CTkFrame, ctk.CTkButton, ctk.CTkLabel, ctk.CTkSwitch = Frame, TButton, Label, Switch


# ---- small building blocks
def font(size=13, weight="normal"):
    return ctk.CTkFont(size=size, weight=weight)


def heading(parent, text, size=15):
    return ctk.CTkLabel(parent, text=text, font=font(size, "bold"), text_color=TEXT, anchor="w")


def muted(parent, text, **kw):
    kw.setdefault("justify", "left")
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(parent, text=text, text_color=MUTED, **kw)


def secondary_button(parent, text, command=None, **kw):
    return ctk.CTkButton(parent, text=text, command=command, fg_color=CARD3, hover_color=FAINT, text_color=TEXT, **kw)


def danger_button(parent, text, command=None, **kw):
    return ctk.CTkButton(parent, text=text, command=command, fg_color=ERR_FILL, hover_color=("#991b1b", "#dc2626"), **kw)


class Pill(ctk.CTkFrame):
    """Rounded status chip: coloured dot + text."""

    def __init__(self, master, text="", color=MUTED):
        super().__init__(master, fg_color=CARD2, corner_radius=20, border_width=0)
        self.dot = ctk.CTkLabel(self, text="●", width=14, text_color=color, font=font(12))
        self.dot.pack(side="left", padx=(10, 2), pady=3)
        self.lbl = ctk.CTkLabel(self, text=text, text_color=TEXT, font=font(12))
        self.lbl.pack(side="left", padx=(0, 12), pady=3)

    def set(self, text, color):
        self.lbl.configure(text=text)
        self.dot.configure(text_color=color)


class SideTabs(ctk.CTkFrame):
    """A left navigation rail instead of a tab strip. Same small API as CTkTabview (add / tab / set / get), so the page
    builders do not care: tab(name) returns the empty content frame of that page."""

    def __init__(self, master, on_change=None, brand="Desk Companion", version=""):
        super().__init__(master, fg_color="transparent", border_width=0, corner_radius=0)
        self.on_change = on_change
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.side = ctk.CTkFrame(self, width=208, corner_radius=0, fg_color=SIDE, border_width=0)
        self.side.grid(row=0, column=0, sticky="ns")
        self.side.grid_propagate(False)
        self.side.pack_propagate(False)
        head = ctk.CTkFrame(self.side, fg_color="transparent", border_width=0)
        head.pack(fill="x", padx=18, pady=(20, 6))
        ctk.CTkLabel(head, text="◉", font=font(20, "bold"), text_color=ACCENT, width=26).pack(side="left")
        col = ctk.CTkFrame(head, fg_color="transparent", border_width=0)
        col.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(col, text=brand, font=font(15, "bold"), text_color=TEXT, anchor="w").pack(anchor="w")
        if version:
            ctk.CTkLabel(col, text=version, font=font(11), text_color=FAINT, anchor="w").pack(anchor="w")
        self.nav = ctk.CTkFrame(self.side, fg_color="transparent", border_width=0)
        self.nav.pack(fill="both", expand=True, padx=10, pady=(14, 6))
        self.foot = ctk.CTkFrame(self.side, fg_color="transparent", border_width=0)
        self.foot.pack(fill="x", padx=12, pady=(0, 14))
        self.body = ctk.CTkFrame(self, fg_color=BG, corner_radius=0, border_width=0)
        self.body.grid(row=0, column=1, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self._pages, self._buttons, self._bars, self._headers, self._cur = {}, {}, {}, {}, None

    def group(self, text):
        ctk.CTkLabel(self.nav, text=text.upper(), font=font(10, "bold"), text_color=FAINT, anchor="w").pack(fill="x", padx=10, pady=(12, 2))

    def add(self, name, label=None, title=None, subtitle=""):
        page = ctk.CTkFrame(self.body, fg_color="transparent", border_width=0, corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(page, fg_color="transparent", border_width=0)
        head.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 4))
        head.grid_columnconfigure(0, weight=1)
        t = ctk.CTkLabel(head, text=title or label or name, font=font(22, "bold"), text_color=TEXT, anchor="w")
        t.grid(row=0, column=0, sticky="w")
        if subtitle:
            ctk.CTkLabel(head, text=subtitle, font=font(12), text_color=MUTED, anchor="w").grid(row=1, column=0, sticky="w")
        content = ctk.CTkFrame(page, fg_color="transparent", border_width=0, corner_radius=0)
        content.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 10))
        row = ctk.CTkFrame(self.nav, fg_color="transparent", border_width=0)
        row.pack(fill="x", pady=1)
        bar = ctk.CTkFrame(row, width=3, height=22, corner_radius=2, fg_color="transparent", border_width=0)
        bar.pack(side="left", padx=(0, 4))
        btn = ctk.CTkButton(row, text=label or name, anchor="w", height=34, corner_radius=8, fg_color="transparent",
                            hover_color=CARD2, text_color=MUTED, font=font(13), command=lambda n=name: self.set(n))
        btn.pack(side="left", fill="x", expand=True)
        self._pages[name], self._buttons[name], self._bars[name] = (page, content), btn, bar
        self._headers[name] = head
        page.grid_remove()
        if self._cur is None:
            self.set(name)
        return content

    def header_area(self, name):
        """Right-hand part of a page header (for a selector or an action button)."""
        return self._headers[name]

    def tab(self, name):
        return self._pages[name][1]

    def get(self):
        return self._cur

    def set(self, name):
        if name not in self._pages:
            raise KeyError(name)
        for n, (page, _c) in self._pages.items():
            if n == name:
                page.grid()
                page.tkraise()
            else:
                page.grid_remove()
            on = n == name
            self._buttons[n].configure(fg_color=CARD2 if on else "transparent", text_color=TEXT if on else MUTED)
            self._bars[n].configure(fg_color=ACCENT if on else "transparent")
        changed = name != self._cur
        self._cur = name
        if changed and self.on_change:
            self.on_change(name)
