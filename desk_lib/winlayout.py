"""Window layouts: remember where your windows are (program, title, position, size) under a name and put them back with one key.
Linux: wmctrl. macOS: AppleScript (System Events, needs the Accessibility permission). Windows: PowerShell. All runners injectable.
The list / move commands print simple tab-separated lines that this module parses itself, so every parser is testable."""
import platform
import re
import shutil
import subprocess

PS_LIST = ("$sig='[DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr h, out RECT r); public struct RECT {public int L,T,R,B;}'; "
           "Add-Type -MemberDefinition $sig -Name Win32 -Namespace Native; "
           "Get-Process | Where-Object {$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle} | ForEach-Object { "
           "$rc = New-Object Native.Win32+RECT; [void][Native.Win32]::GetWindowRect($_.MainWindowHandle, [ref]$rc); "
           "\"$($_.MainWindowHandle)`t$($_.ProcessName)`t$($rc.L)`t$($rc.T)`t$($rc.R-$rc.L)`t$($rc.B-$rc.T)`t$($_.MainWindowTitle)\" }")
PS_MOVE = ("$sig='[DllImport(\"user32.dll\")] public static extern bool MoveWindow(IntPtr h,int x,int y,int w,int ht,bool r);'; "
           "$t = Add-Type -MemberDefinition $sig -Name Mv -Namespace Native -PassThru; [void]$t::MoveWindow([IntPtr]{id},{x},{y},{w},{h},$true)")
OSA_LIST = ('tell application "System Events"\nset out to ""\nrepeat with p in (every process whose visible is true)\nrepeat with w in (every window of p)\n'
            'set {x, y} to position of w\nset {ww, hh} to size of w\nset out to out & (id of p) & tab & (name of p) & tab & x & tab & y & tab & ww & tab & hh & tab & (name of w) & linefeed\n'
            'end repeat\nend repeat\nreturn out\nend tell')


def _run(args, timeout=8):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise ValueError(f"{args[0]} failed: {e}") from None
    if r.returncode != 0:
        raise ValueError(f"{args[0]}: {(r.stderr or 'failed').strip().splitlines()[-1][:100] if (r.stderr or '').strip() else 'failed'}")
    return r.stdout


class WinLayouts:
    def __init__(self, runner=None, system=None, which=None):
        self.run, self.system, self.which = runner or _run, system or platform.system(), which or shutil.which

    # ---- list the visible windows -> [{"id","process","title","x","y","w","h"}]
    def windows(self):
        if self.system == "Linux":
            if not self.which("wmctrl"):
                raise ValueError("window layouts need wmctrl (sudo apt install wmctrl) on Linux")
            out = []
            for ln in self.run(["wmctrl", "-lGp"]).splitlines():
                m = re.match(r"(0x[0-9a-fA-F]+)\s+(-?\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(\d+)\s+(\d+)\s+\S+\s+(.*)$", ln)
                if not m or m.group(2) == "-1":
                    continue
                proc = self.run(["ps", "-p", m.group(3), "-o", "comm="]).strip() if m.group(3) != "0" else ""
                out.append({"id": m.group(1), "process": proc, "title": m.group(8).strip(), "x": int(m.group(4)), "y": int(m.group(5)), "w": int(m.group(6)), "h": int(m.group(7))})
            return out
        if self.system == "Darwin":
            text = self.run(["osascript", "-e", OSA_LIST])
        elif self.system == "Windows":
            text = self.run(["powershell", "-NoProfile", "-Command", PS_LIST])
        else:
            raise ValueError("window layouts are not available on this system")
        return parse_tab_lines(text)

    def move(self, win, x, y, w, h):
        if self.system == "Linux":
            self.run(["wmctrl", "-i", "-r", win["id"], "-b", "remove,maximized_vert,maximized_horz"])
            self.run(["wmctrl", "-i", "-r", win["id"], "-e", f"0,{x},{y},{w},{h}"])
        elif self.system == "Darwin":
            self.run(["osascript", "-e", f'tell application "System Events" to tell (first window of (first process whose id is {win["id"]})) to set position to {{{x}, {y}}}'])
            self.run(["osascript", "-e", f'tell application "System Events" to tell (first window of (first process whose id is {win["id"]})) to set size to {{{w}, {h}}}'])
        else:
            self.run(["powershell", "-NoProfile", "-Command", PS_MOVE.format(id=int(win["id"]), x=x, y=y, w=w, h=h)])

    # ---- save / restore under a name
    def save(self, store, name):
        wins = [{k: w[k] for k in ("process", "title", "x", "y", "w", "h")} for w in self.windows() if w["w"] > 50 and w["h"] > 50]
        if not wins:
            raise ValueError("no windows found to save")
        store[name] = wins[:24]
        return len(store[name])

    def restore(self, store, name):
        saved = store.get(name)
        if not saved:
            raise ValueError(f"there is no saved window layout called '{name}'")
        cur, used, moved = self.windows(), set(), 0
        for s in saved:
            hit = next((w for w in cur if w["id"] not in used and w["process"] == s["process"] and (w["title"] == s["title"] or w["title"][:18] == s["title"][:18])), None) \
                or next((w for w in cur if w["id"] not in used and w["process"] == s["process"] and s["process"]), None)
            if hit:
                used.add(hit["id"])
                self.move(hit, s["x"], s["y"], s["w"], s["h"])
                moved += 1
        if not moved:
            raise ValueError("none of the saved programs is open")
        return moved


def parse_tab_lines(text):
    """id <TAB> process <TAB> x <TAB> y <TAB> w <TAB> h <TAB> title"""
    out = []
    for ln in text.splitlines():
        p = ln.rstrip("\r").split("\t", 6)
        if len(p) < 7:
            continue
        try:
            out.append({"id": p[0].strip(), "process": p[1].strip(), "x": int(float(p[2])), "y": int(float(p[3])), "w": int(float(p[4])), "h": int(float(p[5])), "title": p[6].strip()})
        except ValueError:
            continue
    return out
