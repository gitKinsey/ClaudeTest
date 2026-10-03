"""Macro scripts: loops, conditions, variables and sub-scripts for the pad's keys - a tiny line-based language, no GUI.

    # comment
    set who = {clipboard}
    repeat 3
      key ctrl+c
      wait 200
    end
    if window "Visual Studio Code"
      text // TODO {date} {who}
    else
      key ctrl+v
    end
    if not time 22:00-06:00
      open https://example.org
    end
    run cleanup                      (another saved script; at most 5 levels deep)

Commands:  key COMBO | text TEXT | wait MS [jitter MS] | click left|right|middle|double | scroll N | media NAME | open URL | app NAME | file PATH |
           notify TEXT | shell COMMAND | run SCRIPT | set NAME = VALUE | stop
           exec COMMAND  (runs it, sets {exit} and {out})  |  card LABEL | TITLE | LINE A | LINE B  (the pad's custom info card)  |  alert RRGGBB [times]  (LED flashes)
           layout [save] NAME  |  http [METHOD] URL [BODY]  |  ask PROMPT  (the AI; sets {answer})  |  translate LANG  (the clipboard; sets {translated})
           moveto X Y  |  clickat X Y [left|right]  |  do OP ARG  (any key action: dnd on, mic toggle, appvol up, audio_out next, shot ...)
Blocks:    repeat N ... end   |   if [not] CONDITION ... [else ...] end
Conditions: window "text" (title contains) | process "text" | clipboard "text" | time HH:MM-HH:MM | weekday mon-fri | weekday sat,sun |
            var NAME == | != | < | > | contains VALUE   (for example:  exec make   then   if var exit == 0 )
Text and arguments may use {date} {time} {clipboard} {counter:name} {uuid} {random:1-6} ... (see textops) and your own {name} variables.
Limits keep a script from running away: 100 repeats per loop, 500 commands, 60 s of waiting in total, 10 s per wait."""
import random
import re
import shlex
from datetime import datetime

from desk_lib import textops

MAX_REPEAT, MAX_COMMANDS, MAX_WAIT_MS, MAX_ONE_WAIT_MS, MAX_DEPTH, MAX_SCRIPT_CHARS = 100, 500, 60_000, 10_000, 5, 6000
CMDS = {"key", "text", "wait", "click", "scroll", "media", "open", "app", "file", "notify", "shell", "run", "set", "stop",
        "exec", "card", "alert", "layout", "http", "ask", "translate", "moveto", "clickat", "do"}
DO_OPS = {"appvol", "dnd", "audio_out", "mic", "shot", "layout", "cliphist", "plugin", "art", "qr", "webhook", "translate", "ai", "url", "app", "file", "notify", "snippet", "clip"}
MEDIA_NAMES = {"PLAY_PAUSE", "NEXT", "PREV", "STOP", "MUTE", "VOL_UP", "VOL_DOWN", "FF", "REWIND"}
MODS = {"ctrl": "CTRL", "control": "CTRL", "shift": "SHIFT", "alt": "ALT", "option": "ALT", "gui": "GUI", "win": "GUI", "cmd": "GUI", "super": "GUI",
        "primary": "PRIMARY", "mod": "PRIMARY"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,23}")


class ScriptError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(f"line {line}: {msg}" if line else msg)
        self.line = line


# ---------------------------------------------------------------- parsing
def parse_combo(s, line=None):
    parts = [p for p in re.split(r"\s*\+\s*", s.strip()) if p]
    if not parts:
        raise ScriptError("key needs a combination like ctrl+shift+t", line)
    keys = []
    for p in parts:
        low = p.lower()
        if low in MODS:
            keys.append(MODS[low])
        elif len(p) == 1:
            keys.append(p.lower())
        elif p.upper() in ("ENTER", "TAB", "ESC", "SPACE", "BACKSPACE", "DELETE", "INSERT", "HOME", "END", "PGUP", "PGDN", "UP", "DOWN", "LEFT", "RIGHT",
                           "PRTSC", "MENU", "CAPSLOCK") or re.fullmatch(r"[Ff]([1-9]|1[0-2])", p):
            keys.append(p.upper())
        else:
            raise ScriptError(f"unknown key '{p}'", line)
    if len(keys) > 4:
        raise ScriptError("at most 4 keys in one combination", line)
    return keys


