"""Firmware 2.0 native tests, batch 2: on-pad apps (snippets, radial launcher, typer, TOTP, PIN lock, sketch, calculator, timers, calendar), legend / icons,
confirm-on-pad and the macro progress ring.  Run through fw20_test.py."""
import base64
import datetime
import math
import time

from fw20_common import (a, appstate, clear, click, combo, expect, fresh, frame, hid, holddial, presses, px, region, remap, setmode, tap, turn)

M_SNIPS, M_RADIAL, M_TYPER, M_TOTP, M_SKETCH, M_CALC, M_TIMERS, M_CAL = 21, 22, 23, 24, 25, 26, 27, 28


def t_snippets(binary):
    e = fresh(binary, "snips")
    expect(e.request({"cmd": "snippets"})["n"] == 0, "starts empty")
    items = [{"l": "Email", "t": "me@example.com"}, {"l": "Sig", "t": "Best,\nMe"}, {"l": "Hi", "t": "hello"}]
    r = e.request({"cmd": "snippets", "op": "set", "items": items}); expect(r["ok"] and r["n"] == 3, f"set {r}")
    expect(e.request({"cmd": "snippets", "from": 0, "n": 20})["labels"] == ["Email", "Sig", "Hi"], "labels")
    for bad in ([{"l": "", "t": "x"}], [{"l": "x" * 15, "t": "x"}], [{"l": "ok", "t": "café"}], [{"l": "ok", "t": "x" * 121}], [{"l": "ok", "t": ""}], [{"l": "ok", "t": "x"}] * 13):
        expect(e.request({"cmd": "snippets", "op": "set", "items": bad}).get("err") == "items", f"rejected {str(bad)[:40]}")
    expect(e.request({"cmd": "snippets", "op": "set", "at": 99, "items": items[:2]}).get("err") == "items", "past the 100th")
    expect(e.request({"cmd": "snippets"})["n"] == 3, "bad requests changed nothing")
    setmode(e, M_SNIPS)
    s = appstate(e); expect(s["n"] == 3 and s["sel"] == 0 and s["label"] == "Email", f"state {s}")
    clear(e); click(e); time.sleep(0.6)
    expect(presses(e) == [ord(c) for c in "me@example.com"], f"click types the snippet: {presses(e)}")
    turn(e, 1); expect(appstate(e)["sel"] == 1, "dial scrolls")
    clear(e); tap(e, 3); time.sleep(0.7)
    expect(presses(e) == [ord(c) for c in "Best,\nMe"] + [10], f"K3 types it + Enter: {presses(e)}")
    turn(e, 5); expect(appstate(e)["sel"] == 0, "wraps around (1 + 5 mod 3 = 0)")
    tap(e, 2); expect(appstate(e)["sel"] == 1, "K2 +10 (1 + 10 mod 3)")
    tap(e, 1); expect(appstate(e)["sel"] == 0, "K1 -10")
    tap(e, 4); expect(appstate(e)["sel"] == 0, "K4 first")
    for at in range(0, 100, 10):                                       # 100 snippets in chunks
        r = e.request({"cmd": "snippets", "op": "set", "at": at, "total": 100 if at == 90 else at + 10, "items": [{"l": f"S{at + i}", "t": f"text {at + i}"} for i in range(10)]})
        expect(r["ok"], f"chunk {at}: {r}")
    expect(e.request({"cmd": "snippets"})["n"] == 100, "100 snippets")
    turn(e, -1); expect(appstate(e)["sel"] == 99 and appstate(e)["label"] == "S99", f"last one: {appstate(e)}")
    e.close(); e = fresh(binary, "snips", keep=True)
    expect(e.request({"cmd": "snippets", "from": 95, "n": 5})["labels"] == [f"S{i}" for i in range(95, 100)], "survive a power cycle")
    expect(e.request({"cmd": "snippets", "op": "clear"})["n"] == 0, "clear")
    setmode(e, M_SNIPS); clear(e); click(e); expect(presses(e) == [], "empty list types nothing")
    e.close()


