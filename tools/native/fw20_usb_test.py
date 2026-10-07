"""Firmware 2.0 native tests, batch 5: USB MIDI controller, game controller and the read-only drive.  The native build uses stand-ins for the core's USBMIDI /
USBHIDGamepad / USBMSC classes that record what the firmware sends; the real USB enumeration is not tested here.  Run through fw20_test.py."""
import base64
import json
import shutil
import struct
import subprocess
import tempfile
import time
import os

from fw20_common import clear, click, combo, expect, fresh, hid, presses, remap, tap, turn


def reboot(e):
    e.request({"cmd": "reboot"}); time.sleep(2.2); e.request({"cmd": "hello"}); e.request({"cmd": "events", "val": True})


def midi_clear(e):
    with e.lock:
        e.midi.clear(); e.pad.clear()


def t_midi(binary):
    e = fresh(binary, "midi")
    h = e.request({"cmd": "hello"}); expect("midi" in h["caps"] and "gamepad" in h["caps"] and "usbdrive" in h["caps"], f"caps {h['caps'][-8:]}")
    u = e.request({"cmd": "usbmode"}); expect(not u["midi_on"] and u["available"] == ["midi", "gamepad", "drive"], f"{u}")
    expect(e.request({"cmd": "usbmode", "midi_on": True}).get("err") == "not_active", "cannot be switched on before the class is registered")
    u = e.request({"cmd": "usbmode", "midi": True}); expect(u["midi"] and u["reboot_needed"] and u["active"] == 0, f"registered at the next boot: {u}")
    reboot(e)
    u = e.request({"cmd": "usbmode"}); expect(u["active"] == 1 and not u["reboot_needed"], f"after the reboot: {u}")
    expect(e.request({"cmd": "usbmode", "midi_on": True})["midi_on"], "on")
    remap(e, 1, combo("q"))
    midi_clear(e); clear(e); e.control("#key 1 1"); time.sleep(0.12); e.control("#key 1 0"); time.sleep(0.2)
    expect(e.midi == [["on", "36", "100", "1"], ["off", "36", "0", "1"]], f"K1 = note 36: {e.midi}")
    expect(presses(e) == [], "the key's normal action is not run")
    midi_clear(e); e.control("#key 5 1"); time.sleep(0.1); e.control("#key 5 0"); time.sleep(0.2); expect(e.midi[0] == ["on", "40", "100", "1"], f"K5 = note 40: {e.midi}")
    e.request({"cmd": "layer", "val": 2}); midi_clear(e); e.control("#key 2 1"); time.sleep(0.1); e.control("#key 2 0"); time.sleep(0.2)
    expect(e.midi[0] == ["on", "47", "100", "1"], f"layer 3 = bank +10: {e.midi}"); e.request({"cmd": "layer", "val": 0})
    r = e.request({"cmd": "usbmode", "channel": 3, "base": 60, "velocity": 90, "cc": 10}); expect(r["channel"] == 3 and r["base"] == 60, f"{r}")
    midi_clear(e); e.control("#key 1 1"); time.sleep(0.1); e.control("#key 1 0"); time.sleep(0.2); expect(e.midi[0] == ["on", "60", "90", "3"], f"new base / channel / velocity: {e.midi}")
    midi_clear(e); turn(e, 3); expect(e.midi and e.midi[-1] == ["cc", "10", "67", "3"], f"the dial is a CC (starts at 64, +3): {e.midi}")
    midi_clear(e); turn(e, 100); expect(e.midi[-1][:3] == ["cc", "10", "127"], f"clamped at 127: {e.midi[-1]}")
    midi_clear(e); turn(e, -200, 0.6); expect(e.midi[-1][:3] == ["cc", "10", "0"], f"... and at 0: {e.midi[-1]}")
    midi_clear(e); click(e); click(e); expect([m[:3] for m in e.midi] == [["cc", "20", "127"], ["cc", "20", "0"]], f"the dial button toggles CC base+10: {e.midi}")
    e.request({"cmd": "layer", "val": 1}); midi_clear(e); turn(e, 1); expect(e.midi[0][:3] == ["cc", "11", "65"], f"each layer has its own CC and value: {e.midi}")
    for bad in ({"channel": 0}, {"channel": 17}, {"base": 121}, {"cc": 116}, {"velocity": 0}, {"velocity": 128}, {"midi": "yes"}):
        expect(not e.request(dict({"cmd": "usbmode"}, **bad)).get("ok"), f"rejected {bad}")
    expect(e.request({"cmd": "usbmode", "pad_on": True}).get("err") == "not_active", "game mode needs its class too")
    reboot(e); expect(e.request({"cmd": "usbmode"})["midi_on"], "MIDI mode survives a reboot")
    e.request({"cmd": "usbmode", "midi_on": False}); clear(e); tap(e, 1); time.sleep(0.2); expect(presses(e) == [ord("q")], "off: the key works as before")
    u = e.request({"cmd": "usbmode", "midi": False}); expect(u["reboot_needed"], "unregistering also needs a reboot")
    e.close()


