"""Pure-logic tests for the time-based automation (no GUI).   python3 tools/scheduler_test.py"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from desk_lib import scheduler as sc   # noqa: E402

v = sc.validate
d = lambda days=None, t="09:00": {"kind": "daily", "time": t, **({"days": days} if days is not None else {})}   # noqa: E731
e1 = v({"name": "work", "when": d([0, 1, 2, 3, 4]), "do": {"kind": "layer", "n": 1}})
assert e1["when"] == {"kind": "daily", "time": "09:00", "days": [0, 1, 2, 3, 4]} and e1["enabled"] and len(e1["id"]) == 8
assert sc.describe(e1) == "weekdays at 09:00: switch to layer 2"
assert v({"when": d(None, "7:05"), "do": {"kind": "brightness", "n": 40}})["when"]["time"] == "07:05"
for bad in ({"when": d(None, "25:00"), "do": {"kind": "layer", "n": 0}}, {"when": d([], "09:00"), "do": {"kind": "layer", "n": 0}},
            {"when": d([9]), "do": {"kind": "layer", "n": 0}}, {"when": {"kind": "every", "minutes": 0}, "do": {"kind": "layer", "n": 0}},
            {"when": {"kind": "once", "at": "tomorrow"}, "do": {"kind": "layer", "n": 0}}, {"when": {"kind": "x"}, "do": {}},
            {"when": d(), "do": {"kind": "layer", "n": 3}}, {"when": d(), "do": {"kind": "mode", "n": 9}},
            {"when": d(), "do": {"kind": "brightness", "n": 2}}, {"when": d(), "do": {"kind": "led", "mode": "solid"}},
            {"when": d(), "do": {"kind": "host", "op": "rm", "arg": "x"}}, {"when": d(), "do": {"kind": "host", "op": "url"}},
            {"when": d(), "do": {"kind": "notify", "text": " "}}, {"when": d(), "do": {"kind": "nope"}}, "junk"):
    try:
        v(bad); raise SystemExit(f"accepted {bad}")
    except (ValueError, TypeError):
        pass
assert v({"when": d(), "do": {"kind": "led", "mode": "solid", "hex": "#FF8800"}})["do"] == {"kind": "led", "mode": "solid", "hex": "ff8800"}
assert v({"when": d(), "do": {"kind": "host", "op": "clipboard"}})["do"] == {"kind": "host", "op": "clipboard", "arg": ""}

# ---- daily: fires once, inside the grace window, only on its weekdays
sat = datetime(2026, 10, 3, 9, 0, 0)              # a Saturday
mon = datetime(2026, 10, 5, 9, 0, 0)
entries = [e1]
S = sc.Scheduler(lambda: entries)
assert S.tick(sat) == [] and S.tick(sat + timedelta(seconds=30)) == [], "not on weekend"
assert S.tick(mon - timedelta(seconds=1)) == []
assert S.tick(mon) == [e1] and S.tick(mon + timedelta(seconds=1)) == [] and S.tick(mon + timedelta(seconds=90)) == [], "once per day"
assert S.tick(mon + timedelta(days=1)) == [e1], "next day fires again"
late = sc.Scheduler(lambda: entries)
assert late.tick(mon + timedelta(minutes=30)) == [], "app started 30 min late: skipped, not run late"
e1["enabled"] = False
assert sc.Scheduler(lambda: entries).tick(mon) == [], "disabled rules never fire"
e1["enabled"] = True

# ---- every N minutes: countdown starts at first sight
ev = v({"when": {"kind": "every", "minutes": 30}, "do": {"kind": "led", "mode": "off"}})
S2 = sc.Scheduler(lambda: [ev]); t0 = datetime(2026, 10, 3, 12, 0, 0)
assert S2.tick(t0) == [] and S2.tick(t0 + timedelta(minutes=29)) == [] and S2.tick(t0 + timedelta(minutes=30)) == [ev]
assert S2.tick(t0 + timedelta(minutes=45)) == [] and S2.tick(t0 + timedelta(minutes=60)) == [ev]
assert S2.next_run(ev, t0 + timedelta(minutes=60)) == "13:30"

# ---- once: fires, retires itself; a stale one is retired without firing
at = datetime(2026, 10, 3, 15, 0)
eo = v({"when": {"kind": "once", "at": "2026-10-03 15:00"}, "do": {"kind": "notify", "text": "tea"}})
assert eo["when"]["at"] == "2026-10-03T15:00" and sc.describe(eo) == "once at 2026-10-03 15:00: remind: tea"
S3 = sc.Scheduler(lambda: [eo])
assert S3.tick(at - timedelta(seconds=5)) == [] and S3.tick(at) == [eo] and eo["enabled"] is False and eo.get("_retired")
assert S3.tick(at + timedelta(seconds=1)) == []
old = v({"when": {"kind": "once", "at": "2026-10-01 08:00"}, "do": {"kind": "notify", "text": "old"}})
assert sc.Scheduler(lambda: [old]).tick(at) == [] and old["enabled"] is False, "stale once rule retired silently"

# ---- next_run / describe
assert S.next_run(e1, sat) == "Mon 09:00" and S.next_run(e1, mon - timedelta(hours=1)) == "Mon 09:00"
assert sc.describe(v({"when": d([5, 6], "10:30"), "do": {"kind": "mode", "n": 2}})) == "weekends at 10:30: show screen 2"
assert sc.describe(v({"when": d([0, 2]), "do": {"kind": "layer", "n": "next"}})) == "Mon, Wed at 09:00: switch to the next layer"
print("ALL SCHEDULER TESTS PASSED")
