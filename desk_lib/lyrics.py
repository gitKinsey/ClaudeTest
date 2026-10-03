"""Synced lyrics for the Info screen: the current line of the song that is playing (LRCLIB, no key), next line below.
Needs the playback position: Linux (playerctl position) and macOS (Spotify / Music) provide it; Windows does not -> shows the song's first lines.
`fetch(url) -> dict` and `runner(args) -> str|None` are injectable."""
import bisect
import json
import platform
import re
import subprocess
import urllib.parse
import urllib.request

from desk_lib.feeds import ascii_fold

LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text):
    """'[01:23.45] words' lines -> sorted [(seconds, 'words')]. Lines with several timestamps are expanded; empty lines kept as ''."""
    out = []
    for raw in str(text or "").splitlines():
        stamps = LINE.findall(raw)
        if not stamps:
            continue
        words = LINE.sub("", raw).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), words))
    out.sort(key=lambda x: x[0])
    return out


def line_at(lines, pos):
    """-> (current, next) text for playback position pos (seconds)."""
    if not lines:
        return "", ""
    i = bisect.bisect_right([t for t, _ in lines], pos) - 1
    cur = lines[i][1] if i >= 0 else ""
    nxt = lines[i + 1][1] if i + 1 < len(lines) else ""
    return cur, nxt


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "DeskCompanion (https://github.com/gitKinsey/ClaudeTest)"})
    with urllib.request.urlopen(req, timeout=8) as r:                       # noqa: S310 - https
        return json.loads(r.read(400_000).decode("utf-8", "replace"))


def _sh(args, timeout=2):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def position(runner=None, system=None):
    """Playback position in seconds, or None."""
    run, system = runner or _sh, system or platform.system()
    try:
        if system == "Linux":
            out = run(["playerctl", "position"])
        elif system == "Darwin":
            out = None
            for app in ("Spotify", "Music"):
                out = run(["osascript", "-e", f'if application "{app}" is running then tell application "{app}" to return player position'])
                if out:
                    break
        else:
            return None
        return float((out or "").strip().replace(",", ".")) if out and out.strip() else None
    except ValueError:
        return None


class Lyrics:
    def __init__(self, fetch=None, runner=None, system=None):
        self.fetch, self.runner, self.system = fetch or _fetch, runner, system
        self._key, self._lines, self._plain = None, [], []

    def _load(self, artist, title):
        key = (artist.lower(), title.lower())
        if key == self._key:
            return
        self._key, self._lines, self._plain = key, [], []
        d = self.fetch("https://lrclib.net/api/get?" + urllib.parse.urlencode({"artist_name": artist, "track_name": title}))
        self._lines = parse_lrc(d.get("syncedLyrics") or "")
        if not self._lines:
            self._plain = [ln.strip() for ln in str(d.get("plainLyrics") or "").splitlines() if ln.strip()]

    def card(self, np, pos=None):
        """np: feeds.now_playing() dict. Raises ValueError with a readable reason when there is nothing to show."""
        if not np or not np.get("title"):
            raise ValueError("nothing is playing")
        try:
            self._load(np.get("artist", ""), np["title"])
        except Exception:                                             # noqa: BLE001  (404 = no lyrics known, network, ...)
            self._key, self._lines, self._plain = (np.get("artist", "").lower(), np["title"].lower()), [], []
            raise ValueError("no lyrics found for this song") from None
        if self._lines:
            pos = position(self.runner, self.system) if pos is None else pos
            if pos is None:
                cur, nxt = self._lines[0][1], self._lines[1][1] if len(self._lines) > 1 else ""
            else:
                cur, nxt = line_at(self._lines, pos)
        elif self._plain:
            cur, nxt = self._plain[0], self._plain[1] if len(self._plain) > 1 else ""
        else:
            raise ValueError("no lyrics found for this song")
        return {"k": "c", "label": "LYRICS", "t": ascii_fold(cur or "...", 40), "a": ascii_fold(nxt, 40), "b": ascii_fold(np["title"], 40)}