def parse_condition(text, line):
    t = text.strip()
    neg = False
    if t.lower().startswith("not "):
        neg, t = True, t[4:].strip()
    m = re.match(r"(window|process|clipboard)(?:\s+(.*))?$", t, re.I)
    if m:
        try:
            arg = shlex.split(m.group(2) or "")
        except ValueError:
            raise ScriptError("unbalanced quotes in the condition", line) from None
        if len(arg) != 1 or not arg[0]:
            raise ScriptError(f"{m.group(1)} needs one text, e.g. {m.group(1)} \"Notepad\"", line)
        return {"neg": neg, "kind": m.group(1).lower(), "arg": arg[0]}
    m = re.match(r"var\s+([A-Za-z_][A-Za-z0-9_]*)\s*(==|!=|<=|>=|<|>|contains)\s*(.*)$", t, re.I)
    if m:
        return {"neg": neg, "kind": "var", "arg": (m.group(1), m.group(2).lower(), m.group(3).strip().strip('"'))}
    m = re.match(r"time\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$", t, re.I)
    if m:
        a, b = (_hhmm(x, line) for x in m.groups())
        return {"neg": neg, "kind": "time", "arg": (a, b)}
    m = re.match(r"weekday\s+(.+)$", t, re.I)
    if m:
        return {"neg": neg, "kind": "weekday", "arg": _days(m.group(1), line)}
    raise ScriptError(f"unknown condition '{text.strip()}' (window / process / clipboard / var / time / weekday)", line)


def _hhmm(s, line):
    h, m = (int(x) for x in s.split(":"))
    if h > 23 or m > 59:
        raise ScriptError(f"'{s}' is not a time like 09:30", line)
    return h * 60 + m


def _days(s, line):
    out = set()
    for part in s.lower().replace(" ", "").split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if a not in DAYS or b not in DAYS:
                raise ScriptError(f"unknown weekday in '{part}'", line)
            i, j = DAYS.index(a), DAYS.index(b)
            out |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
        elif part in DAYS:
            out.add(DAYS.index(part))
        else:
            raise ScriptError(f"unknown weekday '{part}' (mon, tue, ... sun)", line)
    return sorted(out)


def parse(source):
    """Source text -> list of nodes. Raises ScriptError with a line number."""
    if len(source) > MAX_SCRIPT_CHARS:
        raise ScriptError(f"the script is too long ({len(source)} characters, at most {MAX_SCRIPT_CHARS})")
    root, stack = [], []                                # stack of (kind, node, current_list, opening_line)
    cur = root
    for n, raw in enumerate(source.splitlines(), 1):
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        word, _, rest = ln.partition(" ")
        word, rest = word.lower(), rest.strip()
        if word == "repeat":
            if not rest.isdigit() or not 1 <= int(rest) <= MAX_REPEAT:
                raise ScriptError(f"repeat needs a number from 1 to {MAX_REPEAT}", n)
            node = {"t": "repeat", "n": int(rest), "body": [], "line": n}
            cur.append(node)
            stack.append(("repeat", node, cur, n))
            cur = node["body"]
        elif word == "if":
            node = {"t": "if", "cond": parse_condition(rest, n), "then": [], "else": [], "line": n}
            cur.append(node)
            stack.append(("if", node, cur, n))
            cur = node["then"]
        elif word == "else":
            if not stack or stack[-1][0] != "if" or cur is stack[-1][1]["else"]:
                raise ScriptError("'else' without a matching 'if'", n)
            cur = stack[-1][1]["else"]
        elif word == "end":
            if not stack:
                raise ScriptError("'end' without a matching 'repeat' or 'if'", n)
            _k, _node, cur, _l = stack.pop()
        elif word in CMDS:
            cur.append(_command(word, rest, n))
        else:
            raise ScriptError(f"unknown command '{word}'", n)
    if stack:
        raise ScriptError(f"'{stack[-1][0]}' is never closed with 'end'", stack[-1][3])
    return root