def t_radial(binary):
    e = fresh(binary, "radial")
    sl = [{"l": "Copy", "a": combo("CTRL", "c")}, {}, {"l": "Paste", "a": combo("CTRL", "v")}, {"l": "Mute", "a": {"type": "media", "val": "MUTE"}}]
    r = e.request({"cmd": "radial", "slots": sl}); expect(r["ok"] and r["slots"][0]["l"] == "Copy" and r["slots"][1] == {} and r["slots"][2]["a"]["val"] == ["CTRL", "v"], f"set {r}")
    for bad in ([{"l": "", "a": combo("a")}], [{"l": "x", "a": combo("NOPE")}], [{"l": "toolongname", "a": combo("a")}], [{}] * 9):
        expect(not e.request({"cmd": "radial", "slots": bad}).get("ok"), f"rejected {str(bad)[:30]}")
    setmode(e, M_RADIAL)
    expect(appstate(e)["sel"] == 0 and appstate(e)["label"] == "Copy", "first slot")
    clear(e); click(e); time.sleep(0.3)
    expect(presses(e) == [128, a("c")], f"click runs the slot: {hid(e)}")
    turn(e, 1); expect(appstate(e)["used"] is False, "slot 2 is empty")
    clear(e); click(e); expect(hid(e) == [], "an empty slot does nothing")
    turn(e, 1); clear(e); click(e); time.sleep(0.3); expect(presses(e) == [128, a("v")], "slot 3 pastes")
    turn(e, -3); expect(appstate(e)["sel"] == 7, "wraps backwards")
    tap(e, 2); expect(appstate(e)["sel"] == 2, "K2 jumps to slot 3")
    clear(e); turn(e, 1); click(e); time.sleep(0.3); expect("cc press 226" in hid(e), f"media slot: {hid(e)}")
    e.close(); e = fresh(binary, "radial", keep=True)
    expect(e.request({"cmd": "radial"})["slots"][3]["l"] == "Mute", "survives a power cycle")
    e.close()


def t_icons_legend(binary):
    e = fresh(binary, "icons")
    bits = "ff" * 72
    expect(e.request({"cmd": "icon", "op": "set", "target": "key", "layer": 0, "key": 3, "bits": bits})["keys"][0] == [False, False, True, False, False], "icon stored")
    expect(e.request({"cmd": "icon", "op": "set", "target": "radial", "slot": 4, "bits": bits})["radial"][4], "radial icon")
    for bad in ({"bits": "ff"}, {"bits": "zz" * 72}, {"key": 6, "bits": bits}, {"layer": 3, "bits": bits}, {"target": "x", "bits": bits}):
        expect(not e.request(dict({"cmd": "icon", "op": "set", "target": "key", "layer": 0, "key": 1, "bits": bits}, **bad)).get("ok"), f"rejected {str(bad)[:30]}")
    setmode(e, 1)
    base = frame(e); r0 = region(base, 100, 160, 140, 195)
    expect(e.request({"cmd": "settings", "legend": 1})["legend"] == 1, "legend 1")
    time.sleep(0.3); f1 = frame(e); r1 = region(f1, 100, 160, 140, 195)
    expect(r1 > r0, f"names are drawn along the bottom: {r0} -> {r1}")
    e.request({"cmd": "settings", "legend": 2}); time.sleep(0.3)
    f2 = frame(e); r2 = region(f2, 108, 164, 132, 188)
    expect(r2 >= 570, f"the 24x24 icon of K3 is a filled block: {r2}")
    e.request({"cmd": "icon", "op": "clear", "target": "key", "layer": 0, "key": 3}); time.sleep(0.3)
    expect(region(frame(e), 108, 164, 132, 188) < 200, "cleared: the name is back")
    expect(not e.request({"cmd": "settings", "legend": 3}).get("ok"), "legend range")
    e.request({"cmd": "settings", "legend": 0}); time.sleep(0.3)
    expect(abs(region(frame(e), 100, 160, 140, 195) - r0) < 40, "off again: the same frame as before (up to the clock's moving hand)")
    # the popup menu uses the icons too (legend mode 2)
    remap(e, 5, {"type": "fx", "val": "popup"}); e.request({"cmd": "icon", "op": "set", "target": "key", "layer": 0, "key": 2, "bits": bits})
    e.request({"cmd": "settings", "legend": 2}); tap(e, 5); time.sleep(0.3)
    pf = frame(e); expect(region(pf, 195, 80, 219, 104) >= 500, f"popup segment 2 shows its icon: {region(pf, 195, 80, 219, 104)}")
    tap(e, 5) if False else click(e); time.sleep(0.3)
    e.close(); e = fresh(binary, "icons", keep=True)
    expect(e.request({"cmd": "icon"})["radial"][4], "radial icon survives")
    e.close()