def t_gamepad(binary):
    e = fresh(binary, "pad")
    e.request({"cmd": "usbmode", "gamepad": True, "midi": True}); reboot(e)
    u = e.request({"cmd": "usbmode"}); expect(u["active"] == 3, f"both classes: {u}")
    expect(e.request({"cmd": "usbmode", "pad_on": True})["pad_on"], "game mode on")
    expect(e.request({"cmd": "usbmode", "midi_on": True}).get("err") == "exclusive", "MIDI and game mode exclude each other")
    midi_clear(e); e.control("#key 1 1"); time.sleep(0.12); e.control("#key 3 1"); time.sleep(0.12); e.control("#key 1 0"); time.sleep(0.12); e.control("#key 3 0"); time.sleep(0.2)
    expect([p[2] for p in e.pad] == ["1", "5", "4", "0"], f"buttons as a bit mask (K1 = 1, K3 = 4): {e.pad}")
    e.request({"cmd": "layer", "val": 1}); midi_clear(e); e.control("#key 1 1"); time.sleep(0.1); e.control("#key 1 0"); time.sleep(0.2)
    expect(e.pad[0][2] == "32", f"layer 2 buttons 6-10: {e.pad}"); e.request({"cmd": "layer", "val": 0})
    midi_clear(e); turn(e, 2); expect(e.pad and e.pad[-1][0] == "16", f"the dial is an axis (8 a click): {e.pad}")
    turn(e, 100); expect(e.pad[-1][0] == "127", "clamped at +127"); turn(e, -300, 0.8); expect(e.pad[-1][0] == "-127", f"... and -127: {e.pad[-1]}")
    r = e.request({"cmd": "usbmode", "axis_step": 20}); expect(r["axis_step"] == 20, "step");
    click(e); midi_clear(e); turn(e, 1); expect(e.pad[-1][0] == "20", f"after a recentre the step is 20: {e.pad}")
    midi_clear(e); click(e); expect([p[:3] for p in e.pad] == [["0", "0", "32768"], ["0", "0", "0"]], f"the dial button recentres and taps button 16: {e.pad}")
    expect(not e.request({"cmd": "usbmode", "axis_step": 0}).get("ok") and not e.request({"cmd": "usbmode", "axis_step": 65}).get("ok"), "axis step range")
    e.request({"cmd": "usbmode", "pad_on": False, "midi_on": True}); midi_clear(e); e.control("#key 1 1"); time.sleep(0.1); e.control("#key 1 0"); time.sleep(0.2)
    expect(e.pad == [] and e.midi, "switching to MIDI stops the game mode")
    e.close()


def fat12_files(img):
    """minimal FAT12 reader: returns (label, {name: bytes}) and checks the structure"""
    bps, spc, rsv, nfat, rootn, total, media, fatsz = struct.unpack_from("<HBHBHHBH", img, 11)
    assert (bps, spc, nfat, total, media) == (512, 1, 2, 256, 0xF8) and img[510:512] == b"\x55\xAA", "boot sector"
    fat1 = img[rsv * bps:(rsv + fatsz) * bps]; fat2 = img[(rsv + fatsz) * bps:(rsv + 2 * fatsz) * bps]
    assert fat1 == fat2, "both FATs are the same"

    def fat(n):
        o = n * 3 // 2
        v = fat1[o] | fat1[o + 1] << 8
        return v >> 4 if n & 1 else v & 0xFFF
    root = img[(rsv + nfat * fatsz) * bps:(rsv + nfat * fatsz) * bps + rootn * 32]
    data0 = rsv + nfat * fatsz + rootn * 32 // bps
    files, label, used = {}, None, set()
    for i in range(rootn):
        e = root[i * 32:(i + 1) * 32]
        if e[0] == 0:
            break
        if e[11] == 0x08:
            label = e[:11].decode().strip(); continue
        name = e[:8].decode().strip() + "." + e[8:11].decode().strip(); cl, size = struct.unpack_from("<HI", e, 26)
        out = b""; n = cl
        while size and n < 0xFF8:
            assert n not in used, "no cluster used twice"; used.add(n)
            out += img[(data0 + n - 2) * bps:(data0 + n - 1) * bps]; n = fat(n)
        files[name] = out[:size]
        assert n >= 0xFF8 or not size, "chain ends properly"
    return label, files