def _command(word, rest, n):
    node = {"t": "cmd", "c": word, "arg": rest, "line": n}
    if word == "key":
        node["keys"] = parse_combo(rest, n)
    elif word == "wait":
        m = re.fullmatch(r"(\d+)(?:\s+jitter\s+(\d+))?", rest)
        if not m or not 0 <= int(m.group(1)) <= MAX_ONE_WAIT_MS or (m.group(2) and int(m.group(2)) > MAX_ONE_WAIT_MS):
            raise ScriptError(f"wait needs milliseconds from 0 to {MAX_ONE_WAIT_MS}, optionally followed by  jitter MS", n)
        node["ms"], node["jitter"] = int(m.group(1)), int(m.group(2) or 0)
    elif word == "click":
        if rest.lower() not in ("left", "right", "middle", "double", ""):
            raise ScriptError("click needs left, right, middle or double", n)
        node["arg"] = rest.lower() or "left"
    elif word == "scroll":
        if not re.fullmatch(r"-?\d{1,2}", rest) or int(rest) == 0 or abs(int(rest)) > 20:
            raise ScriptError("scroll needs a number from -20 to 20 (not 0)", n)
        node["n"] = int(rest)
    elif word == "media":
        if rest.upper() not in MEDIA_NAMES:
            raise ScriptError("media needs one of " + ", ".join(sorted(MEDIA_NAMES)).lower(), n)
        node["arg"] = rest.upper()
    elif word == "run":
        if not NAME.fullmatch(rest.replace("-", "_")) and not re.fullmatch(r"[\w .\-]{1,32}", rest):
            raise ScriptError("run needs the name of a saved script", n)
    elif word == "set":
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]{0,23})\s*=\s*(.*)", rest)
        if not m:
            raise ScriptError("set needs NAME = VALUE", n)
        node["name"], node["arg"] = m.group(1), m.group(2)
    elif word == "alert":
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})(?:\s+(\d{1,2}))?", rest)
        if not m or not 1 <= int(m.group(2) or 3) <= 10:
            raise ScriptError("alert needs a colour like ff0000 and optionally 1-10 flashes", n)
        node["hex"], node["times"] = m.group(1).lower(), int(m.group(2) or 3)
    elif word == "card":
        parts = [x.strip() for x in rest.split("|")]
        if not rest or len(parts) > 4:
            raise ScriptError("card needs  LABEL | TITLE | LINE A | LINE B  (up to four parts)", n)
        node["parts"] = (parts + ["", "", "", ""])[:4]
    elif word in ("moveto", "clickat"):
        m = re.fullmatch(r"(-?\d{1,5})\s+(-?\d{1,5})(?:\s+(left|right|middle))?", rest, re.I)
        if not m or (word == "moveto" and m.group(3)):
            raise ScriptError(f"{word} needs X Y" + (" and optionally left / right / middle" if word == "clickat" else ""), n)
        node["x"], node["y"], node["btn"] = int(m.group(1)), int(m.group(2)), (m.group(3) or "left").lower()
    elif word == "do":
        op, _, arg = rest.partition(" ")
        if op.lower() not in DO_OPS or not arg.strip():
            raise ScriptError("do needs an action and its argument, e.g.  do dnd on   (" + ", ".join(sorted(DO_OPS)) + ")", n)
        node["op"], node["arg"] = op.lower(), arg.strip()
    elif word == "translate":
        if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z]{2,4})?", rest):
            raise ScriptError("translate needs a language code like de, es, fr", n)
    elif word in ("text", "open", "app", "file", "notify", "shell", "exec", "layout", "http", "ask") and not rest:
        raise ScriptError(f"{word} needs an argument", n)
    return node


# ---------------------------------------------------------------- running
class Stop(Exception):
    pass


class Backend:
    """What a script can do. Subclass / duck-type; the app implements it with the real key sender, tests with a recorder."""
    def key(self, keys): raise NotImplementedError
    def text(self, s): raise NotImplementedError
    def wait(self, ms): raise NotImplementedError
    def click(self, how): raise NotImplementedError
    def scroll(self, n): raise NotImplementedError
    def media(self, name): raise NotImplementedError
    def host(self, op, arg): raise NotImplementedError       # open / app / file / notify / shell
    def exec(self, cmd): raise NotImplementedError              # -> (exit code, first output line)
    def card(self, label, title, a, b): raise NotImplementedError
    def alert(self, hex_, times): raise NotImplementedError
    def http(self, arg): raise NotImplementedError             # -> status text
    def ask(self, prompt): raise NotImplementedError           # -> answer text
    def translate(self, lang): raise NotImplementedError       # -> translated clipboard
    def moveto(self, x, y): raise NotImplementedError
    def clickat(self, x, y, btn): raise NotImplementedError
    def window(self): return ("", "")                        # (process, title) of the focused window
    def clipboard(self): return ""
    def now(self): return datetime.now()