def t_typer(binary):
    e = fresh(binary, "typer")
    setmode(e, M_TYPER)
    expect(appstate(e)["page"] == 0, "page 0 keyboard")
    turn(e, 1); click(e); turn(e, 1); click(e)                      # b, c
    expect(appstate(e)["buf"] == "bc", f"letters picked with the dial: {appstate(e)}")
    tap(e, 3); turn(e, -2); click(e)
    expect(appstate(e)["buf"] == "bc a", f"K3 adds a space: {appstate(e)['buf']!r}")
    for _ in range(4):
        tap(e, 1)
    expect(appstate(e)["buf"] == "", "K1 deletes")
    turn(e, 7); click(e); tap(e, 2)                                  # h, then shift on
    s = appstate(e); expect(s["shift"] and s["buf"] == "h", f"{s}")
    click(e); expect(appstate(e)["buf"] == "hH", f"shifted: {appstate(e)}")
    clear(e); tap(e, 4); time.sleep(0.4)
    expect(presses(e) == [ord("h"), ord("H")] and appstate(e)["buf"] == "", f"K4 types the text: {presses(e)}")
    tap(e, 5)
    expect(appstate(e)["page"] == 1, "K5 next page")
    turn(e, 3); click(e); turn(e, 4); click(e); turn(e, 2); click(e)   # 3 7 9
    expect(appstate(e)["buf"] == "379", f"digits: {appstate(e)}")
    clear(e); tap(e, 3); time.sleep(0.4); expect(presses(e) == [ord("3"), ord("7"), ord("9"), 10], f"K3 types + Enter: {presses(e)}")
    tap(e, 5)
    expect(appstate(e)["page"] == 2, "page 3 is morse")
    tap(e, 1); tap(e, 2)                                              # .- = a
    expect(appstate(e)["code"] == ".-", "dit dah")
    time.sleep(1.2); expect(appstate(e)["buf"] == "a" and appstate(e)["code"] == "", f"auto commit after a pause: {appstate(e)}")
    tap(e, 2); tap(e, 1); tap(e, 1); tap(e, 1); tap(e, 3)             # b
    tap(e, 1); tap(e, 1); tap(e, 1); tap(e, 3)                        # s
    s = appstate(e); expect(s["buf"] == "abs", f"morse decoded: {s}")
    for _ in range(7):
        tap(e, 1)
    expect(len(appstate(e)["code"]) <= 6, "code length limit")
    clear(e); tap(e, 4); time.sleep(0.5)
    expect(presses(e)[:3] == [ord("a"), ord("b"), ord("s")], f"K4 types the decoded text: {presses(e)}")
    e.close()


def t_totp(binary):
    e = fresh(binary, "totp")
    secret = base64.b32encode(b"12345678901234567890").decode()
    r = e.request({"cmd": "totp", "op": "set", "items": [{"n": "GitHub", "s": secret, "d": 8}, {"n": "Mail", "s": secret}]}); expect(r["ok"] and r["n"] == 2, f"set {r}")
    expect(secret not in str(r), "secrets are never sent back")
    vec = {59: "94287082", 1111111109: "07081804", 1111111111: "14050471", 1234567890: "89005924", 2000000000: "69279037", 20000000000: "65353130"}
    for t, code in vec.items():
        c = e.request({"cmd": "totp", "op": "code", "i": 0, "t": t}); expect(c["code"] == code, f"RFC 6238 {t}: {c.get('code')} != {code}")
    c = e.request({"cmd": "totp", "op": "code", "i": 1, "t": 59})
    expect(c["code"] == "287082" and c["rem"] == 1, f"6 digits are the last 6 of the 8; one second left: {c}")
    for bad in ([{"n": "x", "s": "not base32!"}], [{"n": "x", "s": "ABCD"}], [{"n": "", "s": secret}], [{"n": "x", "s": secret, "d": 9}], [{"n": "x", "s": secret, "p": 5}], [{"n": "x" * 11, "s": secret}]):
        expect(e.request({"cmd": "totp", "op": "set", "items": bad}).get("err") == "items", f"rejected {str(bad)[:40]}")
    expect(e.request({"cmd": "totp", "op": "code", "i": 5}).get("err") == "i", "no such account")
    e.request({"cmd": "time", "epoch": 1234567890, "tz": 0})        # second 0 of a 30 s period: stays in it for the whole test
    setmode(e, M_TOTP)
    s = appstate(e); expect(s["n"] == 2 and s["code"] == "89005924", f"{s}")
    clear(e); click(e); time.sleep(0.5)
    expect(presses(e) == [ord(c) for c in "89005924"], f"click types the code: {presses(e)}")
    tap(e, 1); expect(appstate(e)["sel"] == 1 and appstate(e)["code"] == "005924", "K1 next account (6 digits)")
    names = e.request({"cmd": "totp"})["names"]; expect([n["n"] for n in names] == ["GitHub", "Mail"], f"list {names}")
    e.close(); e = fresh(binary, "totp", keep=True)
    expect(e.request({"cmd": "totp", "op": "code", "i": 0, "t": 59})["code"] == "94287082", "accounts survive a power cycle")
    expect(e.request({"cmd": "totp", "op": "clear"})["n"] == 0, "clear")
    e.close()


def enter_pin(e, pin):
    cur = 0
    for ch in pin:
        d = int(ch); step = (d - cur) % 10
        if step:
            turn(e, step if step <= 5 else step - 10, 0.15)
        cur = d
        click(e)
    tap(e, 5)


