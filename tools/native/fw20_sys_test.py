"""Firmware 2.0 native tests, batch 4: breadcrumbs, lifetime statistics, LED events, per-screen brightness / screensaver, USB-suspend dimming, ripples, pet reactions,
word clock and the other clock faces, screensavers, anti-aliased rings and text folding.  Run through fw20_test.py."""
import datetime
import os
import re
import time

from fw20_common import (a, appstate, clear, click, combo, expect, fresh, frame, hid, px, region, remap, setmode, tap, turn)


def epoch(y, m, d, hh=12, mm=0):
    return int(datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone.utc).timestamp())


# ------------------------------------------------------------------------------------------------ crash breadcrumbs
def t_crumbs(binary):
    e = fresh(binary, "crumbs")
    c = e.request({"cmd": "crumbs"}); expect(c["boots"] == 1 and c["now"][0]["s"].startswith("BOOT"), f"the boot is the first crumb: {c['now'][:2]}")
    tap(e, 3); turn(e, 2); turn(e, -1); click(e); setmode(e, 3); e.request({"cmd": "layer", "val": 1})
    remap(e, 1, combo("a"), "hold"); e.request({"cmd": "input", "k": 1, "g": "hold"})
    txt = [x["s"] for x in e.request({"cmd": "crumbs"})["now"]]
    for want in ("K3 L1", "DIAL+2", "DIAL-1", "DIALCLICK", "SCREEN 3", "LAYER 2", "K1 HOLD"):
        expect(want in txt, f"crumb {want!r} in {txt}")
    expect(any(x.startswith("CMD ") for x in txt), "commands are logged")
    expect(not any(x.startswith("CMD png") or x.startswith("CMD sta") for x in txt), "ping / stats are not")
    for _ in range(60):
        tap(e, 2, 0.02)
    c = e.request({"cmd": "crumbs"}); expect(len(c["now"]) == 48, f"a ring of 48: {len(c['now'])}")
    times = [x["ms"] for x in c["now"]]; expect(times == sorted(times), "oldest first")
    # a software reset keeps the ring: the new boot sees what happened before
    e.request({"cmd": "reboot"}); time.sleep(2.0); e.request({"cmd": "hello"})
    c = e.request({"cmd": "crumbs"})
    expect(c["boots"] == 2 and c["prev_boots"] == 1 and len(c["prev"]) == 48, f"previous boot kept: {c['boots']} {c['prev_boots']} {len(c['prev'])}")
    expect(c["prev"][-1]["s"].startswith("CMD reb"), f"the last thing before the reset was the reboot command: {c['prev'][-1]}")
    expect(c["now"][0]["s"].startswith("BOOT r3"), f"software reset reason (3): {c['now'][0]}")
    expect("CMD reb" in e.request({"cmd": "boot_log"})["crumbs"], "the boot log shows them")
    # a panic keeps them too
    tap(e, 4); e.request({"cmd": "events", "val": True})
    try:
        e.request({"cmd": "debug_crash"}, timeout=1.5)
    except Exception:                                                  # noqa: BLE001
        pass
    time.sleep(2.5); e.request({"cmd": "hello"})
    c = e.request({"cmd": "crumbs"})
    expect(c["now"][0]["s"].startswith("BOOT r4 c1"), f"panic reset reason (4), crash count 1: {c['now'][0]}")
    expect(any(x["s"].startswith("CMD deb") for x in c["prev"]) and any(x["s"].startswith("K4 L") for x in c["prev"]), f"the events before the crash survived: {[x['s'] for x in c['prev']][-6:]}")
    expect(e.request({"cmd": "crumbs", "op": "clear"})["prev"] == [], "clear")
    e.close()


