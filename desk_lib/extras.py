"""More cards for the pad's Info screen: countdown, world clock, git status, CI status, crypto price.
Plain functions (network / git calls are injectable) so they are testable without a GUI, a pad or the internet.
Every card is {"k","label","t","a","b"} like feeds.py; text is folded to ASCII for the pad's fonts."""
import datetime as dt
import json
import math
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from desk_lib.feeds import ascii_fold

KINDS = {"countdown": "Countdown to a date", "worldclock": "World clock", "git": "Git repository status",
         "ci": "GitHub Actions status", "crypto": "Crypto price", "quote": "Quote of the day", "birthday": "Birthdays / anniversaries",
         "ping": "Is a server up? (ping)", "http": "Is a website up? (HTTP)", "lyrics": "Lyrics of the playing song",
         "progress": "Progress of the year / month / week / day (bar)", "battery": "Laptop battery (ring)", "disk": "Disk usage (ring)", "load": "CPU load (ring)",
         "moon": "Moon phase (ring)", "sun": "Sunrise and sunset", "network": "Network speed", "goal": "Daily goal (counter ring)", "rain": "Rain chance, next 12 hours (ring)",
         "window": "The program in front"}
TTL = {"countdown": 30, "worldclock": 15, "git": 20, "ci": 120, "crypto": 120, "quote": 600, "birthday": 600, "ping": 20, "http": 30, "lyrics": 2,
       "progress": 60, "battery": 30, "disk": 120, "load": 2, "moon": 600, "sun": 300, "network": 2, "goal": 5, "rain": 900, "window": 1}       # seconds between refreshes

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
    elif t == "lyrics" or t in ("battery", "load", "moon", "network", "window", "rain"):
        a = ""
    elif t == "sun":
        if a:
            parse_latlon(a)
    elif t == "goal":
        if not re.fullmatch(r"[\w.\- ]{1,24}:\d{1,6}", a):
            raise ValueError("enter  counter-name:target , e.g.  water:8  (the counter is the {counter:water} of a snippet key)")
    elif t == "progress":
        a = a.lower() or "year"
        if a not in ("year", "month", "week", "day", "work"):
            raise ValueError("enter year, month, week, day or work (the 9-17 working day)")
    elif t == "disk":
        a = a or "/"
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


# ---------------------------------------------------------------- bars and rings (card kinds "p" progress and "r" ring on firmware 1.5)
def progress_card(label, which, now=None):
    now = now or dt.datetime.now()
    if which == "year":
        start, end = dt.datetime(now.year, 1, 1), dt.datetime(now.year + 1, 1, 1)
        tag, sub = f"of {now.year}", f"day {now.timetuple().tm_yday} of {(end - start).days}"
    elif which == "month":
        start = dt.datetime(now.year, now.month, 1)
        end = dt.datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
        tag, sub = now.strftime("of %B"), f"day {now.day} of {(end - start).days}"
    elif which == "week":
        start = dt.datetime(now.year, now.month, now.day) - dt.timedelta(days=now.weekday())
        end = start + dt.timedelta(days=7)
        tag, sub = "of this week", now.strftime("%A")
    elif which == "day":
        start = dt.datetime(now.year, now.month, now.day)
        end = start + dt.timedelta(days=1)
        tag, sub = "of today", now.strftime("%H:%M")
    else:                                                    # "work": 09:00 - 17:00
        start, end = dt.datetime(now.year, now.month, now.day, 9), dt.datetime(now.year, now.month, now.day, 17)
        tag, sub = "of the working day", "9:00 - 17:00"
    pct = max(0.0, min(100.0, (now - start) / (end - start) * 100.0))
    return {"k": "p", "label": ascii_fold(label or "PROGRESS", 24).upper(), "t": str(int(pct)), "a": tag, "b": sub}


def battery_card(label, sensor=None):
    if sensor is None:
        import psutil                                        # noqa: PLC0415
        sensor = getattr(psutil, "sensors_battery", lambda: None)
    b = sensor()
    if b is None:
        raise ValueError("this computer has no battery")
    left = ""
    if not b.power_plugged and b.secsleft and b.secsleft > 0:
        left = f"{b.secsleft // 3600}h {(b.secsleft // 60) % 60:02d}m left"
    return {"k": "r", "label": ascii_fold(label or "BATTERY", 24).upper(), "t": str(int(round(b.percent))), "a": "charging" if b.power_plugged else "on battery", "b": left}