def t_pin(binary):
    e = fresh(binary, "pin")
    secret = base64.b32encode(b"12345678901234567890").decode()
    e.request({"cmd": "totp", "op": "set", "items": [{"n": "A", "s": secret}]})
    for bad in ("12", "123456789", "12a4", ""):
        expect(e.request({"cmd": "pin", "op": "set", "pin": bad}).get("err") == "pin", f"bad pin {bad!r}")
    r = e.request({"cmd": "pin", "op": "set", "pin": "1234"}); expect(r["set"] and not r["locked"], f"set {r}")
    expect(e.request({"cmd": "pin", "op": "set", "pin": "9999", "old": "0000"}).get("err") == "old_pin", "changing needs the old PIN")
    expect(e.request({"cmd": "pin", "op": "lock"})["locked"], "lock")
    expect(e.request({"cmd": "totp", "op": "code", "i": 0, "t": 59}).get("err") == "locked", "no codes for the host while locked")
    expect(e.request({"cmd": "totp", "op": "clear"}).get("err") == "locked", "no clearing while locked")
    e.request({"cmd": "time", "epoch": 1234567890, "tz": 0}); setmode(e, M_TOTP)
    expect("code" not in appstate(e), "no code in the state while locked")
    clear(e); click(e); expect(presses(e) == [], "the locked screen does not type")
    enter_pin(e, "1111"); time.sleep(0.2)
    s = e.request({"cmd": "pin"}); expect(s["locked"] and s["fails"] == 1, f"wrong PIN: {s}")
    enter_pin(e, "1234"); time.sleep(0.2)
    expect(not e.request({"cmd": "pin"})["locked"], "right PIN unlocks")
    expect(appstate(e)["code"] == "005924", f"... and the code is there: {appstate(e)}")
    remap(e, 1, combo("q")); e.request({"cmd": "pin", "op": "lock"}); clear(e); setmode(e, 1); tap(e, 1); time.sleep(0.2)
    expect(presses(e) == [a("q")], "scope 'secrets': keys still run while locked")
    setmode(e, M_TOTP); enter_pin(e, "1234")
    expect(e.request({"cmd": "pin", "op": "config", "pin": "1234", "all": True})["all"], "whole-pad lock")
    e.request({"cmd": "pin", "op": "lock"}); clear(e); tap(e, 1); time.sleep(0.2)
    expect(presses(e) == [], "whole-pad lock: K1 does nothing")
    turn(e, 1); expect(presses(e) == [] and not any(x.startswith("cc") for x in hid(e)), "the dial does nothing either (it only picks a PIN digit)"); turn(e, -1)
    enter_pin(e, "1234"); time.sleep(0.2); setmode(e, 1)
    clear(e); tap(e, 1); time.sleep(0.2); expect(presses(e) == [a("q")], "unlocked again")
    e.request({"cmd": "pin", "op": "lock"})
    for _ in range(5):
        enter_pin(e, "0000")
    expect(e.request({"cmd": "pin"})["fails"] >= 5, "five wrong tries")
    enter_pin(e, "1234"); time.sleep(0.2)
    expect(e.request({"cmd": "pin"})["locked"], "locked out: even the right PIN is refused for 30 s")
    e.close(); e = fresh(binary, "pin", keep=True)
    expect(e.request({"cmd": "pin"})["locked"], "a PIN makes the pad start locked")
    setmode(e, M_TOTP); enter_pin(e, "1234")
    r = e.request({"cmd": "pin", "op": "config", "pin": "1234", "idle": 2, "all": False}); expect(r["idle"] == 2, "idle 2 s")
    expect(not e.request({"cmd": "pin"})["locked"], "unlocked")
    time.sleep(3.5); expect(e.request({"cmd": "pin"})["locked"], "auto-lock after the idle time")
    enter_pin(e, "1234"); time.sleep(0.2)
    e.request({"cmd": "pin", "op": "config", "pin": "1234", "idle": 0})
    e.control("#usb 1"); time.sleep(0.5); expect(e.request({"cmd": "pin"})["locked"], "locks when the PC suspends")
    e.control("#usb 0"); time.sleep(0.2); enter_pin(e, "1234")
    expect(e.request({"cmd": "pin", "op": "clear", "pin": "0000"}).get("err") == "old_pin", "clearing needs the PIN")
    expect(e.request({"cmd": "pin", "op": "clear", "pin": "1234"})["set"] is False, "cleared")
    e.close()


