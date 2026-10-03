"""Text snippets with variables, and clipboard transforms - pure functions, no GUI.

expand("Report {date} #{counter:report}", ctx)  ->  "Report 2026-10-03 #7"
transform("snake", "Hello World")               ->  "hello_world"

Variables:  {date} {time} {datetime} {weekday} {iso}  - the current moment; {date:%d.%m.%Y} takes any strftime format
            {clipboard}   {user}   {host}   {uuid}   {random:1-100}   {counter:name} (persisted, +1 on every use)
            {{ and }} are literal braces. Unknown variables are left as typed so a typo is visible, not silent."""
import base64
import getpass
import json
import random
import re
import socket
import uuid as _uuid
from datetime import datetime
from urllib.parse import quote, unquote

MAX_TEXT = 2000

_VAR = re.compile(r"\{\{|\}\}|\{([a-zA-Z_]+)(?::([^{}]*))?\}")


def expand(template, ctx=None):
    """ctx keys (all optional): now (datetime), clipboard (str), counter (callable name -> int), user, host, rng (random.Random)."""
    ctx = ctx or {}
    now = ctx.get("now") or datetime.now()
    rng = ctx.get("rng") or random

    def sub(m):
        whole = m.group(0)
        if whole == "{{":
            return "{"
        if whole == "}}":
            return "}"
        name, arg = m.group(1).lower(), m.group(2)
        try:
            if name in ("date", "time", "datetime"):
                fmt = arg or {"date": "%Y-%m-%d", "time": "%H:%M", "datetime": "%Y-%m-%d %H:%M"}[name]
                return now.strftime(fmt)
            if name == "weekday":
                return now.strftime("%A")
            if name == "iso":
                return now.replace(microsecond=0).isoformat()
            if name == "clipboard":
                return str(ctx.get("clipboard") or "")
            if name == "user":
                return ctx.get("user") or getpass.getuser()
            if name == "host":
                return ctx.get("host") or socket.gethostname()
            if name == "uuid":
                return str(_uuid.UUID(int=rng.getrandbits(128), version=4)) if hasattr(rng, "getrandbits") else str(_uuid.uuid4())
            if name == "random":
                lo, hi = (int(x) for x in (arg or "1-100").split("-", 1))
                return str(rng.randint(min(lo, hi), max(lo, hi)))
            if name == "counter":
                fn = ctx.get("counter")
                return str(fn((arg or "default").strip()[:32])) if fn else whole
        except (ValueError, TypeError, KeyError):
            return whole
        return whole
    return _VAR.sub(sub, str(template))[:MAX_TEXT]


def _words(s):
    return re.findall(r"[A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s))


def _json(s, pretty):
    try:
        obj = json.loads(s)
    except ValueError as e:
        raise ValueError(f"the clipboard is not valid JSON ({e})") from None
    return json.dumps(obj, indent=2, ensure_ascii=False) if pretty else json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _b64d(s):
    try:
        return base64.b64decode(s.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ValueError("the clipboard is not valid Base64 text") from None


TRANSFORMS = {
    "upper": ("UPPER CASE", lambda s: s.upper()),
    "lower": ("lower case", lambda s: s.lower()),
    "title": ("Title Case", lambda s: s.title()),
    "sentence": ("Sentence case", lambda s: ". ".join(p[:1].upper() + p[1:] for p in s.lower().split(". "))),
    "trim": ("Trim spaces", lambda s: "\n".join(line.strip() for line in s.strip().splitlines())),
    "oneline": ("Join into one line", lambda s: " ".join(s.split())),
    "snake": ("snake_case", lambda s: "_".join(w.lower() for w in _words(s))),
    "kebab": ("kebab-case", lambda s: "-".join(w.lower() for w in _words(s))),
    "camel": ("camelCase", lambda s: "".join(w.lower() if i == 0 else w.capitalize() for i, w in enumerate(_words(s)))),
    "pascal": ("PascalCase", lambda s: "".join(w.capitalize() for w in _words(s))),
    "json_pretty": ("JSON: pretty-print", lambda s: _json(s, True)),
    "json_min": ("JSON: minify", lambda s: _json(s, False)),
    "url_encode": ("URL-encode", lambda s: quote(s, safe="")),
    "url_decode": ("URL-decode", lambda s: unquote(s)),
    "b64_encode": ("Base64 encode", lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")),
    "b64_decode": ("Base64 decode", _b64d),
    "sort_lines": ("Sort lines", lambda s: "\n".join(sorted(s.splitlines(), key=str.lower))),
    "unique_lines": ("Remove duplicate lines", lambda s: "\n".join(dict.fromkeys(s.splitlines()))),
    "reverse_lines": ("Reverse line order", lambda s: "\n".join(reversed(s.splitlines()))),
    "quote": ("Quote each line ( > )", lambda s: "\n".join("> " + line for line in s.splitlines())),
    "count": ("Count words and characters", lambda s: f"{len(s.split())} words, {len(s)} characters, {len(s.splitlines()) or 0} lines"),
}


def transform(name, text):
    """Apply a named transform; raises ValueError (readable message) when it does not apply."""
    if name not in TRANSFORMS:
        raise ValueError(f"unknown transform '{name}'")
    return TRANSFORMS[name][1](str(text))[:MAX_TEXT]


def transform_labels():
    return [(k, v[0]) for k, v in TRANSFORMS.items()]
