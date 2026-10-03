"""More cards for the pad's Info screen: countdown, world clock, git status, CI status, crypto price.
Plain functions (network / git calls are injectable) so they are testable without a GUI, a pad or the internet.
Every card is {"k","label","t","a","b"} like feeds.py; text is folded to ASCII for the pad's fonts."""
import datetime as dt
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from desk_lib.feeds import ascii_fold

KINDS = {"countdown": "Countdown to a date", "worldclock": "World clock", "git": "Git repository status",
         "ci": "GitHub Actions status", "crypto": "Crypto price", "quote": "Quote of the day", "birthday": "Birthdays / anniversaries",
         "ping": "Is a server up? (ping)", "http": "Is a website up? (HTTP)", "lyrics": "Lyrics of the playing song"}
TTL = {"countdown": 30, "worldclock": 15, "git": 20, "ci": 120, "crypto": 120, "quote": 600, "birthday": 600, "ping": 20, "http": 30, "lyrics": 2}       # seconds between refreshes

QUOTES = [
    ("Well begun is half done.", "Aristotle"), ("Simplicity is the ultimate sophistication.", "Leonardo da Vinci"),
    ("Done is better than perfect.", "Sheryl Sandberg"), ("What we think, we become.", "Buddha"),
    ("The only way to do great work is to love what you do.", "Steve Jobs"), ("Make it work, make it right, make it fast.", "Kent Beck"),
    ("Premature optimization is the root of all evil.", "Donald Knuth"), ("Programs must be written for people to read.", "Harold Abelson"),
    ("Stay hungry, stay foolish.", "Stewart Brand"), ("A journey of a thousand miles begins with a single step.", "Lao Tzu"),
    ("It always seems impossible until it is done.", "Nelson Mandela"), ("Fall seven times, stand up eight.", "Japanese proverb"),
    ("Slow is smooth, smooth is fast.", "Navy SEAL saying"), ("Perfection is achieved when nothing is left to take away.", "Antoine de Saint-Exupery"),
    ("The best way out is always through.", "Robert Frost"), ("Quality is not an act, it is a habit.", "Will Durant"),
    ("Talk is cheap. Show me the code.", "Linus Torvalds"), ("Measure twice, cut once.", "Carpenter's rule"),
    ("Energy and persistence conquer all things.", "Benjamin Franklin"), ("You miss 100% of the shots you do not take.", "Wayne Gretzky"),
    ("Everything should be made as simple as possible.", "Albert Einstein"), ("Small steps every day.", "Anonymous"),
]


def _fetch_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "DeskCompanion/1.3", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(300_000).decode("utf-8", "replace"))


def validate(item):
    """Clean a card definition from the UI / config or raise ValueError with a readable reason."""
    if not isinstance(item, dict) or item.get("type") not in KINDS:
        raise ValueError("unknown card type")
    t = item["type"]
    label = ascii_fold(item.get("label", ""), 24)
    a = str(item.get("arg", "")).strip()
    if t == "countdown":
        try:
            d = dt.date.fromisoformat(a)
        except ValueError:
            raise ValueError("enter the date as 2026-12-24") from None
        a = d.isoformat()
    elif t == "worldclock":
        zones = [z.strip() for z in a.split(",") if z.strip()]
        if not 1 <= len(zones) <= 3:
            raise ValueError("enter 1 to 3 time zones, e.g. Europe/Zurich, America/New_York, Asia/Tokyo")
        for z in zones:
            if not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z_+\-0-9]+){0,2}", z):
                raise ValueError(f"'{z}' is not a time zone name like Europe/Zurich")
        a = ", ".join(zones)
    elif t == "git":
        if not a:
            raise ValueError("enter the folder of the repository")
    elif t == "ci":
        if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", a):
            raise ValueError("enter the repository as owner/name")
    elif t == "crypto":
        a = a.lower()
        if not re.fullmatch(r"[a-z0-9\-]{2,40}(:[a-z]{3,4})?", a):
            raise ValueError("enter a coin id like bitcoin or ethereum:eur")
    elif t == "quote":
        a = a[:200]                                          # optional: a text file with one  quote | author  per line
    elif t == "birthday":
        a = ", ".join(f"{n} {d}" for n, d in parse_dates(a))
    elif t == "ping":
        parse_hostport(a)
    elif t == "http":
        if not re.fullmatch(r"https?://[^\s]+", a):
            raise ValueError("enter a web address starting with http:// or https://")
    elif t == "lyrics":
        a = ""
    return {"type": t, "label": label, "arg": a}