def t_confirm(binary):
    e = fresh(binary, "confirm")
    remap(e, 1, dict(combo("a"), confirm=True))
    remap(e, 2, combo("b"))
    g = e.request({"cmd": "getkeys", "layer": 0, "slot": 1}); expect(g["spec"].get("confirm") is True, f"stored with the flag {g}")
    clear(e); tap(e, 1); time.sleep(0.3)
    expect(presses(e) == [], "nothing runs before the confirmation")
    holddial(e, 1.3); time.sleep(0.3)
    expect(presses(e) == [a("a")], f"holding the dial for 1 s runs it: {presses(e)}")
    clear(e); tap(e, 1); tap(e, 2); time.sleep(0.3); expect(presses(e) == [], "any key cancels (and is not run itself)")
    clear(e); tap(e, 1); click(e); time.sleep(0.2); expect(presses(e) == [], "a dial click cancels")
    clear(e); tap(e, 1); holddial(e, 0.5); time.sleep(0.2); expect(presses(e) == [], "a short hold does not confirm")
    clear(e); tap(e, 2); time.sleep(0.2); expect(presses(e) == [a("b")], "ordinary keys are untouched")
    clear(e); tap(e, 1); time.sleep(6.5); holddial(e, 1.3)
    expect(presses(e) == [], "it times out after 6 s")
    r = e.request({"cmd": "remap_batch", "items": [{"key": 3, "type": "combo", "val": ["c"], "confirm": True}]}); expect(r["ok"], f"batch {r}")
    expect(e.request({"cmd": "getkeys", "layer": 0, "slot": 3})["spec"].get("confirm") is True, "the batch keeps the flag")
    e.close()


def t_macro_ring(binary):
    e = fresh(binary, "mring")
    remap(e, 1, {"type": "macro", "val": [{"combo": ["a"]}, {"delay": 1500}, {"combo": ["b"]}, {"delay": 1500}, {"combo": ["c"]}]})
    remap(e, 2, combo("x"))
    clear(e); tap(e, 1); time.sleep(0.4)
    f = frame(e); expect(region(f, 120, 2, 215, 40) > 15, "progress ring along the rim")
    tap(e, 2); time.sleep(2.5)
    expect(presses(e) == [a("a")], f"any key cancels a long macro (and is consumed): {presses(e)}")
    clear(e); tap(e, 2); time.sleep(0.3); expect(presses(e) == [a("x")], "back to normal")
    clear(e); remap(e, 3, {"type": "macro", "val": [{"combo": ["d"]}, {"combo": ["e"]}]}); tap(e, 3); time.sleep(0.4); tap(e, 2); time.sleep(0.3)
    expect(presses(e) == [a("d"), a("e"), a("x")], f"short macros are not cancelled: {presses(e)}")
    e.request({"cmd": "settings", "macro_ring": False}); clear(e); tap(e, 1); time.sleep(0.4); tap(e, 2); time.sleep(3.3)
    expect(presses(e) == [a("a"), a("b"), a("c"), a("x")], f"switched off: no cancel, the key queues behind the macro: {presses(e)}")
    e.close()


def t_sketch(binary):
    e = fresh(binary, "sketch")
    setmode(e, M_SKETCH)
    s = appstate(e); expect(s["x"] == 20 and s["y"] == 20 and not s["pen"] and s["count"] == 0, f"{s}")
    turn(e, 3); expect(appstate(e)["x"] == 23 and appstate(e)["count"] == 0, "moving without the pen draws nothing")
    tap(e, 1); expect(appstate(e)["pen"] and appstate(e)["count"] == 1, "K1 pen down draws the current cell")
    turn(e, 2); expect(appstate(e)["count"] == 3, "drawing while turning")
    click(e); expect(appstate(e)["vert"], "click switches to Y")
    turn(e, 2); s = appstate(e); expect(s["y"] == 22 and s["count"] == 5, f"vertical: {s}")
    tap(e, 2); turn(e, -1); expect(appstate(e)["count"] == 4, "K2 erase mode removes cells")
    tap(e, 4)
    r = e.request({"cmd": "sketch"}); expect(len(r["bits"]) == 400 and r["saved"], "saved")
    tap(e, 3); expect(appstate(e)["count"] == 0, "K3 clears")
    tap(e, 5); expect(appstate(e)["count"] == 4, "K5 loads the saved drawing")
    turn(e, -100); expect(appstate(e)["y"] == 0, "the cursor stops at the edge")
    r = e.request({"cmd": "sketch", "op": "set", "bits": "ff" * 200, "save": True}); expect(r["saved"], "set from the app")
    expect(appstate(e)["count"] == 1600, "all cells")
    expect(e.request({"cmd": "sketch", "op": "set", "bits": "zz"}).get("err") == "bits", "bits validated")
    time.sleep(0.3); f = frame(e); expect(region(f, 25, 25, 215, 215) > 30000, "the screen shows the drawing")
    e.close(); e = fresh(binary, "sketch", keep=True); setmode(e, M_SKETCH); tap(e, 5)
    expect(appstate(e)["count"] == 1600, "survives a power cycle")
    e.close()