def disk_card(label, path, usage=None):
    if usage is None:
        import psutil                                        # noqa: PLC0415
        usage = psutil.disk_usage
    try:
        u = usage(path)
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e.strerror or e}") from None
    gb = 1024 ** 3
    return {"k": "r", "label": ascii_fold(label or "DISK", 24).upper(), "t": str(int(round(u.percent))), "a": f"{u.used / gb:.0f} of {u.total / gb:.0f} GB", "b": ascii_fold(path, 20)}


def load_card(label, cpu=None, ram=None):
    if cpu is None:
        import psutil                                        # noqa: PLC0415
        cpu, ram = psutil.cpu_percent(None), psutil.virtual_memory().percent
    return {"k": "r", "label": ascii_fold(label or "CPU", 24).upper(), "t": str(int(round(cpu))), "a": "CPU load", "b": f"RAM {int(round(ram or 0))}%"}


# ---------------------------------------------------------------- moon, sun, network, goal, rain
SYNODIC = 29.530588853
_NEW_MOON = dt.datetime(2000, 1, 6, 18, 14)                        # a known new moon (UTC)
PHASES = ("New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent")


def moon_card(label, now=None):
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    age = ((now - _NEW_MOON).total_seconds() / 86400.0) % SYNODIC
    illum = (1 - math.cos(2 * math.pi * age / SYNODIC)) / 2 * 100
    name = PHASES[int((age / SYNODIC * 8) + 0.5) % 8]
    return {"k": "r", "label": ascii_fold(label or "MOON", 24).upper(), "t": str(int(round(illum))), "a": name, "b": f"day {age:.0f} of 29.5"}


