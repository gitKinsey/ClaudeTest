"""Update check: ask GitHub whether a newer release of the app exists. Never downloads or installs anything by itself.
`fetch(url) -> (status, text)` is injectable so the logic is testable without a network."""
import json
import re
import urllib.error
import urllib.request

REPO = "gitKinsey/ClaudeTest"


def parse_version(v):
    """'v1.4.0' / '1.4' / '1.5.0-beta' -> (1, 4, 0). Anything without digits -> ()."""
    m = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(v or ""))
    return tuple(int(x or 0) for x in m.groups()) if m else ()


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and bool(b) and a > b


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "DeskCompanion", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:                     # noqa: S310 - https GitHub API
            return r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def check(current, repo=REPO, fetch=None):
    """-> {"current", "latest", "newer": bool, "url", "notes", "message"}. Raises ValueError with a readable reason."""
    fetch = fetch or _fetch
    try:
        status, text = fetch(f"https://api.github.com/repos/{repo}/releases/latest")
    except (OSError, ValueError) as e:
        raise ValueError(f"could not reach GitHub: {e}") from None
    if status == 404:
        return {"current": current, "latest": "", "newer": False, "url": f"https://github.com/{repo}/releases", "notes": "",
                "message": "no release has been published yet - you have the newest there is"}
    if status != 200:
        raise ValueError(f"GitHub answered {status}")
    try:
        d = json.loads(text)
    except ValueError:
        raise ValueError("GitHub sent something unreadable") from None
    tag = str(d.get("tag_name") or d.get("name") or "")
    newer = is_newer(tag, current)
    return {"current": current, "latest": tag, "newer": newer, "url": str(d.get("html_url") or f"https://github.com/{repo}/releases"),
            "notes": str(d.get("body") or "")[:600],
            "message": f"version {tag} is available (you have {current})" if newer else f"up to date ({current})"}