CALC_TOK = ["7", "8", "9", "/", "4", "5", "6", "*", "1", "2", "3", "-", "0", ".", "=", "+", "C", "+/-", "<"]


def calc_press(e, tok):
    cur = CALC_TOK.index(appstate(e)["sel"]); want = CALC_TOK.index(tok); n = len(CALC_TOK)
    d = (want - cur) % n
    if d:
        turn(e, d if d <= n // 2 else d - n, 0.1)
    click(e)


def calc_type(e, text):
    i = 0
    while i < len(text):
        if text[i:i + 3] == "+/-":
            calc_press(e, "+/-"); i += 3
        else:
            calc_press(e, text[i]); i += 1


def t_calc(binary):
    e = fresh(binary, "calc")
    setmode(e, M_CALC)
    calc_type(e, "12+30="); expect(appstate(e)["entry"] == "42", f"12 + 30: {appstate(e)}")
    calc_type(e, "C7/0="); expect(appstate(e)["err"], "division by zero is an error")
    calc_type(e, "C"); expect(not appstate(e)["err"] and appstate(e)["entry"] == "0", "C clears")
    calc_type(e, "0.1+0.2="); expect(appstate(e)["entry"] == "0.3", f"0.1 + 0.2 = {appstate(e)['entry']}")
    calc_type(e, "C2+3*4="); expect(appstate(e)["entry"] == "20", f"immediate execution (2+3)*4 = {appstate(e)['entry']}")
    calc_type(e, "C5+/-"); expect(appstate(e)["entry"] == "-5", "sign")
    calc_type(e, "C123<"); expect(appstate(e)["entry"] == "12", "backspace")
    calc_type(e, "C9-4=*2="); expect(appstate(e)["entry"] == "10", "chained results")
    calc_type(e, "C1.5+1.5="); expect(appstate(e)["entry"] == "3", "decimals")
    calc_type(e, "C2*3"); tap(e, 3); expect(appstate(e)["entry"] == "6", "K3 is =")
    tap(e, 2); expect(appstate(e)["entry"] == "0", "K2 is C")
    calc_type(e, "45"); tap(e, 1); expect(appstate(e)["entry"] == "4", "K1 is backspace")
    tap(e, 4); expect(appstate(e)["page"] == 1, "K4 opens the converter")
    turn(e, 10); s = appstate(e); expect(s["unit"] == 10 and abs(s["conv"] - 6.21371192) < 1e-3, f"10 km = 6.2137 mi: {s}")
    tap(e, 2); expect(appstate(e)["rev"], "K2 swaps the direction")
    tap(e, 2)
    for _ in range(6):
        tap(e, 1)
    expect(appstate(e)["pair"] == 0, f"pairs cycle (6): {appstate(e)['pair']}")
    tap(e, 1); tap(e, 1); expect(appstate(e)["pair"] == 2, "pair C / F")
    click(e); turn(e, 25); turn(e, 25)
    s = appstate(e); expect(s["unit"] == 50 and abs(s["conv"] - 122) < 1e-6, f"50 C = 122 F: {s}")
    tap(e, 2); s = appstate(e); expect(abs(s["conv"] - (50 - 32) * 5 / 9) < 1e-6, f"50 F = 10 C: {s}")
    e.close()


def t_timers(binary):
    e = fresh(binary, "timers")
    setmode(e, M_TIMERS)
    s = appstate(e); expect(s["page"] == 0 and s["bpm"] == 120 and not s["run"], f"{s}")
    turn(e, 5); expect(appstate(e)["bpm"] == 125, "dial sets the BPM")
    turn(e, -5); turn(e, -200); expect(appstate(e)["bpm"] == 30, "clamped to 30")
    turn(e, 400); expect(appstate(e)["bpm"] == 240, "clamped to 240")
    turn(e, -120); expect(appstate(e)["bpm"] == 120, "back to 120")
    tap(e, 3); expect(appstate(e)["bpb"] == 5, "K3 beats per bar")
    for _ in range(3):
        tap(e, 3)
    expect(appstate(e)["bpb"] == 2, "wraps 7 -> 2")
    tap(e, 3); tap(e, 3); expect(appstate(e)["bpb"] == 4, "back to 4")
    with e.lock:
        e.leds.clear()
    tap(e, 1); time.sleep(3.05); s = appstate(e)
    with e.lock:
        flashes = [l for l in e.leds if any(l)]
    expect(5 <= s["beat"] <= 8 and s["run"], f"120 BPM = about 6 beats in 3 s: {s['beat']}")
    expect(len(flashes) >= 5, f"the LED flashes on every beat: {len(flashes)}")
    expect((0, 70, 70) in flashes, "accent colour on the first beat of the bar")
    tap(e, 1); expect(not appstate(e)["run"], "K1 stops")
    for _ in range(4):
        tap(e, 2, 0.05); time.sleep(0.27)
    b = appstate(e)["bpm"]; expect(125 <= b <= 150, f"tap tempo (taps about 0.44 s apart = 136 BPM): {b}")
    tap(e, 5); s = appstate(e); expect(s["page"] == 1 and s["iv"] == 0, "page 2: interval")
    turn(e, -3)
    expect(appstate(e)["work"] == 5, f"dial edits WORK: {appstate(e)['work']}")
    tap(e, 3); turn(e, -1); tap(e, 3); turn(e, -6)
    s = appstate(e); expect(s["work"] == 5 and s["rest"] == 5 and s["rounds"] == 2, f"work 5 rest 5 rounds 2: {s}")
    with e.lock:
        e.leds.clear()
    tap(e, 1); time.sleep(0.4); s = appstate(e); expect(s["iv"] == 1 and s["round"] == 1, f"work phase: {s}")
    tap(e, 2); time.sleep(0.2); expect(appstate(e)["paused"], "K2 pauses")
    time.sleep(5.5); expect(appstate(e)["iv"] == 1, "paused: no progress"); tap(e, 2)
    time.sleep(5.0); s = appstate(e); expect(s["iv"] == 2, f"rest phase: {s}")
    time.sleep(5.2); s = appstate(e); expect(s["iv"] == 1 and s["round"] == 2, f"round 2: {s}")
    time.sleep(5.3); s = appstate(e); expect(s["iv"] == 3, f"done after the last work phase: {s}")
    with e.lock:
        ls = list(e.leds)
    expect((0, 70, 0) in ls and (70, 0, 0) in ls, "green for work, red for rest")
    tap(e, 1); expect(appstate(e)["iv"] == 0, "K1 resets")
    tap(e, 5); expect(appstate(e)["page"] == 2, "page 3: decide")
    seen = set()
    for _ in range(60):
        e.request({"cmd": "input", "k": 1}); seen.add(appstate(e)["result"])
    expect(seen == {1, 2}, f"coin: both sides: {seen}")
    seen = set()
    for _ in range(400):
        e.request({"cmd": "input", "k": 2}); seen.add(appstate(e)["result"])
    expect(seen == set(range(1, 21)), f"d20 covers 1..20: {sorted(seen)}")
    e.request({"cmd": "input", "k": 3}); turn(e, 3, 0.05); expect(appstate(e)["spin"], "turning the dial spins the wheel")
    time.sleep(7); s = appstate(e); expect(not s["spin"] and 1 <= s["result"] <= s["segs"], f"it stops by itself on a segment: {s}")
    tap(e, 4); expect(appstate(e)["segs"] == 9, "K4 segments")
    e.close()


def usno_sun(y, m, d, lat, lon, rising):
    """independent reference: the US Naval Observatory 'Almanac for Computers' algorithm; returns UTC hours"""
    N1 = math.floor(275 * m / 9); N2 = math.floor((m + 9) / 12); N3 = 1 + math.floor((y - 4 * math.floor(y / 4) + 2) / 3)
    N = N1 - N2 * N3 + d - 30
    lh = lon / 15.0; t = N + ((6 - lh) / 24 if rising else (18 - lh) / 24)
    M = 0.9856 * t - 3.289
    L = (M + 1.916 * math.sin(math.radians(M)) + 0.020 * math.sin(math.radians(2 * M)) + 282.634) % 360
    RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
    RA += math.floor(L / 90) * 90 - math.floor(RA / 90) * 90
    RA /= 15
    sd = 0.39782 * math.sin(math.radians(L)); cd = math.cos(math.asin(sd))
    ch = (math.cos(math.radians(90.833)) - sd * math.sin(math.radians(lat))) / (cd * math.cos(math.radians(lat)))
    H = (360 - math.degrees(math.acos(ch))) if rising else math.degrees(math.acos(ch))
    H /= 15
    T = H + RA - 0.06571 * t - 6.622
    return (T - lh) % 24


def disc(raw, cx, cy):
    return all(px(raw, cx + dx, cy + dy) for dx, dy in ((8, 0), (-8, 0), (0, 8), (0, -8)))


def t_calendar(binary):
    e = fresh(binary, "cal")
    setmode(e, M_CAL)
    t0 = int(datetime.datetime(2027, 1, 15, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
    e.request({"cmd": "time", "epoch": t0, "tz": 0}); time.sleep(0.3)
    base = frame(e); expect(region(base, 40, 60, 200, 200) > 200, "the month grid")
    expect(disc(base, 142, 124), "today (Fri 15 Jan 2027) is marked")
    expect(not disc(base, 50, 124), "other days are not")
    expect(e.request({"cmd": "cal", "months": [{"y": 2027, "m": 1, "days": [3, 15]}]})["ok"], "event days")
    time.sleep(0.3); f = frame(e); expect(region(f, 185, 89, 192, 94) > 5, "a dot under the 3rd")
    for bad in ([{"y": 2027, "m": 13, "days": [1]}], [{"y": 2027, "m": 1, "days": [32]}], [{"y": 1999, "m": 1, "days": []}], [{"y": 2027, "m": 1, "days": [1]}] * 4):
        expect(not e.request({"cmd": "cal", "months": bad}).get("ok"), f"rejected {str(bad)[:40]}")
    turn(e, 1); expect(appstate(e)["off"] == 1, "dial = next month"); click(e); expect(appstate(e)["off"] == 0, "click = today")
    tap(e, 5); expect(appstate(e)["page"] == 1, "world clock")
    expect(e.request({"cmd": "worldclock", "zones": [{"n": "NYC", "o": -300}, {"n": "TYO", "o": 540}, {"n": "UTC", "o": 0}]})["ok"], "zones")
    time.sleep(0.2); z = {x["n"]: x["t"] for x in appstate(e)["zones"]}
    expect(z == {"NYC": "07:00", "TYO": "21:00", "UTC": "12:00"}, f"offsets applied: {z}")
    for bad in ([{"n": "", "o": 0}], [{"n": "LONGNAME", "o": 0}], [{"n": "X", "o": 9999}], [{"n": "X", "o": 0}] * 5):
        expect(not e.request({"cmd": "worldclock", "zones": bad}).get("ok"), f"rejected zones {str(bad)[:30]}")
    tap(e, 5); expect(appstate(e)["page"] == 2, "sun and moon")
    cases = [(47.37, 8.54, 2027, 6, 21), (51.51, -0.13, 2027, 12, 21), (-33.87, 151.21, 2027, 3, 20), (40.71, -74.0, 2027, 9, 1), (35.68, 139.69, 2027, 1, 15), (64.15, -21.94, 2027, 5, 1)]
    for lat, lon, y, m, d in cases:
        tt = int(datetime.datetime(y, m, d, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
        e.request({"cmd": "time", "epoch": tt, "tz": 0})
        r = e.request({"cmd": "geo", "lat": lat, "lon": lon}); expect(r["ok"] and r["sun"], f"geo {lat},{lon}: {r}")
        day0 = int(datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc).timestamp())
        rise = (r["rise"] - day0) / 3600 % 24; sset = (r["set"] - day0) / 3600 % 24
        for name, got, want in (("rise", rise, usno_sun(y, m, d, lat, lon, True)), ("set", sset, usno_sun(y, m, d, lat, lon, False))):
            diff = min(abs(got - want), 24 - abs(got - want)) * 60
            expect(diff < 3.5, f"{name} at {lat},{lon} on {y}-{m}-{d}: pad {got:.3f} h, reference {want:.3f} h ({diff:.1f} min)")
    zurich = int(datetime.datetime(2027, 6, 21, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
    e.request({"cmd": "time", "epoch": zurich, "tz": 0}); r = e.request({"cmd": "geo", "lat": 47.37, "lon": 8.54})
    rh = (r["rise"] % 86400) / 3600
    expect(abs(rh - 3.47) < 0.1, f"Zurich midsummer sunrise is about 03:28 UTC (05:28 CEST): {rh:.2f}")
    r = e.request({"cmd": "geo", "lat": 78.2, "lon": 15.6}); expect(r["sun"] is False, "midnight sun in Svalbard: no sunrise")
    expect(not e.request({"cmd": "geo", "lat": 95, "lon": 0}).get("ok") and not e.request({"cmd": "geo", "lat": 1}).get("ok"), "geo validated")
    for when, want in ((datetime.datetime(2024, 4, 8, 18, 21), 0.0), (datetime.datetime(2024, 4, 23, 23, 49), 14.77), (datetime.datetime(2024, 5, 8, 3, 22), 0.0)):
        tt = int(when.replace(tzinfo=datetime.timezone.utc).timestamp()); e.request({"cmd": "time", "epoch": tt, "tz": 0})
        age = e.request({"cmd": "geo", "lat": 10, "lon": 10})["moon_age"]
        d = min(abs(age - want), 29.53 - abs(age - want)); expect(d < 0.8, f"moon age on {when}: {age:.2f} (want about {want})")
    f = frame(e); expect(region(f, 20, 100, 220, 200) > 100, "the sun / moon page draws")
    e.close()