# ---------------------------------------------------------------- countdown
def countdown_card(label, target, today=None):
    today = today or dt.date.today()
    d = dt.date.fromisoformat(target) if isinstance(target, str) else target
    n = (d - today).days
    big = "TODAY" if n == 0 else f"{n} days" if n > 1 else "1 day" if n == 1 else f"{-n} d ago"
    return {"k": "e", "label": "COUNTDOWN", "t": big, "a": ascii_fold(label or "Event", 40), "b": d.strftime("%a %d %b %Y")}


# ---------------------------------------------------------------- world clock
def worldclock_card(label, zones, now=None, zone_factory=None):
    """zones: 'Europe/Zurich, Asia/Tokyo'. zone_factory(name) -> tzinfo (default: zoneinfo)."""
    if zone_factory is None:
        try:
            from zoneinfo import ZoneInfo as zone_factory
        except ImportError:
            raise ValueError("this Python has no time zone support") from None
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = []
    for z in [z.strip() for z in zones.split(",") if z.strip()][:3]:
        try:
            tz = zone_factory(z)
        except Exception:                                  # noqa: BLE001  (unknown zone, or no tz database on Windows)
            raise ValueError(f"unknown time zone '{z}' (on Windows run: pip install tzdata)") from None
        rows.append(f"{now.astimezone(tz).strftime('%H:%M')} {ascii_fold(z.split('/')[-1].replace('_', ' '), 14)}")
    rows += [""] * (3 - len(rows))
    return {"k": "c", "label": ascii_fold(label or "WORLD CLOCK", 24).upper(), "t": rows[0], "a": rows[1], "b": rows[2]}


# ---------------------------------------------------------------- git
def _git(path, args, run=None):
    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=6, check=False))
    r = run(["git", "-C", path, *args])
    if r.returncode != 0:
        raise ValueError((r.stderr or "git failed").strip().splitlines()[-1][:80] if (r.stderr or "").strip() else "git failed")
    return r.stdout


def git_card(label, path, run=None):
    try:
        out = _git(path, ["status", "--porcelain=v1", "-b"], run)
    except FileNotFoundError:
        raise ValueError("git is not installed") from None
    lines = out.splitlines()
    head = lines[0] if lines else "## ?"
    m = re.match(r"## (?:No commits yet on |Initial commit on )?(.+?)(?:\.\.\.\S+)?(?: \[(.*)\])?$", head)
    branch = m.group(1) if m else "?"
    track = m.group(2) or "" if m else ""
    changes = len(lines) - 1
    ahead = re.search(r"ahead (\d+)", track)
    behind = re.search(r"behind (\d+)", track)
    sync = ", ".join(x for x in ((f"ahead {ahead.group(1)}" if ahead else ""), (f"behind {behind.group(1)}" if behind else "")) if x) or "in sync"
    return {"k": "c", "label": ascii_fold(label or "GIT", 24).upper(), "t": ascii_fold(branch, 24),
            "a": "clean" if changes == 0 else f"{changes} change{'s' if changes != 1 else ''}", "b": sync}


