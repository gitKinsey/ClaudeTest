"""Ready-made key layouts for popular programs. A preset fills ONE layer (K1-K5, dial right / left = slots 1-7) with actions from the
app's library and can add the matching program rule to the Profiles page, so the layer switches by itself when you use that program.
Shortcuts are the programs' defaults - if you changed them in the program, change the key too. Pure data + one function, no GUI."""

# name -> {"keys": {slot: (category, action)}, "profile": (display name, match text, kind: process|title)}
PRESETS = {
    "Video meeting: Zoom": {"keys": {1: ("Productivity & Dev", "Toggle Zoom Mute"), 2: ("Productivity & Dev", "Toggle Zoom Camera"), 3: ("Meetings", "Zoom: Share Screen"),
                                     4: ("Meetings", "Zoom: Raise Hand"), 5: ("Media", "Mute"), 6: ("Media", "Volume Up"), 7: ("Media", "Volume Down")},
                            "profile": ("Zoom", "zoom", "process")},
    "Video meeting: Microsoft Teams": {"keys": {1: ("Meetings", "Teams: Mute"), 2: ("Meetings", "Teams: Camera"), 3: ("Meetings", "Teams: Share Screen"),
                                                4: ("Meetings", "Teams: Raise Hand"), 5: ("Media", "Mute"), 6: ("Media", "Volume Up"), 7: ("Media", "Volume Down")},
                                       "profile": ("Teams", "teams", "process")},
    "Video meeting: Google Meet": {"keys": {1: ("Meetings", "Meet: Mute"), 2: ("Meetings", "Meet: Camera"), 3: ("Meetings", "Meet: Raise Hand"),
                                            4: ("Meetings", "Meet: Chat"), 5: ("Media", "Mute"), 6: ("Media", "Volume Up"), 7: ("Media", "Volume Down")},
                                   "profile": ("Google Meet", "Meet -", "title")},
    "Drawing: Photoshop / Krita": {"keys": {1: ("Creative & Video", "Brush Tool"), 2: ("Creative & Video", "Eraser Tool"), 3: ("Editing", "Undo"),
                                            4: ("Creative & Video", "Step Backward (Photoshop)"), 5: ("Editing", "Save"),
                                            6: ("Creative & Video", "Brush Size Up"), 7: ("Creative & Video", "Brush Size Down")},
                                   "profile": ("Photoshop", "photoshop", "process")},
    "Video editing: Premiere": {"keys": {1: ("Creative & Video", "Shuttle Reverse (J)"), 2: ("Creative & Video", "Shuttle Stop (K)"), 3: ("Creative & Video", "Shuttle Forward (L)"),
                                         4: ("Creative & Video", "Add Edit / Split (Premiere)"), 5: ("Editing", "Undo"),
                                         6: ("Creative & Video", "Frame Forward"), 7: ("Creative & Video", "Frame Back")},
                                "profile": ("Premiere", "premiere", "process")},
    "Spreadsheet: Excel": {"keys": {1: ("Spreadsheet & 3D", "Edit Cell (F2)"), 2: ("Spreadsheet & 3D", "Autosum"), 3: ("Spreadsheet & 3D", "Toggle Filter"),
                                    4: ("Spreadsheet & 3D", "Absolute Reference (F4)"), 5: ("Editing", "Save"),
                                    6: ("Spreadsheet & 3D", "Next Sheet"), 7: ("Spreadsheet & 3D", "Previous Sheet")},
                           "profile": ("Excel", "excel", "process")},
    "Writing: Word / Docs": {"keys": {1: ("Writing", "Bold"), 2: ("Writing", "Italic"), 3: ("Writing", "Underline"), 4: ("Writing", "Insert Link"),
                                      5: ("Editing", "Save"), 6: ("Browser", "Zoom In"), 7: ("Browser", "Zoom Out")},
                             "profile": ("Word", "winword", "process")},
    "3D: Blender": {"keys": {1: ("Spreadsheet & 3D", "Blender: Grab"), 2: ("Spreadsheet & 3D", "Blender: Rotate"), 3: ("Spreadsheet & 3D", "Blender: Scale"),
                             4: ("Spreadsheet & 3D", "Blender: Edit Mode"), 5: ("Editing", "Undo"), 6: ("Mouse", "Scroll Up"), 7: ("Mouse", "Scroll Down")},
                    "profile": ("Blender", "blender", "process")},
    "Coding: VS Code": {"keys": {1: ("Productivity & Dev", "Search Files (Quick Open)"), 2: ("Productivity & Dev", "Command Palette"),
                                 3: ("Productivity & Dev", "VS Code Terminal"), 4: ("Productivity & Dev", "VS Code Format Document"),
                                 5: ("Productivity & Dev", "VS Code Line Comment"), 6: ("Mouse", "Scroll Up"), 7: ("Mouse", "Scroll Down")},
                        "profile": ("VS Code", "code", "process")},
    "Browsing": {"keys": {1: ("Browser", "Back"), 2: ("Browser", "Forward"), 3: ("Browser", "Refresh"), 4: ("Browser", "New Tab"), 5: ("Browser", "Close Tab"),
                          6: ("Navigation", "Page Down"), 7: ("Navigation", "Page Up")},
                 "profile": ("Browser", "firefox", "process")},
}

# programs worth a quick profile rule even without a key preset: (name, match, kind)
EXTRA_PROFILES = [("Spotify", "spotify", "process"), ("Discord", "discord", "process"), ("Slack", "slack", "process"), ("Figma", "figma", "process"),
                  ("OBS Studio", "obs", "process"), ("DaVinci Resolve", "resolve", "process"), ("Chrome", "chrome", "process"), ("Edge", "msedge", "process")]


def profile_choices():
    """[(name, match, kind)] for the quick-add menu: every preset's program plus the extras."""
    return [v["profile"] for v in PRESETS.values()] + EXTRA_PROFILES


def apply(cfg, name, layer, add_profile=False, action_exists=lambda cat, a: True):
    """Write the preset into cfg["layers"][layer]. Returns (list of changed slots, profile rule dict or None).
    Raises KeyError for an unknown preset / ValueError when the library lacks an action the preset needs."""
    p = PRESETS[name]
    for slot, (cat, act) in p["keys"].items():
        if not action_exists(cat, act):
            raise ValueError(f"the action library has no '{act}' in '{cat}'")
    lm = cfg["layers"][layer]
    for slot, (cat, act) in p["keys"].items():
        lm[str(slot)] = {"cat": cat, "action": act}
    rule = None
    if add_profile:
        pname, match, kind = p["profile"]
        rule = {"name": pname, "match": match, "kind": kind, "layer": layer, "enabled": True}
        if not any(r.get("match") == match and r.get("kind") == kind for r in cfg["profiles"]):
            cfg["profiles"].append(rule)
        else:
            rule = None
    return sorted(p["keys"]), rule
