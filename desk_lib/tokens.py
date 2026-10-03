"""Colour tokens shared by every front end (Tk and Qt). Every colour is a (light, dark) pair."""

# ---- palette  (light, dark)
BG = ("#f6f6f7", "#09090b")
SIDE = ("#ececef", "#0d0d10")
CARD = ("#ffffff", "#121215")
CARD2 = ("#f0f0f3", "#1a1a1f")
CARD3 = ("#e4e4e9", "#25252c")
LINE = ("#dcdce2", "#26262d")
TEXT = ("#0b0b0d", "#f4f4f6")
MUTED = ("#62626d", "#8e8e9b")
FAINT = ("#9a9aa6", "#5d5d69")
ACCENT = ("#0891b2", "#22d3ee")         # text / ring / indicator accent
ACCENT_FILL = ("#0e7490", "#0e7490")    # filled buttons (white text)
ACCENT_FILL_H = ("#155e75", "#0891b2")
PINK = ("#db2777", "#ff4fa8")
OK = ("#15803d", "#34d399")
OK_FILL = ("#15803d", "#15803d")
WARN = ("#b45309", "#fbbf24")
WARN_FILL = ("#b45309", "#b45309")
ERR = ("#dc2626", "#ff6b6b")
ERR_FILL = ("#b91c1c", "#b91c1c")
WHITE = "#ffffff"

# the same colours as single strings for plain tk widgets (Canvas, Listbox ...) which cannot take pairs
DARK = {"bg": "#09090b", "card": "#121215", "card2": "#1a1a1f", "line": "#26262d", "text": "#f4f4f6", "accent": "#22d3ee"}



ACCENTS = {   # name -> (text accent (light, dark), fill (light, dark), fill hover (light, dark))
    "cyan": (("#0891b2", "#22d3ee"), ("#0e7490", "#0e7490"), ("#155e75", "#0891b2")),
    "pink": (("#db2777", "#ff4fa8"), ("#be185d", "#be185d"), ("#9d174d", "#db2777")),
    "green": (("#15803d", "#34d399"), ("#15803d", "#15803d"), ("#166534", "#16a34a")),
    "amber": (("#b45309", "#fbbf24"), ("#b45309", "#b45309"), ("#92400e", "#d97706")),
    "violet": (("#6d28d9", "#a78bfa"), ("#6d28d9", "#6d28d9"), ("#5b21b6", "#7c3aed")),
}


