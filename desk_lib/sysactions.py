"""Operating-system actions the pad can trigger through the app: per-program volume, Do-Not-Disturb, audio output / microphone,
screenshots. Every command runner and the OS name are injectable, so each branch is tested with canned tool output.
Best effort by design: each function raises ValueError with a readable reason when the OS / tool cannot do it."""
import datetime as dt
import platform
import re
import shutil
import subprocess
from pathlib import Path


def _run(args, timeout=5):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise ValueError(f"{args[0]} failed: {e}") from None
    if r.returncode != 0:
        raise ValueError(f"{args[0]}: {(r.stderr or r.stdout or 'failed').strip().splitlines()[-1][:100]}")
    return r.stdout


class SysActions:
    def __init__(self, runner=None, system=None, which=None):
        self.run = runner or _run
        self.system = system or platform.system()
        self.which = which or shutil.which

    # ---------------------------------------------------------------- per-program volume (the program in front)
    def app_volume(self, program, delta):
        """Change the volume of the audio stream(s) of `program` (a process / application name) by delta percent points."""
        program = (program or "").lower()
        if not program:
            raise ValueError("no program in front to change the volume of")
        if self.system == "Linux":
            if not self.which("pactl"):
                raise ValueError("per-program volume needs pactl (PulseAudio / PipeWire)")
            out = self.run(["pactl", "list", "sink-inputs"])
            changed = 0
            for block in re.split(r"\n(?=Sink Input #)", out):
                m = re.match(r"Sink Input #(\d+)", block)
                if not m:
                    continue
                names = " ".join(re.findall(r'application\.(?:name|process\.binary)\s*=\s*"([^"]*)"', block)).lower()
                if program not in names:
                    continue
                self.run(["pactl", "set-sink-input-volume", m.group(1), f"{'+' if delta >= 0 else '-'}{abs(int(delta))}%"])
                changed += 1
            if not changed:
                raise ValueError(f"'{program}' is not playing any sound right now")
            return f"{program} volume {'+' if delta >= 0 else '-'}{abs(int(delta))}% ({changed} stream{'s' if changed != 1 else ''})"
        if self.system == "Windows":
            try:
                from pycaw.pycaw import AudioUtilities               # noqa: PLC0415
            except Exception:                                      # noqa: BLE001
                raise ValueError("per-program volume on Windows needs:  pip install pycaw") from None
            n = 0
            for s in AudioUtilities.GetAllSessions():
                if s.Process and program in s.Process.name().lower():
                    v = s.SimpleAudioVolume
                    v.SetMasterVolume(max(0.0, min(1.0, v.GetMasterVolume() + delta / 100.0)), None)
                    n += 1
            if not n:
                raise ValueError(f"'{program}' is not playing any sound right now")
            return f"{program} volume {delta:+d}%"
        raise ValueError("per-program volume is not available on this system")

    # ---------------------------------------------------------------- Do Not Disturb
    def dnd(self, state):
        """state: on | off | toggle"""
        if state not in ("on", "off", "toggle"):
            raise ValueError("Do-Not-Disturb: use on, off or toggle")
        if self.system == "Linux":
            if not self.which("gsettings"):
                raise ValueError("Do-Not-Disturb needs GNOME (gsettings); other desktops: not supported")
            banners = self.run(["gsettings", "get", "org.gnome.desktop.notifications", "show-banners"]).strip() == "true"
            want_dnd = banners if state == "toggle" else state == "on"          # toggle: banners are shown now -> switch Do Not Disturb on
            self.run(["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "false" if want_dnd else "true"])
            return "Do Not Disturb " + ("on" if want_dnd else "off")
        if self.system == "Darwin":
            if not self.which("shortcuts"):
                raise ValueError("Do-Not-Disturb on macOS needs a Shortcut named 'Do Not Disturb On' / 'Do Not Disturb Off' (Shortcuts app)")
            name = "Do Not Disturb Off" if state == "off" else "Do Not Disturb On"
            self.run(["shortcuts", "run", name])
            return name
        if self.system == "Windows":
            if state == "toggle":
                raise ValueError("on Windows use dnd on / dnd off (toggle cannot read the current state)")
            key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
            self.run(["reg", "add", key, "/v", "NOC_GLOBAL_SETTING_TOASTS_ENABLED", "/t", "REG_DWORD", "/d", "1" if state == "off" else "0", "/f"])
            return f"Do Not Disturb {state} (notification banners)"
        raise ValueError("Do-Not-Disturb is not available on this system")

    # ---------------------------------------------------------------- audio output / microphone
    def audio_output(self, target):
        """target: 'next' or part of a device name."""
        if self.system == "Linux":
            if not self.which("pactl"):
                raise ValueError("switching the audio output needs pactl")
            sinks = [ln.split("\t")[1] for ln in self.run(["pactl", "list", "short", "sinks"]).splitlines() if "\t" in ln]
            if not sinks:
                raise ValueError("no audio outputs found")
            cur = self.run(["pactl", "get-default-sink"]).strip()
            if target == "next":
                new = sinks[(sinks.index(cur) + 1) % len(sinks)] if cur in sinks else sinks[0]
            else:
                hit = [s for s in sinks if target.lower() in s.lower()]
                if not hit:
                    raise ValueError(f"no audio output matching '{target}' (found: {', '.join(sinks)[:80]})")
                new = hit[0]
            self.run(["pactl", "set-default-sink", new])
            return f"audio output: {new}"
        if self.system == "Darwin":
            if not self.which("SwitchAudioSource"):
                raise ValueError("switching the audio output on macOS needs:  brew install switchaudio-osx")
            if target == "next":
                devs = [d for d in self.run(["SwitchAudioSource", "-a", "-t", "output"]).splitlines() if d.strip()]
                cur = self.run(["SwitchAudioSource", "-c", "-t", "output"]).strip()
                new = devs[(devs.index(cur) + 1) % len(devs)] if cur in devs else devs[0]
            else:
                new = target
            self.run(["SwitchAudioSource", "-s", new, "-t", "output"])
            return f"audio output: {new}"
        if self.system == "Windows":
            if not self.which("nircmd"):
                raise ValueError("switching the audio output on Windows needs NirCmd (nircmd.exe in the PATH)")
            if target == "next":
                raise ValueError("on Windows name the device: audio_out arg = part of its name")
            self.run(["nircmd", "setdefaultsounddevice", target, "1"])
            return f"audio output: {target}"
        raise ValueError("switching the audio output is not available on this system")

    def microphone(self, state):
        """state: mute | unmute | toggle (the default microphone, system-wide)"""
        if state not in ("mute", "unmute", "toggle"):
            raise ValueError("microphone: use mute, unmute or toggle")
        if self.system == "Linux":
            if not self.which("pactl"):
                raise ValueError("microphone control needs pactl")
            arg = {"mute": "1", "unmute": "0", "toggle": "toggle"}[state]
            self.run(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", arg])
            muted = "yes" in self.run(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"]).lower()
            return "microphone " + ("muted" if muted else "live")
        if self.system == "Darwin":
            vol = self.run(["osascript", "-e", "input volume of (get volume settings)"]).strip()
            cur_muted = vol.isdigit() and int(vol) == 0
            to_mute = (not cur_muted) if state == "toggle" else state == "mute"
            self.run(["osascript", "-e", f"set volume input volume {0 if to_mute else 75}"])
            return "microphone " + ("muted" if to_mute else "live")
        if self.system == "Windows":
            if not self.which("nircmd"):
                raise ValueError("microphone control on Windows needs NirCmd (nircmd.exe in the PATH)")
            self.run(["nircmd", "mutesysvolume", {"mute": "1", "unmute": "0", "toggle": "2"}[state], "default_record"])
            return "microphone " + state
        raise ValueError("microphone control is not available on this system")

    # ---------------------------------------------------------------- screenshot
    def screenshot(self, folder, grab=None, now=None):
        """Save the whole screen as PNG into folder. grab() -> PIL image (default: PIL.ImageGrab). Returns the path."""
        folder = Path(folder or Path.home() / "Pictures")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            img = (grab or self._grab)()
        except Exception as e:                                       # noqa: BLE001
            raise ValueError(f"cannot take a screenshot here: {e}") from None
        name = (now or dt.datetime.now()).strftime("screenshot-%Y%m%d-%H%M%S.png")
        path = folder / name
        n = 1
        while path.exists():
            n += 1
            path = folder / name.replace(".png", f"-{n}.png")
        img.save(path)
        return path

    @staticmethod
    def _grab():
        from PIL import ImageGrab                                    # noqa: PLC0415
        return ImageGrab.grab()


def parse_volume_arg(arg):
    """'up' | 'down' | '+5' | '-10' -> signed percent points."""
    a = str(arg).strip().lower()
    if a in ("up", "+"):
        return 5
    if a in ("down", "-"):
        return -5
    if re.fullmatch(r"[+-]?\d{1,2}", a):
        return max(-50, min(50, int(a)))
    raise ValueError("appvol needs up, down or a number like +5 / -10")