def t_drive(binary):
    e = fresh(binary, "drive")
    expect(e.request({"cmd": "usbdrive", "op": "refresh"}).get("err") == "not_active", "nothing to refresh before the drive is enabled")
    remap(e, 1, combo("q")); remap(e, 2, combo("w"), "hold"); e.request({"cmd": "settings", "clock_style": 6})
    e.request({"cmd": "usbmode", "drive": True}); reboot(e)
    s = e.request({"cmd": "usbdrive"}); expect(s["active"] and s["size"] == 131072 and s["readonly"], f"{s}")
    img = b""
    for off in range(0, 131072, 1500):
        r = e.request({"cmd": "usbdrive", "op": "image", "off": off, "n": 1500}); img += base64.b64decode(r["d"])
    expect(len(img) == 131072, f"the whole image: {len(img)}")
    label, files = fat12_files(img)
    expect(label == "DESKCOMPAN" and sorted(files) == ["KEYS.JSN", "LIFETIME.JSN", "README.TXT", "SETTINGS.JSN"], f"label {label!r} files {sorted(files)}")
    expect(files["README.TXT"].startswith(b"DeskCompanion backup drive"), "readme")
    st = json.loads(files["SETTINGS.JSN"]); expect(st["fw"] == "1.5.0" and st["clock_style"] == 6 and st["proto"] == 20 and st["usb_extra"] == 4, f"settings {st}")
    kj = json.loads(files["KEYS.JSN"]); expect(kj["layers"][0]["slots"]["1"] == {"type": "combo", "val": ["q"]} and kj["layers"][0]["gestures"]["2h"]["val"] == ["w"], f"keys {json.dumps(kj)[:200]}")
    expect(len(kj["layers"]) == 3 and "7" in kj["layers"][1]["slots"], "all three layers with their defaults")
    lj = json.loads(files["LIFETIME.JSN"]); expect("dial_cw" in lj and len(lj["presses"]) == 5, "lifetime")
    # the same bytes through the firmware's USB read callback (what a PC would see)
    for lba in (0, 1, 3, 5, 6, 255):
        n, blk = e.msc_read(lba); expect(n == 512 and blk == img[lba * 512:lba * 512 + 512], f"block {lba} via the mass-storage callback")
    expect(e.msc_read(256)[0] == -1, "reading past the end fails")
    # an external check of the file system, when the tool is installed
    fsck = shutil.which("fsck.fat") or shutil.which("fsck.vfat") or shutil.which("dosfsck")
    if fsck:
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as f:
            f.write(img); path = f.name
        r = subprocess.run([fsck, "-n", path], capture_output=True, text=True); os.unlink(path)
        expect(r.returncode == 0, f"fsck.fat: {r.stdout[-300:]} {r.stderr[-200:]}")
    # refresh picks up new settings
    remap(e, 3, combo("z")); e.request({"cmd": "usbdrive", "op": "refresh"})
    img2 = b""
    for off in range(0, 131072, 1500):
        img2 += base64.b64decode(e.request({"cmd": "usbdrive", "op": "image", "off": off, "n": 1500})["d"])
    expect(json.loads(fat12_files(img2)[1]["KEYS.JSN"])["layers"][0]["slots"]["3"]["val"] == ["z"], "refresh rebuilds the image")
    for bad in ({"off": -1}, {"off": 131072}, {"n": 0}, {"n": 1501}):
        expect(e.request(dict({"cmd": "usbdrive", "op": "image"}, **bad)).get("err") == "range", f"range {bad}")
    expect(e.request({"cmd": "usbdrive", "op": "x"}).get("err") == "op", "op")
    # a long key list still fits: the image cuts what does not (and stays a valid file system)
    for i in range(1, 6):
        for g in ("hold", "double", "triple"):
            for layer in range(3):
                e.request({"cmd": "remap", "key": i, "layer": layer, "gesture": g, "type": "text", "val": "x" * 200})
    e.request({"cmd": "usbdrive", "op": "refresh"})
    img3 = b""
    for off in range(0, 131072, 1500):
        img3 += base64.b64decode(e.request({"cmd": "usbdrive", "op": "image", "off": off, "n": 1500})["d"])
    label, files = fat12_files(img3); expect("KEYS.JSN" in files and len(files["KEYS.JSN"]) > 5000, f"big key list: {len(files['KEYS.JSN'])} bytes")
    e.close()