# ---------------------------------------------------------------- GitHub Actions (public repositories, no key: 60 requests/hour)
def ci_card(label, repo, fetch=None, now=None):
    fetch = fetch or _fetch_json
    d = fetch(f"https://api.github.com/repos/{repo}/actions/runs?per_page=1")
    runs = d.get("workflow_runs") or []
    if not runs:
        raise ValueError("no workflow runs found (is the repository public?)")
    r = runs[0]
    state = (r.get("conclusion") or r.get("status") or "?").replace("_", " ")
    rel = ""
    try:
        ts = dt.datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
        mins = int(((now or dt.datetime.now(dt.timezone.utc)) - ts).total_seconds() // 60)
        rel = "just now" if mins < 2 else f"{mins} min ago" if mins < 90 else f"{mins // 60} h ago" if mins < 48 * 60 else f"{mins // 1440} d ago"
    except (KeyError, ValueError):
        pass
    return {"k": "c", "label": ascii_fold(label or "CI", 24).upper(), "t": ascii_fold(state, 24).upper(),
            "a": ascii_fold(f"{r.get('name', '')} {r.get('head_branch', '')}", 40), "b": rel}


# ---------------------------------------------------------------- crypto (CoinGecko public API)
def crypto_card(label, coin, fetch=None):
    fetch = fetch or _fetch_json
    coin_id, _, cur = coin.partition(":")
    cur = (cur or "usd").lower()
    d = fetch("https://api.coingecko.com/api/v3/simple/price?" + urllib.parse.urlencode(
        {"ids": coin_id, "vs_currencies": cur, "include_24hr_change": "true"}))
    row = d.get(coin_id)
    if not row or cur not in row:
        raise ValueError(f"no price for '{coin_id}' in {cur.upper()}")
    price, chg = float(row[cur]), row.get(f"{cur}_24h_change")
    p = f"{price:,.0f}" if price >= 100 else f"{price:,.2f}" if price >= 1 else f"{price:.4f}"
    return {"k": "c", "label": ascii_fold(label or coin_id, 24).upper(), "t": f"{p} {cur.upper()}",
            "a": "" if chg is None else f"{float(chg):+.1f}% in 24 h", "b": ""}


# ---------------------------------------------------------------- quote of the day
def quote_card(label, source="", today=None, read=None):
    """Same quote all day (by date). source: optional text file, one 'quote | author' per line (else the built-in list)."""
    today = today or dt.date.today()
    pool = QUOTES
    if source:
        try:
            lines = (read or (lambda p: open(p, encoding="utf-8").read()))(source).splitlines()
        except OSError as e:
            raise ValueError(f"cannot read {source}: {e.strerror or e}") from None
        mine = [(q.strip(), a.strip()) for q, _, a in (ln.partition("|") for ln in lines) if q.strip()]
        if not mine:
            raise ValueError("the quote file is empty")
        pool = mine
    q, who = pool[today.toordinal() % len(pool)]
    words, rows = q.split(), [""]
    for w in words:                                          # wrap onto 3 short lines of the card
        if len(rows[-1]) + len(w) + 1 > 26 and len(rows) < 3:
            rows.append("")
        rows[-1] = (rows[-1] + " " + w).strip()
    rows += [""] * (3 - len(rows))
    f = ascii_fold
    return {"k": "c", "label": f(label or "QUOTE", 24).upper(), "t": f(rows[0], 28), "a": f(rows[1], 40), "b": f((rows[2] + "  - " + who).strip(" -"), 40)}


# ---------------------------------------------------------------- birthdays / anniversaries
def parse_dates(text):
    """'Anna 03-14, Max 1990-07-02' -> [('Anna', '03-14'), ('Max', '1990-07-02')]. Raises ValueError."""
    out = []
    for part in [p.strip() for p in str(text).split(",") if p.strip()]:
        name, _, d = part.rpartition(" ")
        if not name or not re.fullmatch(r"(\d{4}-)?\d{2}-\d{2}", d):
            raise ValueError(f"'{part}' should look like  Anna 03-14  or  Max 1990-07-02")
        try:
            dt.date.fromisoformat(d if len(d) == 10 else "2000-" + d)
        except ValueError:
            raise ValueError(f"'{d}' is not a real date") from None
        out.append((name.strip()[:16], d))
    if not 1 <= len(out) <= 12:
        raise ValueError("enter 1 to 12 entries like  Anna 03-14, Max 1990-07-02")
    return out


def birthday_card(label, arg, today=None):
    """The next birthday / anniversary: who, in how many days, and (with a year) the age they turn."""
    today = today or dt.date.today()
    best = None
    for name, d in parse_dates(arg):
        year, md = (int(d[:4]), d[5:]) if len(d) == 10 else (None, d)
        month, day = int(md[:2]), int(md[3:])
        for y in (today.year, today.year + 1):
            try:
                nxt = dt.date(y, month, day)
            except ValueError:                               # 29 Feb in a non-leap year -> 28 Feb
                nxt = dt.date(y, month, 28)
            if nxt >= today:
                break
        n = (nxt - today).days
        if best is None or n < best[0]:
            best = (n, name, nxt, None if year is None else nxt.year - year)
    n, name, nxt, age = best
    when = "TODAY!" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"
    return {"k": "c", "label": ascii_fold(label or "BIRTHDAY", 24).upper(), "t": ascii_fold(name, 24), "a": when,
            "b": (f"turns {age}" if age is not None and age > 0 else "") + ("" if age is None or age <= 0 else "  ") + nxt.strftime("%d %b")}


# ---------------------------------------------------------------- is it up?
def parse_hostport(arg):
    m = re.fullmatch(r"([A-Za-z0-9.\-]{1,253})(?::(\d{1,5}))?", str(arg).strip())
    if not m or (m.group(2) and not 1 <= int(m.group(2)) <= 65535):
        raise ValueError("enter a server like  example.com  or  192.168.1.1:22  (default port 443)")
    return m.group(1), int(m.group(2) or 443)


def ping_card(label, arg, connect=None, clock=None):
    """TCP connect time to host:port (no admin rights needed, unlike ICMP ping)."""
    import socket                                            # noqa: PLC0415
    import time                                              # noqa: PLC0415
    host, port = parse_hostport(arg)
    clock = clock or time.perf_counter
    t0 = clock()
    try:
        (connect or (lambda h, p: socket.create_connection((h, p), timeout=3).close()))(host, port)
        ms = (clock() - t0) * 1000
        t, b = f"{ms:.0f} ms", "reachable"
    except OSError as e:
        t, b = "DOWN", ascii_fold(str(e.strerror or e), 30)
    return {"k": "c", "label": ascii_fold(label or "PING", 24).upper(), "t": t, "a": f"{host}:{port}", "b": b}


def http_card(label, url, fetch=None, clock=None):
    import time                                              # noqa: PLC0415
    clock = clock or time.perf_counter

    def default(u):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers={"User-Agent": "DeskCompanion"}), timeout=6) as r:      # noqa: S310
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
    t0 = clock()
    try:
        status = (fetch or default)(url)
        ms = (clock() - t0) * 1000
        ok = 200 <= status < 400
        t, b = ("UP" if ok else "ERROR"), f"HTTP {status}  {ms:.0f} ms"
    except (OSError, ValueError) as e:
        t, b = "DOWN", ascii_fold(str(getattr(e, "reason", None) or e), 30)
    host = urllib.parse.urlparse(url).netloc
    return {"k": "c", "label": ascii_fold(label or "WEBSITE", 24).upper(), "t": t, "a": ascii_fold(host, 40), "b": b}