def parse_latlon(text):
    m = re.fullmatch(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*", str(text))
    if not m or abs(float(m.group(1))) > 90 or abs(float(m.group(2))) > 180:
        raise ValueError("enter the place as  47.37, 8.54  (latitude, longitude)")
    return float(m.group(1)), float(m.group(2))


def sun_times(lat, lon, day, utc_offset_h):
    """NOAA's solar calculator -> (sunrise, sunset) as local datetime.time, or (None, None) for polar day / night."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = 0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
    lat_r = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl)) - math.tan(lat_r) * math.tan(decl)
    if cos_ha > 1 or cos_ha < -1:
        return None, None
    ha = math.degrees(math.acos(cos_ha))

    def at(minutes_utc):
        mins = (minutes_utc + utc_offset_h * 60) % 1440
        return dt.time(int(mins // 60), int(mins % 60))
    return at(720 - 4 * (lon + ha) - eqt), at(720 - 4 * (lon - ha) - eqt)


def sun_card(label, latlon, now=None, utc_offset_h=None):
    now = now or dt.datetime.now()
    if utc_offset_h is None:
        utc_offset_h = (now.astimezone().utcoffset() or dt.timedelta()).total_seconds() / 3600
    rise, sset = sun_times(latlon[0], latlon[1], now.date(), utc_offset_h)
    if rise is None:
        polar = (latlon[0] > 0) == (now.month in (4, 5, 6, 7, 8, 9))                  # the sun stays up in the hemisphere's summer, stays down in its winter
        return {"k": "c", "label": ascii_fold(label or "SUN", 24).upper(), "t": "polar day" if polar else "polar night", "a": "no sunrise or sunset today", "b": ""}
    mins = now.hour * 60 + now.minute
    r, s_ = rise.hour * 60 + rise.minute, sset.hour * 60 + sset.minute
    up = r <= mins < s_
    light = (s_ - r) % 1440
    return {"k": "c", "label": ascii_fold(label or "SUN", 24).upper(), "t": f"{rise:%H:%M} - {sset:%H:%M}", "a": "the sun is up" if up else "the sun is down", "b": f"daylight {light // 60}h {light % 60:02d}m"}


_NET_LAST = {}


def network_card(label, counters=None, now=None):
    """Download / upload speed since the previous call (the first call has nothing to compare with yet)."""
    import time                                                    # noqa: PLC0415
    if counters is None:
        import psutil                                              # noqa: PLC0415
        counters = psutil.net_io_counters()
    t = now if now is not None else time.monotonic()
    prev = _NET_LAST.get("v")
    _NET_LAST["v"] = (t, counters.bytes_recv, counters.bytes_sent)
    f = lambda b: f"{b / 1e6:.1f} MB/s" if b >= 1e6 else f"{b / 1e3:.0f} KB/s"   # noqa: E731
    if not prev or t <= prev[0]:
        return {"k": "c", "label": ascii_fold(label or "NETWORK", 24).upper(), "t": "...", "a": "measuring", "b": ""}
    dt_ = t - prev[0]
    return {"k": "c", "label": ascii_fold(label or "NETWORK", 24).upper(), "t": "D " + f((counters.bytes_recv - prev[1]) / dt_), "a": "U " + f((counters.bytes_sent - prev[2]) / dt_), "b": ""}


def goal_card(label, arg, counters):
    name, _, target = arg.rpartition(":")
    value, target = int(counters.get(name.strip(), 0)), max(1, int(target))
    pct = min(100, value * 100 // target)
    return {"k": "p", "label": ascii_fold(label or name or "GOAL", 24).upper(), "t": str(pct), "a": f"{value} of {target}", "b": "done!" if value >= target else f"{target - value} to go"}


def rain_card(label, latlon, fetch=None, now=None):
    fetch = fetch or _fetch_json
    d = fetch("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(
        {"latitude": latlon[0], "longitude": latlon[1], "hourly": "precipitation_probability", "forecast_days": 2, "timezone": "auto"}))
    times, probs = (d.get("hourly") or {}).get("time") or [], (d.get("hourly") or {}).get("precipitation_probability") or []
    now = now or dt.datetime.now()
    cur = now.strftime("%Y-%m-%dT%H:00")
    start = next((i for i, t in enumerate(times) if t >= cur), None)
    if start is None or not probs:
        raise ValueError("no forecast available")
    window = [(p if p is not None else 0, times[start + i]) for i, p in enumerate(probs[start:start + 12])]
    best = max(window)
    return {"k": "r", "label": ascii_fold(label or "RAIN", 24).upper(), "t": str(int(best[0])), "a": "chance of rain, 12 h", "b": f"peak at {best[1][11:16]}"}


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
    if t == "moon":
        return moon_card(label, kw.get("now"))
    if t == "sun":
        ll = parse_latlon(arg) if arg else kw.get("latlon")
        if not ll or ll[0] is None:
            raise ValueError("enter a place (latitude, longitude) or set a city under Weather")
        return sun_card(label, ll, kw.get("now"), kw.get("utc_offset_h"))
    if t == "network":
        return network_card(label, kw.get("counters"), kw.get("clock"))
    if t == "goal":
        return goal_card(label, arg, kw.get("counter_values") if kw.get("counter_values") is not None else {})
    if t == "rain":
        ll = kw.get("latlon")
        if not ll or ll[0] is None:
            raise ValueError("set a city under Weather first (the forecast needs a place)")
        return rain_card(label, ll, kw.get("fetch"), kw.get("now"))
    if t == "window":
        w = kw.get("window") or ("", "")
        if not w[0] and not w[1]:
            raise ValueError("no program in front")
        return {"k": "c", "label": ascii_fold(label or "ACTIVE", 24).upper(), "t": ascii_fold(w[0], 24), "a": ascii_fold(w[1], 40), "b": ""}
    if t == "progress":
        return progress_card(label, arg, kw.get("now"))
    if t == "battery":
        return battery_card(label, kw.get("sensor"))
    if t == "disk":
        return disk_card(label, arg, kw.get("usage"))
    if t == "load":
        return load_card(label, kw.get("cpu"), kw.get("ram"))
    if t == "lyrics":
        global _LYRICS
        from desk_lib import feeds, lyrics                   # noqa: PLC0415
        _LYRICS = _LYRICS or lyrics.Lyrics()
        return _LYRICS.card(kw.get("np") or feeds.now_playing(), kw.get("pos"))
    return crypto_card(label, arg, kw.get("fetch"))