# ------------------------------------------------------------------------------------------------ lifetime statistics
def t_lifetime(binary):
    e = fresh(binary, "life")
    z = e.request({"cmd": "lifetime", "op": "reset"}); expect(z["presses"] == [0] * 5 and z["dial_cw"] == 0 and z["boots"] == 1, f"reset {z}")
    for k, n in ((1, 3), (2, 1), (5, 2)):
        for _ in range(n):
            tap(e, k, 0.1)
    turn(e, 5); turn(e, -2); click(e); click(e)
    s = e.request({"cmd": "lifetime"}); expect(s["presses"] == [3, 1, 0, 0, 2] and s["dial_cw"] == 5 and s["dial_ccw"] == 2 and s["dial_click"] == 2, f"counted {s}")
    e.request({"cmd": "lifetime", "op": "save"}); e.close(); e = fresh(binary, "life", keep=True)
    s = e.request({"cmd": "lifetime"}); expect(s["presses"] == [3, 1, 0, 0, 2] and s["dial_cw"] == 5 and s["boots"] == 2, f"survives a power cycle, one more boot: {s}")
    expect(e.request({"cmd": "lifetime", "op": "x"}).get("err") == "op", "op validated")
    t0 = e.request({"cmd": "lifetime"})["minutes"]; time.sleep(61.5)
    expect(e.request({"cmd": "lifetime"})["minutes"] == t0 + 1, "the minutes of use count up")
    e.close()


# ------------------------------------------------------------------------------------------------ LED event language
def leds_since(e, secs):
    with e.lock:
        e.leds.clear()
    time.sleep(secs)
    with e.lock:
        return list(e.leds)


def t_led_events(binary):
    e = fresh(binary, "ledev")
    evs = [{"n": "build_fail", "p": 3, "rep": 2, "steps": [{"c": "ff0000", "ms": 200}, {"c": "000000", "ms": 200}]},
           {"n": "meeting", "p": 2, "rep": 1, "steps": [{"c": "0000ff", "ms": 400}]},
           {"n": "info", "p": 1, "rep": 1, "steps": [{"c": "00ff00", "ms": 300}]}]
    r = e.request({"cmd": "led_events", "events": evs}); expect(r["ok"] and [n["n"] for n in r["names"]] == ["build_fail", "meeting", "info"], f"set {r}")
    for bad in ([{"n": "", "p": 1, "steps": [{"c": "ff0000", "ms": 100}]}], [{"n": "x", "p": 0, "steps": [{"c": "ff0000", "ms": 100}]}], [{"n": "x", "p": 1, "steps": []}],
                [{"n": "x", "p": 1, "steps": [{"c": "zz0000", "ms": 100}]}], [{"n": "x", "p": 1, "steps": [{"c": "ff0000", "ms": 10}]}], [{"n": "x", "p": 1, "rep": 99, "steps": [{"c": "ff0000", "ms": 100}]}],
                [{"n": "x", "p": 1, "steps": [{"c": "ff0000", "ms": 100}] * 7}], [{"n": "x" * 11, "p": 1, "steps": [{"c": "ff0000", "ms": 100}]}], [evs[0]] * 9):
        expect(not e.request({"cmd": "led_events", "events": bad}).get("ok"), f"rejected {str(bad)[:50]}")
    expect(len(e.request({"cmd": "led_events"})["names"]) == 3, "bad definitions changed nothing")
    expect(e.request({"cmd": "led_event", "name": "nope"}).get("err") == "event", "unknown event")
    with e.lock:
        e.leds.clear()
    r = e.request({"cmd": "led_event", "name": "info"}); expect(r["active"] == "info", f"{r}")
    time.sleep(0.15); ls = list(e.leds); expect((0, 85, 0) in ls, f"green (255 / 3): {ls}")
    time.sleep(0.5); expect(e.request({"cmd": "led_event", "cancel": True})["active"] == "", "it ended by itself, cancel is harmless")
    # priorities: meeting pre-empts info; info waits behind meeting; build_fail pre-empts everything
    with e.lock:
        e.leds.clear()
    e.request({"cmd": "led_event", "name": "info"}); r = e.request({"cmd": "led_event", "name": "meeting"}); expect(r["active"] == "meeting" and r["queue"] == [], f"pre-empted: {r}")
    r = e.request({"cmd": "led_event", "name": "info"}); expect(r["active"] == "meeting" and r["queue"] == ["info"], f"lower priority waits: {r}")
    r = e.request({"cmd": "led_event", "name": "info"}); expect(r["queue"] == ["info"], "no duplicates in the queue")
    time.sleep(1.0); ls = list(e.leds); expect((0, 0, 85) in ls and (0, 85, 0) in ls and ls.index((0, 0, 85)) < ls.index((0, 85, 0)), f"blue first, then the waiting green: {ls}")
    r = e.request({"cmd": "led_event", "name": "meeting"}); ls = leds_since(e, 0.0)
    r = e.request({"cmd": "led_event", "name": "build_fail"}); expect(r["active"] == "build_fail" and r["queue"] == [], f"build_fail wins: {r}")
    ls = leds_since(e, 1.5)
    expect(ls.count((85, 0, 0)) == 2 and (0, 0, 0) in ls, f"red, off, red, off (2 repeats): {ls}")
    e.request({"cmd": "led_events", "events": []}); expect(e.request({"cmd": "led_events"})["names"] == [], "cleared")
    e.request({"cmd": "led_events", "events": evs}); e.close(); e = fresh(binary, "ledev", keep=True)
    expect(len(e.request({"cmd": "led_events"})["names"]) == 3, "events survive a power cycle")
    e.close()


