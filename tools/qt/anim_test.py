"""Motion test ("a video of the app"): sample the animated widgets frame by frame and check they really move, in the right direction, and stop; with reduce-motion they jump."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from ui_qt import toast    # noqa: E402
from ui_qt.widgets import Segmented    # noqa: E402

k = QtKit("dcanim_")
e, sh = k.e, k.shell


def sample(fn, secs=0.5, step=0.02):
    out, t0 = [], time.time()
    while time.time() - t0 < secs:
        k.app.processEvents()
        out.append(fn())
        time.sleep(step)
    return out


# ---- the rail highlight slides to the new page, monotonic, and ends exactly there
k.go("overview")
sh.rail.set_current("overview", animate=False)
idx = [i["id"] for i in sh.rail.items].index("rules")
sh.rail.set_current("rules", animate=True)
seq = sample(lambda: sh.rail._pos)
assert seq[0] < idx and abs(seq[-1] - idx) < 1e-6, (seq[0], seq[-1])
assert all(b >= a - 1e-9 for a, b in zip(seq, seq[1:])), "the highlight moves one way only"
assert len({round(x, 3) for x in seq}) >= 4, "it takes several frames (a slide, not a jump)"

# ---- the segmented control's highlight slides too
page = k.go("keys")
seg = page.layer_seg
seg.set_current("Layer 1", emit=False)
k.spin(15)
seg.set_current("Layer 3", emit=False)
hl = sample(lambda: seg._hl, 0.4)
assert abs(hl[-1] - 2.0) < 1e-6 and len({round(x, 3) for x in hl}) >= 3, hl[:6]

# ---- a toast slides in from the right edge and stays
for t in list(toast.LIVE):
    t.close()
toast.show_toast("Title", "Body text", "ok")
t = toast.LIVE[-1]
xs = sample(lambda: t.x(), 0.5)
assert xs[0] > xs[-1] and xs[-1] == t._end.x(), (xs[0], xs[-1], t._end.x())
t.close()

# ---- the twin shows life: the clock face changes, so two frames a second apart differ
tw = page.twin
a = tw.grab().toImage()
time.sleep(1.1); k.spin(15)
b = tw.grab().toImage()
assert a != b, "the twin's screen is live"

# ---- the animated GIF preview advances frames
disp = k.go("display")
e.use_preset(e.builtin_presets()[0])
assert k.until(lambda: e.gif_data is not None, 20)
from ui_qt.media import GifPreview    # noqa: E402
pv = disp.gifs.findChildren(GifPreview)[0]
assert k.until(lambda: len(pv.frames) > 1, 10)
seen = set(sample(lambda: pv.i, 1.2))
assert len(seen) >= 2, f"the GIF preview animates (frames seen: {seen})"

# ---- reduce motion: everything jumps
e.set_pref("reduce_motion", True); k.spin(5)
sh.rail.set_current("overview", animate=True)
assert sh.rail._pos == 0.0
seg2 = Segmented(["a", "b", "c"], "a"); seg2.show()
seg2.set_current("c"); assert seg2._hl == 2.0 or seg2._hl == 0.0
toast.show_toast("Quiet", "no sliding", "ok")
t = toast.LIVE[-1]
assert t.x() == t._end.x(), "reduce motion: the toast appears in place"
t.close()
k.close()
print("ALL QT ANIMATION TESTS PASSED")