class Recorder(Backend):
    """Dry run: records what would happen, never touches the computer."""
    def __init__(self, window=("", ""), clipboard="", now=None):
        self.log, self._w, self._c, self._now = [], window, clipboard, now or datetime.now()
    def key(self, keys): self.log.append("key " + "+".join(keys))
    def text(self, s): self.log.append(f"type {s!r}")
    def wait(self, ms): self.log.append(f"wait {ms} ms")
    def click(self, how): self.log.append(f"click {how}")
    def scroll(self, n): self.log.append(f"scroll {n}")
    def media(self, name): self.log.append(f"media {name.lower()}")
    def host(self, op, arg): self.log.append(f"{op} {arg}")
    def exec(self, cmd): self.log.append(f"exec {cmd}"); return 0, "(dry run)"
    def card(self, label, title, a, b): self.log.append(f"card {label!r} {title!r} {a!r} {b!r}")
    def alert(self, hex_, times): self.log.append(f"alert {hex_} x{times}")
    def http(self, arg): self.log.append(f"http {arg}"); return "(dry run)"
    def ask(self, prompt): self.log.append(f"ask {prompt!r}"); return "(AI answer)"
    def translate(self, lang): self.log.append(f"translate {lang}"); return "(translation)"
    def moveto(self, x, y): self.log.append(f"move the pointer to {x},{y}")
    def clickat(self, x, y, btn): self.log.append(f"click {btn} at {x},{y}")
    def window(self): return self._w
    def clipboard(self): return self._c
    def now(self): return self._now