# ------------------------------------------------------------------------------------------------ per-screen brightness / screensaver, USB suspend
def t_screen_settings(binary):
    e = fresh(binary, "scr")
    e.request({"cmd": "brightness", "val": 200}); time.sleep(0.2)
    base = e.request({"cmd": "settings"})["bl"]
    sb = [0] * 32; sb[0] = 50; sb[1] = 200
    r = e.request({"cmd": "settings", "scr_bright": sb}); expect(r["scr_bright"][:2] == [50, 200], f"set {r['scr_bright'][:3]}")
    setmode(e, 1); time.sleep(0.2); b1 = e.request({"cmd": "settings"})["bl"]
    setmode(e, 2); time.sleep(0.2); b2 = e.request({"cmd": "settings"})["bl"]
    setmode(e, 3); time.sleep(0.2); b3 = e.request({"cmd": "settings"})["bl"]
    expect(b1 == base // 2 or abs(b1 - base * 0.5) <= 2, f"clock at 50%: {b1} of {base}")
    expect(b2 >= base and b3 == base, f"screen 2 at 200% (capped), screen 3 unchanged: {b2} / {b3} of {base}")
    for bad in ([0] * 31, [1] + [0] * 31, [201] + [0] * 31, ["x"] + [0] * 31, "x"):
        expect(not e.request({"cmd": "settings", "scr_bright": bad}).get("ok"), f"rejected {str(bad)[:20]}")
    ss = [0] * 32; ss[0] = 2; ss[1] = -1
    e.request({"cmd": "settings", "scr_saver": ss, "saver_s": 3}); setmode(e, 1); time.sleep(3.5)
    expect(e.request({"cmd": "settings"})["saver_on"] is False, "screen 1 inherits... no: it has its own 2 s? (checked below)") if False else None
    st = e.request({"cmd": "settings"}); expect(st["saver_on"] is True, f"screen 1 starts its saver after 2 s (the global setting is 3 s): {st['saver_on']}")
    tap(e, 1); setmode(e, 2); time.sleep(3.8)
    expect(e.request({"cmd": "settings"})["saver_on"] is False, "screen 2 never starts one (-1) although the global setting is 3 s")
    setmode(e, 3); time.sleep(3.8); expect(e.request({"cmd": "settings"})["saver_on"] is True, "screen 3 inherits the global 3 s")
    for bad in ([-2] + [0] * 31, [3601] + [0] * 31):
        expect(not e.request({"cmd": "settings", "scr_saver": bad}).get("ok"), f"rejected {bad[:1]}")
    e.close(); e = fresh(binary, "scr", keep=True)
    st = e.request({"cmd": "settings"}); expect(st["scr_bright"][:2] == [50, 200] and st["scr_saver"][:2] == [2, -1], "survive a power cycle")
    # the PC sleeps: dark screen and LED
    e.request({"cmd": "settings", "scr_bright": [0] * 32, "saver_s": 0}); e.request({"cmd": "brightness", "val": 200}); setmode(e, 1); time.sleep(0.3)
    e.request({"cmd": "led", "mode": "auto"})
    e.control("#usb 1"); time.sleep(0.6)
    expect(e.request({"cmd": "settings"})["bl"] == 0, "suspended: backlight off")
    with e.lock:
        last_led = e.leds[-1] if e.leds else None
    expect(last_led == (0, 0, 0), f"suspended: LED off {last_led}")
    e.control("#usb 0"); time.sleep(0.6); expect(e.request({"cmd": "settings"})["bl"] > 100, "resumed: back")
    expect(e.request({"cmd": "settings", "suspend_dim": False})["suspend_dim"] is False, "switch it off")
    e.control("#usb 1"); time.sleep(0.5); expect(e.request({"cmd": "settings"})["bl"] > 100, "switched off: the pad stays lit")
    e.control("#usb 0")
    e.close()


# ------------------------------------------------------------------------------------------------ ripples, pet
def t_ripples(binary):
    e = fresh(binary, "ripples")
    setmode(e, 25); time.sleep(0.2)
    base = region(frame(e), 40, 150, 200, 235)
    e.control("#key 3 1"); time.sleep(0.12); f_off = frame(e); e.control("#key 3 0"); time.sleep(1.2)      # (the normal key flash ring is part of this picture)
    expect(e.request({"cmd": "settings", "ripples": True})["ripples"] is True, "on")
    e.control("#key 3 1"); time.sleep(0.12); f_on = frame(e); e.control("#key 3 0")
    off, on = region(f_off, 40, 150, 200, 235), region(f_on, 40, 150, 200, 235)
    expect(on > off + 20, f"a ring is drawn around K3's spot: {off} -> {on}")
    time.sleep(1.2); expect(region(frame(e), 40, 150, 200, 235) == base, "and it is gone again after 0.8 s")
    e.close()


def t_pet(binary):
    e = fresh(binary, "pet")
    expect(e.request({"cmd": "pet_event", "kind": "x"}).get("err") == "kind", "kind validated")
    r = e.request({"cmd": "pet_event", "kind": "cheer"}); expect(r["cheer"] and not r["sad"], f"cheer {r}")
    time.sleep(4.3); expect(e.request({"cmd": "pet_event"})["cheer"] is False, "ends after 4 s")
    f0 = e.request({"cmd": "pet_event"})["fun"]
    r = e.request({"cmd": "pet_event", "kind": "sad"}); expect(r["sad"] and r["fun"] == max(0, f0 - 10), f"sad lowers the mood: {r}")
    e.send({"cmd": "stats", "cpu": 95, "ram": 40}); time.sleep(0.3); expect(e.request({"cmd": "pet_event"})["sweat"], "sweats at 95% CPU")
    e.send({"cmd": "stats", "cpu": 20, "ram": 40}); time.sleep(0.3); expect(e.request({"cmd": "pet_event"})["sweat"] is False, "cool at 20%")
    setmode(e, 17); e.request({"cmd": "pet_event", "kind": "cheer"}); time.sleep(0.5)
    f = frame(e); expect(region(f, 80, 20, 160, 60) > 30, "the cheering pet draws its message and stars")
    # night: the pet falls asleep when nothing happens for a while
    e.request({"cmd": "time", "epoch": epoch(2027, 1, 15, 12, 0), "tz": 0}); time.sleep(12.5)
    expect(e.request({"cmd": "pet_event"})["sleep"] is False, "awake at noon")
    e.request({"cmd": "time", "epoch": epoch(2027, 1, 15, 2, 0), "tz": 0}); time.sleep(12.5)
    expect(e.request({"cmd": "pet_event"})["sleep"] is True, "asleep at 02:00")
    e.close()


# ------------------------------------------------------------------------------------------------ word clock, clock faces, savers, AA, folding
def ref_words(h, m):
    hours = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE"]
    m5 = (m + 2) // 5 * 5
    if m5 >= 60:
        m5 = 0; h += 1
    to = m5 > 30; hh = h + 1 if to else h; hour = hours[(hh + 11) % 12]
    mins = {0: "", 5: "FIVE", 10: "TEN", 15: "A QUARTER", 20: "TWENTY", 25: "TWENTY FIVE", 30: "HALF", 35: "TWENTY FIVE", 40: "TWENTY", 45: "A QUARTER", 50: "TEN", 55: "FIVE"}[m5]
    if m5 == 0:
        return f"IT IS {hour} OCLOCK"
    return f"IT IS {mins} {'TO' if to else 'PAST'} {hour}"


def t_wordclock(binary):
    e = fresh(binary, "wordclock")
    for h, m, want in ((10, 30, "IT IS HALF PAST TEN"), (10, 0, "IT IS TEN OCLOCK"), (10, 45, "IT IS A QUARTER TO ELEVEN"), (23, 58, "IT IS TWELVE OCLOCK"), (12, 50, "IT IS TEN TO ONE"), (0, 7, "IT IS FIVE PAST TWELVE")):
        r = e.request({"cmd": "clockwords", "h": h, "m": m}); expect(r["text"] == want, f"{h}:{m:02d} -> {r['text']!r}, want {want!r}")
    bad = 0
    for h in range(24):
        for m in range(60):
            if bad < 3 and e.request({"cmd": "clockwords", "h": h, "m": m})["text"] != ref_words(h, m):
                bad += 1; expect(False, f"{h}:{m:02d}: {e.request({'cmd': 'clockwords', 'h': h, 'm': m})['text']!r} != {ref_words(h, m)!r}")
    expect(bad == 0, "all 1440 minutes match the reference sentence")
    expect(not e.request({"cmd": "clockwords", "h": 24, "m": 0}).get("ok"), "range")
    # the letter grid in the source: every word is spelled out at the position the table gives
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "DeskCompanion", "apps_sys.h")).read()
    grid = re.search(r"WC_GRID\[10\] = \{([^}]*)\}", src).group(1); rows = re.findall(r'"([A-Z]+)"', grid)
    expect(len(rows) == 10 and all(len(r) == 11 for r in rows), f"11 x 10 letters: {[len(r) for r in rows]}")
    words = re.findall(r'\{"([A-Z]+)", (\d+), (\d+), (\d+)\}', src[src.index("struct WcWord"):src.index("static int wcWords")])
    expect(len(words) >= 22, f"{len(words)} words in the table")
    for w, r, c, n in words:
        r, c, n = int(r), int(c), int(n)
        expect(rows[r][c:c + n] == w.replace("O'", "O"), f"{w} sits at row {r} col {c}: grid has {rows[r][c:c + n]!r}")
    # the screen lights exactly those rows
    e.request({"cmd": "time", "epoch": epoch(2027, 1, 15, 10, 30), "tz": 0}); e.request({"cmd": "settings", "clock_style": 8}); setmode(e, 1); time.sleep(0.4)
    f = frame(e); acc = 0x069F
    lit_rows = [r for r in range(10) if sum(1 for y in range(48 + r * 16 - 6, 48 + r * 16 + 6) for x in range(30, 215) if px(f, x, y) == acc) > 10]
    expect(lit_rows == [0, 3, 4, 9], f"10:30 lights the rows of IT IS / HALF / PAST / TEN: {lit_rows}")
    e.close()


