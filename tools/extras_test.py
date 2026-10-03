"""Pure-logic tests for the extra Info cards (no network, no GUI).   python3 tools/extras_test.py"""
import datetime as dt
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from desk_lib import extras   # noqa: E402

v = extras.validate
assert v({"type": "countdown", "label": "Café Trip", "arg": "2026-12-24"}) == {"type": "countdown", "label": "Cafe Trip", "arg": "2026-12-24"}
assert v({"type": "worldclock", "arg": " Europe/Zurich , Asia/Tokyo "})["arg"] == "Europe/Zurich, Asia/Tokyo"
assert v({"type": "ci", "arg": "gitKinsey/ClaudeTest"})["arg"] == "gitKinsey/ClaudeTest" and v({"type": "crypto", "arg": "Ethereum:EUR"})["arg"] == "ethereum:eur"
for bad in ({"type": "x"}, "junk", {"type": "countdown", "arg": "24.12.2026"}, {"type": "worldclock", "arg": ""}, {"type": "worldclock", "arg": "a/b, c/d, e/f, g/h"},
            {"type": "worldclock", "arg": "Europe/Zurich; rm -rf"}, {"type": "git", "arg": " "}, {"type": "ci", "arg": "no-slash"}, {"type": "ci", "arg": "a/b/c"},
            {"type": "crypto", "arg": "bit coin"}, {"type": "crypto", "arg": "btc:toolong"}):
    try:
        v(bad); raise SystemExit(f"accepted {bad}")
    except ValueError:
        pass

# ---- countdown
today = dt.date(2026, 10, 3)
c = extras.countdown_card("Holiday", "2026-10-13", today)
assert c == {"k": "e", "label": "COUNTDOWN", "t": "10 days", "a": "Holiday", "b": "Tue 13 Oct 2026"}, c
assert extras.countdown_card("x", "2026-10-03", today)["t"] == "TODAY" and extras.countdown_card("x", "2026-10-04", today)["t"] == "1 day"
assert extras.countdown_card("x", "2026-10-01", today)["t"] == "2 d ago" and extras.countdown_card("", "2026-10-04", today)["a"] == "Event"

# ---- world clock (fixed-offset zones, so the test does not depend on a tz database)
zones = {"Europe/Zurich": dt.timezone(dt.timedelta(hours=2)), "Asia/Tokyo": dt.timezone(dt.timedelta(hours=9)), "America/New_York": dt.timezone(dt.timedelta(hours=-4))}
def zf(name):
    if name not in zones:
        raise KeyError(name)
    return zones[name]
now = dt.datetime(2026, 10, 3, 12, 30, tzinfo=dt.timezone.utc)
w = extras.worldclock_card("", "Europe/Zurich, Asia/Tokyo, America/New_York", now, zf)
assert w == {"k": "c", "label": "WORLD CLOCK", "t": "14:30 Zurich", "a": "21:30 Tokyo", "b": "08:30 New York"}, w
assert extras.worldclock_card("TRAVEL", "Asia/Tokyo", now, zf)["a"] == "" and extras.worldclock_card("T", "Asia/Tokyo", now, zf)["t"] == "21:30 Tokyo"
try:
    extras.worldclock_card("", "Mars/Base", now, zf); raise SystemExit("accepted an unknown zone")
except ValueError as e:
    assert "unknown time zone" in str(e)
real = extras.worldclock_card("", "UTC", now)          # the real zoneinfo path (UTC exists everywhere, even without tzdata on most systems)
assert real["t"].startswith("12:30"), real

# ---- git (injected runner)
def runner(out, rc=0, err=""):
    return lambda cmd: subprocess.CompletedProcess(cmd, rc, out, err)
g = extras.git_card("", "/repo", runner("## main...origin/main [ahead 2, behind 1]\n M a.py\n?? b.py\n"))
assert g == {"k": "c", "label": "GIT", "t": "main", "a": "2 changes", "b": "ahead 2, behind 1"}, g
assert extras.git_card("", "/r", runner("## feature/x...origin/feature/x\n"))["a"] == "clean" and extras.git_card("", "/r", runner("## feature/x...origin/feature/x\n"))["b"] == "in sync"
assert extras.git_card("", "/r", runner("## No commits yet on main\n M a\n"))["t"] == "main" and extras.git_card("", "/r", runner("## HEAD (no branch)\n"))["t"] == "HEAD (no branch)"
assert extras.git_card("", "/r", runner("## main\n M a\n"))["a"] == "1 change"
try:
    extras.git_card("", "/nope", runner("", 128, "fatal: not a git repository (or any of the parent directories): .git\n")); raise SystemExit("accepted a failing git")
except ValueError as e:
    assert "not a git repository" in str(e)
def no_git(cmd):
    raise FileNotFoundError
try:
    extras.git_card("", "/r", no_git); raise SystemExit("no git binary")
except ValueError as e:
    assert "not installed" in str(e)

# ---- CI + crypto (injected fetch)
runs = {"workflow_runs": [{"name": "CI", "head_branch": "main", "conclusion": "failure", "status": "completed", "updated_at": "2026-10-03T12:00:00Z"}]}
ci = extras.ci_card("", "o/r", lambda u: runs, now)
assert ci == {"k": "c", "label": "CI", "t": "FAILURE", "a": "CI main", "b": "30 min ago"}, ci
runs["workflow_runs"][0].update(conclusion=None, status="in_progress"); assert extras.ci_card("", "o/r", lambda u: runs, now)["t"] == "IN PROGRESS"
try:
    extras.ci_card("", "o/r", lambda u: {"workflow_runs": []}, now); raise SystemExit("no runs accepted")
except ValueError as e:
    assert "no workflow runs" in str(e)
urls = []
btc = extras.crypto_card("", "bitcoin", lambda u: (urls.append(u), {"bitcoin": {"usd": 67123.4, "usd_24h_change": -1.234}})[1])
assert btc == {"k": "c", "label": "BITCOIN", "t": "67,123 USD", "a": "-1.2% in 24 h", "b": ""} and "ids=bitcoin" in urls[0] and "vs_currencies=usd" in urls[0]
assert extras.crypto_card("", "ethereum:eur", lambda u: {"ethereum": {"eur": 2.5}})["t"] == "2.50 EUR" and extras.crypto_card("", "x", lambda u: {"x": {"usd": 0.01234}})["t"] == "0.0123 USD"
try:
    extras.crypto_card("", "nope", lambda u: {}); raise SystemExit("unknown coin accepted")
except ValueError as e:
    assert "no price" in str(e)

# ---- dispatcher
assert extras.build(v({"type": "countdown", "label": "x", "arg": "2026-10-04"}), today=today)["t"] == "1 day"
assert extras.build(v({"type": "git", "arg": "/r"}), run=runner("## main\n"))["t"] == "main"
for k in extras.KINDS:
    assert k in extras.TTL
print("ALL EXTRAS TESTS PASSED")
