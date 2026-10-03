"""Sound-reactive LED: three frequency bands (low / mid / high) from the audio input -> the pad's RGB colour.
The input is a microphone or a loopback device (e.g. 'Stereo Mix') picked in the sound settings; needs the optional `sounddevice` + `numpy`.
Everything numeric is plain Python (Goertzel), so it is testable without sound hardware."""
import math
import threading

BANDS = ((60, 120, 200), (400, 800, 1500), (3000, 5000, 8000))       # centre frequencies per band (low, mid, high)


def goertzel(samples, rate, freq):
    """Power of one frequency in the sample block."""
    n = len(samples)
    if n == 0:
        return 0.0
    k = round(n * freq / rate)
    w = 2 * math.pi * k / n
    c = 2 * math.cos(w)
    s1 = s2 = 0.0
    for x in samples:
        s0 = x + c * s1 - s2
        s2, s1 = s1, s0
    return max(0.0, s1 * s1 + s2 * s2 - c * s1 * s2) / (n * n)


def band_levels(samples, rate):
    """-> [low, mid, high], each 0..1 (square-root scaled so quiet music still moves the LED)."""
    out = []
    for group in BANDS:
        p = sum(goertzel(samples, rate, f) for f in group if f < rate / 2) / len(group)
        out.append(min(1.0, math.sqrt(p) * 6))
    return out


def color(levels, floor=0.04):
    """Bands -> (r, g, b): low = red, mid = green, high = blue. Silence -> off (0, 0, 0)."""
    lo, mid, hi = levels
    rgb = tuple(int(255 * max(0.0, min(1.0, v))) for v in (lo, mid, hi))
    return (0, 0, 0) if max(levels) < floor else rgb


def available():
    import importlib.util                                            # noqa: PLC0415
    return all(importlib.util.find_spec(m) is not None for m in ("numpy", "sounddevice"))


class Spectrum:
    """Listens to the default input in the background; .levels holds the latest [low, mid, high]."""

    def __init__(self, stream_factory=None, rate=16000, block=1024):
        self.rate, self.block, self.levels = rate, block, [0.0, 0.0, 0.0]
        self._factory, self._stream, self._lock = stream_factory, None, threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        try:
            samples = [float(x[0]) if hasattr(x, "__len__") else float(x) for x in indata]          # (frames, channels) array or plain list
        except Exception:                                            # noqa: BLE001
            return
        lv = band_levels(samples, self.rate)
        with self._lock:
            self.levels = [max(new, old * 0.6) for new, old in zip(lv, self.levels)]          # fast attack, smooth decay

    def start(self):
        if self._stream is not None:
            return
        if self._factory is None:
            try:
                import sounddevice as sd                              # noqa: PLC0415
            except ImportError:
                raise ValueError("the sound-reactive LED needs:  pip install sounddevice numpy") from None
            self._factory = lambda cb: sd.InputStream(channels=1, samplerate=self.rate, blocksize=self.block, callback=cb)
        try:
            self._stream = self._factory(self._callback)
            self._stream.start()
        except Exception as e:                                       # noqa: BLE001  (no input device, permission)
            self._stream = None
            raise ValueError(f"no usable audio input: {e}") from None

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:                                        # noqa: BLE001
                pass
            self._stream = None
        self.levels = [0.0, 0.0, 0.0]

    def current(self):
        with self._lock:
            return list(self.levels)