def t_clock_faces(binary):
    e = fresh(binary, "faces")
    e.request({"cmd": "time", "epoch": epoch(2027, 1, 15, 14, 35), "tz": 0}); setmode(e, 1)
    shots = {}
    for st in (0, 6, 7, 8):
        expect(e.request({"cmd": "settings", "clock_style": st})["clock_style"] == st, f"style {st}"); time.sleep(0.4); shots[st] = frame(e)
        expect(region(shots[st], 0, 0, 240, 240) > 300, f"style {st} draws")
    expect(len({bytes(v) for v in shots.values()}) == 4, "four different faces")
    f = shots[7]; orange = sum(1 for y in range(70, 160) for x in range(24, 220) if (px(f, x, y) >> 11) >= 25 and ((px(f, x, y) >> 5) & 63) in range(30, 50))
    expect(orange > 100, f"nixie digits glow orange: {orange}")
    expect(not e.request({"cmd": "settings", "clock_style": 9}).get("ok"), "style range")
    e.close()


def t_savers(binary):
    e = fresh(binary, "savers")
    setmode(e, 1); e.request({"cmd": "sketch", "op": "set", "bits": "ff" * 200, "save": True})
    for st in range(8, 14):
        expect(e.request({"cmd": "settings", "saver_style": st, "saver_s": 1})["saver_style"] == st, f"style {st}")
        time.sleep(1.6); expect(e.request({"cmd": "settings"})["saver_on"], f"saver {st} is on")
        f1 = frame(e); time.sleep(0.7); f2 = frame(e)
        expect(region(f1, 0, 0, 240, 240) > 200, f"saver {st} draws")
        if st != 8:
            diff = sum(1 for i in range(0, len(f1), 2) if f1[i:i + 2] != f2[i:i + 2]); expect(diff > 300, f"saver {st} is animated: {diff} pixels changed")
        tap(e, 1); time.sleep(0.3)
    expect(not e.request({"cmd": "settings", "saver_style": 14}).get("ok"), "style range")
    e.close()


