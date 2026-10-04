"""NativeEmu: the same interface as tools/emulator_test.py's QEMU `Emu`, but the firmware is the native host build (tools/native/build.py).
Serial lines go through pipes; '#...' lines are native events (led, bl, hid, restart); esp_restart() (exit code 75) relaunches the process on the same NVS / filesystem,
a "power cycle" keeps them as well."""
import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import emulator_test as et    # noqa: E402


class NativeEmu(et.Emu):
    def __init__(self, binary, workdir, keep_state=False, env=None):
        self.binary, self.workdir, self.extra_env = binary, workdir, dict(env or {})
        os.makedirs(workdir, exist_ok=True)
        if not keep_state:
            for f in ("nvs.tsv",):
                try:
                    os.remove(os.path.join(workdir, f))
                except OSError:
                    pass
            import shutil
            shutil.rmtree(os.path.join(workdir, "fs"), ignore_errors=True)
        self.lock = threading.Lock()
        self.msgs, self.log, self.events = [], [], []
        self.hid, self.leds, self.backlight, self.restarts = [], [], None, 0
        self.flash = binary
        self.port = 0
        self.rid = 0
        self._closing = False
        self._launch(reset="power")

    def _launch(self, reset="power"):
        env = dict(os.environ, DC_NATIVE_FS=os.path.join(self.workdir, "fs"), DC_NATIVE_NVS=os.path.join(self.workdir, "nvs.tsv"), DC_NATIVE_RESET=reset, **self.extra_env)
        self.p = subprocess.Popen([self.binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, bufsize=0)
        threading.Thread(target=self._reader, args=(self.p,), daemon=True).start()

    def _reader(self, proc):
        buf = b""
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._line(line.decode("utf-8", "replace").strip())
        proc.wait()
        if proc.returncode == 75 and not self._closing and proc is self.p:           # esp_restart(): the pad resets itself
            self.restarts += 1
            time.sleep(0.2)
            self._launch(reset="sw")

    def _line(self, t):
        if not t:
            return
        with self.lock:
            if t.startswith("{"):
                try:
                    self.msgs.append(json.loads(t))
                    return
                except ValueError:
                    pass
            if t.startswith("#"):
                parts = t[1:].split()
                self.events.append(parts)
                if parts and parts[0] == "hid":
                    self.hid.append(parts[1:])
                elif parts and parts[0] == "led":
                    self.leds.append(tuple(int(x) for x in parts[1:4]))
                elif parts and parts[0] == "bl":
                    self.backlight = int(parts[1])
                return
            self.log.append(t)

    def pump(self, secs=0.0):
        if secs > 0:
            time.sleep(secs)

    def send(self, obj):
        raw = obj if isinstance(obj, (bytes, bytearray)) else (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        for _ in range(50):
            try:
                self.p.stdin.write(raw)
                self.p.stdin.flush()
                return
            except (BrokenPipeError, OSError):
                time.sleep(0.1)                                    # the process is restarting
        raise RuntimeError("the native firmware is not running")

    def control(self, line):
        """'#key 1 1', '#enc 3', '#encsw 1', '#pin 4 0' ..."""
        self.send((line.rstrip("\n") + "\n").encode())

    def request(self, obj, timeout=6.0, want=None):
        with self.lock:
            self.msgs.clear()
        self.rid += 1
        raw = isinstance(obj, (bytes, bytearray))
        self.send(obj if raw else dict(obj, id=self.rid))
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.01)
            with self.lock:
                for i, m in enumerate(self.msgs):
                    if "ok" in m and (raw or m.get("id") == self.rid):
                        del self.msgs[: i + 1]
                        return m
        raise TimeoutError(f"no reply to {obj if not raw else '<raw>'}")

    def take(self, evt):
        """Pop every received message whose "evt" is `evt` (events are not cleared by request() until the next request)."""
        with self.lock:
            got = [m for m in self.msgs if m.get("evt") == evt]
            self.msgs = [m for m in self.msgs if m.get("evt") != evt]
        return got

    def power_cycle(self):
        self.close()
        time.sleep(0.2)
        self._closing = False
        self.msgs, self.log, self.events, self.hid, self.leds = [], [], [], [], []
        self._launch(reset="power")

    def close(self):
        self._closing = True
        try:
            self.p.stdin.write(b"#quit\n")
            self.p.stdin.flush()
            self.p.wait(timeout=2)
        except Exception:                                          # noqa: BLE001
            try:
                self.p.kill()
            except Exception:                                      # noqa: BLE001
                pass
