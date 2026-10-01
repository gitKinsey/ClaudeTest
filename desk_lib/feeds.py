"""Data for the pad's INFO screen (mode 6): now playing, weather, next calendar event, notification badges.
Everything here is plain functions / one small class, so it can be tested without a GUI or a pad."""
import datetime as dt
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import threading
import unicodedata
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPEN_METEO = os.environ.get("DESK_COMPANION_METEO", "https://api.open-meteo.com/v1")
GEOCODE = os.environ.get("DESK_COMPANION_GEOCODE", "https://geocoding-api.open-meteo.com/v1")

WMO = {0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
       55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
       66: "Freezing rain", 67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
       80: "Rain showers", 81: "Rain showers", 82: "Violent showers", 85: "Snow showers", 86: "Snow showers",
       95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail"}


def ascii_fold(s, limit=60):
    """The pad's built-in fonts are ASCII only: fold accents (e -> e), drop everything else."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"\s+", " ", s.encode("ascii", "ignore").decode()).strip()[:limit]


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "DeskCompanion/1.3"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(200_000).decode("utf-8", "replace"))


# ---------------------------------------------------------------- weather (Open-Meteo, no API key)
def geocode(name):
    """-> (lat, lon, label). Raises ValueError when the place is unknown."""
    d = _get_json(f"{GEOCODE}/search?{urllib.parse.urlencode({'name': name, 'count': 1, 'language': 'en'})}")
    res = d.get("results") or []
    if not res:
        raise ValueError(f"no place called '{name}' found")
    r = res[0]
    return float(r["latitude"]), float(r["longitude"]), ascii_fold(r.get("name", name), 24)


def weather_fetch(lat, lon):
    qs = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,wind_speed_10m"})
    c = _get_json(f"{OPEN_METEO}/forecast?{qs}").get("current") or {}
    if "temperature_2m" not in c:
        raise ValueError("the weather service returned no data")
    return {"temp": float(c["temperature_2m"]), "code": int(c.get("weather_code", 0)), "wind": float(c.get("wind_speed_10m", 0))}


def weather_card(label, w, fahrenheit=False):
    t = w["temp"] * 9 / 5 + 32 if fahrenheit else w["temp"]
    return {"k": "w", "label": f"WEATHER  {ascii_fold(label, 14)}".strip(), "t": f"{t:.0f} {'F' if fahrenheit else 'C'}",
            "a": WMO.get(w["code"], "Weather"), "b": f"wind {w['wind']:.0f} km/h"}


# ---------------------------------------------------------------- now playing
def _sh(args, timeout=2):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def now_playing(runner=None, system=None):
    """-> {"playing": bool, "title", "artist", "album"} or None. Linux: playerctl; macOS: Spotify / Music via osascript;
    Windows: the Spotify window title (best effort)."""
    run, system = runner or _sh, system or platform.system()
    try:
        if system == "Linux":
            out = run(["playerctl", "metadata", "--format", "{{status}}|{{title}}|{{artist}}|{{album}}"])
            if not out:
                return None
            st, title, artist, album = (out.strip().split("|") + ["", "", "", ""])[:4]
            return {"playing": st.lower() == "playing", "title": title, "artist": artist, "album": album} if title else None
        if system == "Darwin":
            for app in ("Spotify", "Music"):
                out = run(["osascript", "-e", f'if application "{app}" is running then tell application "{app}" to return (player state as string) '
                           f'& "|" & (name of current track) & "|" & (artist of current track) & "|" & (album of current track)'])
                if out and out.count("|") >= 3:
                    st, title, artist, album = out.strip().split("|", 3)
                    return {"playing": st.lower() == "playing", "title": title, "artist": artist, "album": album}
            return None
        if system == "Windows":
            out = run(["powershell", "-NoProfile", "-Command", "(Get-Process spotify -ErrorAction SilentlyContinue | "
                       "Where-Object {$_.MainWindowTitle} | Select-Object -First 1).MainWindowTitle"], timeout=4)
            title = (out or "").strip()
            if " - " in title:
                artist, _, song = title.partition(" - ")
                return {"playing": True, "title": song, "artist": artist, "album": ""}
            return None
    except Exception:                                      # noqa: BLE001
        return None
    return None


def music_card(np):
    return {"k": "m", "label": "NOW PLAYING" if np["playing"] else "PAUSED", "t": ascii_fold(np["title"], 40),
            "a": ascii_fold(np["artist"], 40), "b": ascii_fold(np["album"], 40)}


def have_player_tool():
    return platform.system() != "Linux" or shutil.which("playerctl") is not None


# ---------------------------------------------------------------- calendar (.ics file or URL; single events, no recurrence rules)
def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text)


def _parse_dt(value, params, local_tz):
    v = value.strip()
    if re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v, "%Y%m%d").replace(tzinfo=local_tz), True
    if v.endswith("Z"):
        return dt.datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).astimezone(local_tz), False
    return dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S").replace(tzinfo=local_tz), False        # floating / TZID: treated as local time


def parse_ics(text, local_tz=None):
    """-> list of (start_datetime(aware), all_day, summary), unsorted. Recurring events are not expanded."""
    local_tz = local_tz or dt.datetime.now().astimezone().tzinfo
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", _unfold(text), re.S):
        start, summary, allday = None, "", False
        for ln in block.splitlines():
            name, _, value = ln.partition(":")
            key, _, params = name.partition(";")
            if key.upper() == "DTSTART":
                try:
                    start, allday = _parse_dt(value, params, local_tz)
                except ValueError:
                    start = None
            elif key.upper() == "SUMMARY":
                summary = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")
        if start:
            out.append((start, allday, summary))
    return out


def next_event(events, now=None):
    now = now or dt.datetime.now().astimezone()
    upcoming = [e for e in events if e[0] + (dt.timedelta(days=1) if e[1] else dt.timedelta(minutes=1)) > now]
    return min(upcoming, key=lambda e: e[0]) if upcoming else None


def event_card(ev, now=None):
    now = now or dt.datetime.now().astimezone()
    start, allday, summary = ev
    delta = start - now
    mins = int(delta.total_seconds() // 60)
    if allday:
        when, rel = "all day", ("today" if start.date() == now.date() else start.strftime("%a %d %b"))
    else:
        when = start.strftime("%H:%M")
        rel = "now" if mins <= 0 else f"in {mins} min" if mins < 90 else f"in {mins // 60} h" if mins < 36 * 60 else start.strftime("%a %d %b")
    return {"k": "e", "label": "NEXT EVENT", "t": when, "a": ascii_fold(summary, 40), "b": rel}


def load_calendar(src):
    """src: local path or http(s) URL of an .ics file."""
    if re.match(r"https?://", src or ""):
        req = urllib.request.Request(src, headers={"User-Agent": "DeskCompanion/1.3"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(2_000_000).decode("utf-8", "replace")
    with open(os.path.expanduser(src), encoding="utf-8", errors="replace") as f:
        return f.read(2_000_000)


# ---------------------------------------------------------------- notification badges: any script can push a counter
class BadgeServer:
    """127.0.0.1 only.  GET/POST /badge?name=mail&n=3&token=SECRET   (n=0 removes the badge).  Example for a script:
         curl "http://127.0.0.1:PORT/badge?name=mail&n=3&token=SECRET"
    The token stops other programs / web pages from pushing counters."""

    def __init__(self, token=None, port=0):
        self.token = token or secrets.token_urlsafe(9)
        self.badges = {}
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                if u.path != "/badge":
                    return self._send(404, "not found")
                if not secrets.compare_digest(q.get("token", [""])[0], outer.token):
                    return self._send(403, "bad token")
                name = ascii_fold(q.get("name", [""])[0], 8)
                try:
                    n = max(0, min(999, int(q.get("n", ["0"])[0])))
                except ValueError:
                    return self._send(400, "n must be a number")
                if not name:
                    return self._send(400, "name missing")
                outer.set(name, n)
                self._send(200, "ok")

            do_GET = do_POST = _do

            def _send(self, code, text):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(text.encode())

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def set(self, name, n):
        with self.lock:
            if n:
                self.badges[name] = n
            else:
                self.badges.pop(name, None)

    def get(self):
        with self.lock:
            return [{"name": k, "n": v} for k, v in list(self.badges.items())[:4]]

    def url(self):
        return f"http://127.0.0.1:{self.port}/badge?name=mail&n=3&token={self.token}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