def t_aa(binary):
    e = fresh(binary, "aa")
    setmode(e, 2); time.sleep(0.3)
    off = frame(e); colors_off = {px(off, x, y) for y in range(0, 30) for x in range(60, 180)}
    expect(e.request({"cmd": "settings", "aa": True})["aa"] is True, "on")
    time.sleep(0.4); on = frame(e); colors_on = {px(on, x, y) for y in range(0, 30) for x in range(60, 180)}
    expect(len(colors_on) > len(colors_off) + 5, f"smooth edges use in-between colours: {len(colors_off)} -> {len(colors_on)}")
    ring_off = region(off, 0, 0, 240, 240); ring_on = region(on, 0, 0, 240, 240)
    expect(abs(ring_on - ring_off) < ring_off * 0.25, f"same shapes: {ring_off} vs {ring_on} pixels")
    e.request({"cmd": "settings", "aa": False}); time.sleep(0.4)
    expect(len({px(frame(e), x, y) for y in range(0, 30) for x in range(60, 180)}) == len(colors_off), "off again")
    e.close()


def t_fold(binary):
    e = fresh(binary, "fold")
    cases = {"Müller": "Mueller", "Ærø": "AEro", "straße": "strasse", "Привет": "Privet", "ΑΘΗΝΑ": "AThENA", "€5": "EUR5", "café – ok": "cafe - ok",
             "‘hi’": "'hi'", "\U0001F600": "?", "plain": "plain", "Łódź": "Lodz", "жук": "zhuk"}
    for src, want in cases.items():
        r = e.request({"cmd": "fold", "text": src}); expect(r["text"] == want, f"{src!r} -> {r['text']!r}, want {want!r}")
    r = e.request({"cmd": "toast", "text": "Müller", "kind": "ok", "secs": 2}); expect(r.get("err") == "bad_arg", f"refused as before: {r}")
    expect(e.request({"cmd": "settings", "fold_text": True})["fold_text"] is True, "switch on")
    r = e.request({"cmd": "toast", "text": "Müller", "kind": "ok", "secs": 2}); expect(r["ok"], f"accepted and folded: {r}")
    expect(e.request({"cmd": "ctx", "text": "Привет"})["ok"], "ctx")
    expect(e.request({"cmd": "layer_names", "names": ["Ärger", "", "Старт"]})["ok"], "layer names")
    names = e.request({"cmd": "layer_names"})["names"]; expect(names == ["Aerger", "", "Start"], f"folded names: {names}")
    r = e.request({"cmd": "labels", "layer": 0, "labels": ["Küss", "", "", "", "", "", ""]}); expect(r.get("ok"), f"labels: {r}")
    expect(not e.request({"cmd": "toast", "text": "x" * 25}).get("ok"), "the length limit applies after folding")
    expect(e.request({"cmd": "toast", "text": "Ü" * 13}).get("err") == "too_long", "folded text that gets too long is refused (Ü -> Ue)")
    e.request({"cmd": "settings", "fold_text": False})
    e.close()