_LYRICS = None


def build(item, **kw):
    """Card for a validated definition. kw: today/now/zone_factory/run/fetch overrides for tests."""
    t, label, arg = item["type"], item["label"], item["arg"]
    if t == "countdown":
        return countdown_card(label, arg, kw.get("today"))
    if t == "worldclock":
        return worldclock_card(label, arg, kw.get("now"), kw.get("zone_factory"))
    if t == "git":
        return git_card(label, arg, kw.get("run"))
    if t == "ci":
        return ci_card(label, arg, kw.get("fetch"), kw.get("now"))
    if t == "quote":
        return quote_card(label, arg, kw.get("today"), kw.get("read"))
    if t == "birthday":
        return birthday_card(label, arg, kw.get("today"))
    if t == "ping":
        return ping_card(label, arg, kw.get("connect"), kw.get("clock"))
    if t == "http":
        return http_card(label, arg, kw.get("fetch"), kw.get("clock"))
    if t == "lyrics":
        global _LYRICS
        from desk_lib import feeds, lyrics                   # noqa: PLC0415
        _LYRICS = _LYRICS or lyrics.Lyrics()
        return _LYRICS.card(kw.get("np") or feeds.now_playing(), kw.get("pos"))
    return crypto_card(label, arg, kw.get("fetch"))
