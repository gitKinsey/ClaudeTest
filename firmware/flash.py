#!/usr/bin/env python3
"""
Flash a prebuilt DeskCompanion image onto an ESP32-S3 (Waveshare ESP32-S3-Zero) - no Arduino IDE needed.

    pip install esptool pyserial
    python flash.py --list                      show serial ports
    python flash.py --image core                CoreBringup  (step 1: LED + USB serial + wiring tests)
    python flash.py --image full                DeskCompanion (full firmware, cable only - the default)
    python flash.py --image wifi                DeskCompanion built with DC_ENABLE_WIFI=1 (NTP + Wi-Fi update)
    python flash.py --image path/to/file.bin    any merged image (written at 0x0)
    python flash.py --image core --port COM5    choose the port yourself

The board must be in download mode: hold BOOT, plug in USB (or tap RESET while holding BOOT), release BOOT.
If the pad already runs DeskCompanion / CoreBringup the script puts it into download mode by itself.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = {"core": os.path.join(HERE, "CoreBringup.bin"), "full": os.path.join(HERE, "DeskCompanion.bin"),
          "wifi": os.path.join(HERE, "DeskCompanion-wifi.bin")}
ESP_VID, ROM_PID = 0x303A, 0x1001


def ports():
    from serial.tools import list_ports
    return list(list_ports.comports())


def show_ports():
    ps = ports()
    if not ps:
        print("no serial ports found")
    for p in ps:
        vp = "----:----" if p.vid is None else f"{p.vid:04X}:{p.pid or 0:04X}"
        tag = "  <-- Espressif" if p.vid == ESP_VID else ""
        mode = "  (ROM download mode)" if p.vid == ESP_VID and p.pid == ROM_PID else ""
        print(f"  {p.device:12s} {vp}  {p.description}{tag}{mode}")


def rom_port():
    for p in ports():
        if p.vid == ESP_VID and p.pid == ROM_PID:
            return p.device
    return None


def try_enter_download(port):
    """If a DeskCompanion / CoreBringup answers on `port`, ask it to reboot into the ROM flasher."""
    import serial
    try:
        s = serial.Serial(port, 115200, timeout=0.3)
    except Exception:                                   # noqa: BLE001
        return False
    try:
        s.write(b'{"cmd":"hello"}\n')
        end, got = time.time() + 3, b""
        while time.time() < end and b"desk-companion" not in got:
            got += s.read(256)
        if b"desk-companion" not in got:
            return False
        print(f"{port}: running DeskCompanion firmware - rebooting it into download mode ...")
        s.write(b'{"cmd":"reboot","mode":"download"}\n')
        time.sleep(0.5)
        return True
    finally:
        try:
            s.close()
        except Exception:                               # noqa: BLE001
            pass


def run_esptool(args):
    """`python -m esptool ...`, or - inside a packaged (PyInstaller) app that has no Python - esptool in this process."""
    if getattr(sys, "frozen", False):
        import esptool
        try:
            esptool.main(args)
            return 0
        except SystemExit as e:
            return int(e.code or 0) if isinstance(e.code, int) or e.code is None else 1
        except Exception as e:                              # noqa: BLE001
            print(f"esptool error: {e}")
            return 1
    return subprocess.call([sys.executable, "-m", "esptool"] + args)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", default="", help="core | full | wifi | path to a merged .bin")
    ap.add_argument("--port", default="", help="serial port (default: auto-detect)")
    ap.add_argument("--baud", default="460800")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args(argv)
    if a.list:
        show_ports()
        return 0
    if not a.image:
        ap.print_help()
        return 2
    image = IMAGES.get(a.image, a.image)
    if not os.path.isfile(image):
        print(f"image not found: {image}")
        return 2
    try:
        import esptool
    except ImportError:
        print("esptool is not installed:  pip install esptool pyserial")
        return 2

    port = a.port
    if port and not rom_port():
        try_enter_download(port)                         # user-chosen port may be a running pad
    deadline = time.time() + 15
    rp = rom_port()
    while not rp and time.time() < deadline:
        time.sleep(0.5)
        rp = rom_port()
    if rp:
        port = rp
    if not port:
        print("No ESP32-S3 in download mode found. Hold BOOT, plug in the USB cable (or tap RESET while holding BOOT), release BOOT, "
              "then run this again. Current ports:")
        show_ports()
        return 1
    print(f"image : {image}  ({os.path.getsize(image)} bytes)\nport  : {port}")
    if not a.yes:
        if input("Write this image to the board now? [y/N] ").strip().lower() != "y":
            print("cancelled")
            return 1
    try:
        major = int(str(esptool.__version__).split(".")[0])
    except (AttributeError, ValueError):
        major = 4
    d = (lambda t: t.replace("_", "-")) if major >= 5 else (lambda t: t)       # esptool 5 uses dashes, 4 uses underscores
    cmd = ["--chip", "esp32s3", "--port", port, "--baud", a.baud,
           "--before", d("default_reset"), "--after", d("hard_reset"), d("write_flash"), "-z",
           d("--flash_mode"), "dio", d("--flash_freq"), "80m", d("--flash_size"), "4MB", "0x0", image]
    print("running: esptool", " ".join(cmd), flush=True)
    rc = run_esptool(cmd)
    if rc == 0:
        print("\nDone. Unplug and re-plug the board (or press RESET). The port number will probably be different now.")
    else:
        print("\nesptool failed. Typical fixes: put the board in download mode (BOOT held while plugging in), try another cable / USB port, "
              "close the Arduino Serial Monitor, or lower the speed with --baud 115200.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