def _cmp(a, op, b):
    try:
        fa, fb = float(a), float(b)
        both = True
    except (TypeError, ValueError):
        fa, fb, both = str(a), str(b), False
    if op == "contains":
        return str(b).lower() in str(a).lower()
    x, y = (fa, fb) if both else (str(a).lower(), str(b).lower())
    return {"==": x == y, "!=": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


def check_condition(c, backend, variables=None):
    k, arg = c["kind"], c["arg"]
    if k == "var":
        name, op, val = arg
        r = _cmp((variables or {}).get(name, ""), op, val)
        return (not r) if c["neg"] else r
    if k == "window":
        r = arg.lower() in (backend.window()[1] or "").lower()
    elif k == "process":
        r = arg.lower() in (backend.window()[0] or "").lower()
    elif k == "clipboard":
        r = arg.lower() in (backend.clipboard() or "").lower()
    elif k == "time":
        now = backend.now()
        m = now.hour * 60 + now.minute
        a, b = arg
        r = a <= m < b if a <= b else (m >= a or m < b)
    else:
        r = backend.now().weekday() in arg
    return (not r) if c["neg"] else r


def run(source_or_nodes, backend, lookup=None, ctx=None, shell_ok=False):
    """Execute a script. lookup(name) -> source text of another saved script (or None). ctx: textops context (counter, ...).
    Returns the number of commands run. Raises ScriptError (readable) on a runtime problem, Stop is swallowed."""
    nodes = parse(source_or_nodes) if isinstance(source_or_nodes, str) else source_or_nodes
    state = {"cmds": 0, "wait": 0, "vars": {}}
    base = dict(ctx or {})

    def expand(text, line):
        c = dict(base)
        c["vars"] = state["vars"]
        if "{clipboard}" in text and "clipboard" not in c:
            c["clipboard"] = backend.clipboard()
        c["now"] = backend.now()
        return textops.expand(text, c)

    def block(items, depth):
        for it in items:
            if it["t"] == "repeat":
                for _ in range(it["n"]):
                    block(it["body"], depth)
            elif it["t"] == "if":
                block(it["then"] if check_condition(it["cond"], backend, state["vars"]) else it["else"], depth)
            else:
                state["cmds"] += 1
                if state["cmds"] > MAX_COMMANDS:
                    raise ScriptError(f"stopped: more than {MAX_COMMANDS} commands (is a loop running away?)", it["line"])
                one(it, depth)

    def one(it, depth):
        c, ln = it["c"], it["line"]
        if c == "stop":
            raise Stop
        if c == "key":
            backend.key(it["keys"])
        elif c == "wait":
            ms = it["ms"]
            if it.get("jitter"):
                ms = max(0, ms + int((base.get("rng") or random).uniform(-it["jitter"], it["jitter"])))
            ms = min(ms, MAX_ONE_WAIT_MS)
            state["wait"] += ms
            if state["wait"] > MAX_WAIT_MS:
                raise ScriptError("stopped: more than 60 s of waiting in total", ln)
            backend.wait(ms)
        elif c == "text":
            backend.text(expand(it["arg"], ln))
        elif c == "click":
            backend.click(it["arg"])
        elif c == "scroll":
            backend.scroll(it["n"])
        elif c == "media":
            backend.media(it["arg"])
        elif c in ("open", "app", "file", "notify"):
            backend.host({"open": "url"}.get(c, c), expand(it["arg"], ln))
        elif c == "shell":
            if not shell_ok:
                raise ScriptError("shell commands are switched off (Device page: 'Allow the pad to run shell commands')", ln)
            backend.host("shell", expand(it["arg"], ln))
        elif c == "set":
            state["vars"][it["name"]] = expand(it["arg"], ln)
        elif c == "exec":
            if not shell_ok:
                raise ScriptError("exec runs a shell command: switch on 'Allow the pad to run shell commands' first", ln)
            code, out = backend.exec(expand(it["arg"], ln))
            state["vars"]["exit"], state["vars"]["out"] = str(code), (out or "").strip().splitlines()[0][:200] if (out or "").strip() else ""
        elif c == "card":
            backend.card(*(expand(x, ln) for x in it["parts"]))
        elif c == "alert":
            backend.alert(it["hex"], it["times"])
        elif c == "layout":
            backend.host("layout", expand(it["arg"], ln))
        elif c == "http":
            state["vars"]["http"] = str(backend.http(expand(it["arg"], ln)))
        elif c == "ask":
            state["vars"]["answer"] = backend.ask(expand(it["arg"], ln))
        elif c == "translate":
            state["vars"]["translated"] = backend.translate(it["arg"])
        elif c == "moveto":
            backend.moveto(it["x"], it["y"])
        elif c == "clickat":
            backend.clickat(it["x"], it["y"], it["btn"])
        elif c == "do":
            backend.host(it["op"], expand(it["arg"], ln))
        elif c == "run":
            if depth >= MAX_DEPTH:
                raise ScriptError(f"stopped: scripts call each other more than {MAX_DEPTH} levels deep", ln)
            src = lookup(it["arg"]) if lookup else None
            if src is None:
                raise ScriptError(f"there is no saved script called '{it['arg']}'", ln)
            try:
                block(parse(src), depth + 1)
            except ScriptError as e:
                raise ScriptError(f"in '{it['arg']}': {e}", ln) from None
    try:
        block(nodes, 0)
    except Stop:
        pass
    return state["cmds"]


def dry_run(source, lookup=None, window=("", ""), clipboard="", now=None, ctx=None):
    """-> list of lines describing what the script would do, without doing it."""
    rec = Recorder(window, clipboard, now)
    n = run(source, rec, lookup, ctx, shell_ok=True)
    return rec.log + [f"({n} commands)"]


# ---------------------------------------------------------------- ready-made scripts (the Scripts page offers them as a starting point)
TEMPLATES = {
    "Work mode: open my tools": "# open the programs and pages for a work session\nnotify Work mode\napp code\nwait 800\nopen https://mail.google.com\nopen https://calendar.google.com\ndo dnd on\n",
    "Presentation: no distractions": "do dnd on\ndo mic mute\nkey F11\nnotify Presentation mode - notifications off\n",
    "Stand-up message": 'http POST https://hooks.example/replace-me {"text": "Stand-up notes {date}: "}\nnotify Posted\n',
    "Build and report": "exec make test\nif var exit == 0\ncard BUILD | passed | {out} |\nalert 00ff00 2\nelse\ncard BUILD | FAILED | exit {exit} |\nalert ff0000 4\nend\n",
    "Type today's date and a bullet": "text {date:%A %d %B %Y}\nkey enter\ntext - \n",
    "Fill a form (copy, tab, paste)": "key ctrl+c\nwait 150 jitter 40\nkey alt+tab\nwait 400\nkey ctrl+v\nkey tab\n",
    "Spam-click 10 times": "repeat 10\nclick left\nwait 60 jitter 20\nend\n",
    "Summarize the clipboard (AI)": "ask Summarize in two sentences: {clipboard}\ntext {answer}\n",
    "Translate and paste": "translate de\ntext {translated}\n",
    "Evening wind-down": "if time 20:00-23:59\ndo dnd on\ndo audio_out next\nalert 3030ff 2\nelse\nnotify It is not evening yet\nend\n",
}
