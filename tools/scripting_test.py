"""Pure-logic tests for the macro scripting language (no GUI).   python3 tools/scripting_test.py"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from desk_lib import scripting as sc   # noqa: E402

NOW = datetime(2026, 10, 5, 10, 30)                      # a Monday
dry = lambda src, **kw: sc.dry_run(src, now=kw.pop("now", NOW), **kw)   # noqa: E731


def fails(src, needle, **kw):
    try:
        sc.run(src, sc.Recorder(), **kw); raise SystemExit(f"accepted: {src!r}")
    except sc.ScriptError as e:
        assert needle in str(e), (needle, str(e))
        return e


# ---- plain commands
out = dry("""# comment
key ctrl+shift+t
text hello {date}
wait 250
click double
scroll -3
media play_pause
open https://example.org
app calc
file /tmp/x
notify done
""")
assert out == ["key CTRL+SHIFT+t", "type 'hello 2026-10-05'", "wait 250 ms", "click double", "scroll -3", "media play_pause",
               "url https://example.org", "app calc", "file /tmp/x", "notify done", "(10 commands)"], out
assert dry("key cmd+F5\nkey PRIMARY+c\nkey enter")[:3] == ["key GUI+F5", "key PRIMARY+c", "key ENTER"]

# ---- loops, nesting, stop
assert dry("repeat 3\nkey a\nend")[:3] == ["key a"] * 3 and dry("repeat 2\nrepeat 2\ntext x\nend\nend")[-1] == "(4 commands)"
assert dry("key a\nstop\nkey b") == ["key a", "(2 commands)"]

# ---- conditions
src = 'if window "Visual Studio"\ntext code\nelse\ntext other\nend'
assert dry(src, window=("code", "main.py - Visual Studio Code"))[0] == "type 'code'" and dry(src, window=("x", "Notepad"))[0] == "type 'other'"
assert dry('if not process "firefox"\ntext no-ff\nend', window=("chrome", ""))[0] == "type 'no-ff'"
assert dry('if process "FIRE"\ntext y\nend', window=("firefox", ""))[0] == "type 'y'", "process match is case-insensitive substring"
assert dry('if clipboard "http"\ntext link\nelse\ntext none\nend', clipboard="see http://x")[0] == "type 'link'"
assert dry("if time 09:00-17:00\ntext work\nelse\ntext home\nend")[0] == "type 'work'"
assert dry("if time 22:00-06:00\ntext night\nelse\ntext day\nend")[0] == "type 'day'"
assert dry("if time 22:00-06:00\ntext night\nelse\ntext day\nend", now=datetime(2026, 10, 5, 23, 0))[0] == "type 'night'"
assert dry("if time 22:00-06:00\ntext night\nelse\ntext day\nend", now=datetime(2026, 10, 5, 5, 59))[0] == "type 'night'"
assert dry("if weekday mon-fri\ntext wd\nelse\ntext we\nend")[0] == "type 'wd'" and dry("if weekday sat,sun\ntext we\nelse\ntext wd\nend")[0] == "type 'wd'"
assert dry("if weekday fri-mon\ntext yes\nend")[0] == "type 'yes'", "wrap-around range includes Monday"
assert dry("if window a\nif window b\ntext ab\nelse\ntext a-only\nend\nelse\ntext none\nend", window=("", "a b"))[0] == "type 'ab'"

# ---- variables (own variables beat built-ins; set expands)
out = dry("set who = {clipboard}\nset greeting = Hi {who}!\ntext {greeting} on {date}\nset date = custom\ntext {date}", clipboard="Kim")
assert out[:2] == ["type 'Hi Kim! on 2026-10-05'", "type 'custom'"], out
assert dry("text {nope} {{x}}")[0] == "type '{nope} {x}'"

# ---- sub-scripts and recursion guard
lib = {"inner": "text inner\nrun leaf", "leaf": "key z", "loop": "run loop"}
assert dry("run inner", lookup=lib.get)[:2] == ["type 'inner'", "key z"]
e = fails("run missing", "no saved script called 'missing'", lookup=lib.get)
e = fails("run loop", "more than 5 levels", lookup=lib.get)
lib["bad"] = "key nonsense+q"
e = fails("run bad", "in 'bad': line 1: unknown key 'nonsense'", lookup=lib.get)

# ---- limits
fails("repeat 100\nrepeat 100\nkey a\nend\nend", "more than 500 commands")
fails("repeat 7\nwait 10000\nend", "60 s of waiting")
fails("shell rm -rf /", "switched off")
assert sc.dry_run("shell echo hi", now=NOW)[0] == "shell echo hi", "a dry run lists shell commands without running them"
rec = sc.Recorder(); sc.run("shell echo hi", rec, shell_ok=True); assert rec.log == ["shell echo hi"]

# ---- syntax errors carry line numbers
for src, needle, line in (("key", "needs a combination", 1), ("\n\nrepeat x", "repeat needs a number", 3), ("repeat 101\nend", "from 1 to 100", 1),
                          ("wait 99999", "wait needs milliseconds", 1), ("click triple", "click needs", 1), ("scroll 0", "scroll needs", 1),
                          ("scroll 99", "scroll needs", 1), ("media loud", "media needs", 1), ("set x", "NAME = VALUE", 1), ("text", "needs an argument", 1),
                          ("end", "without a matching", 1), ("else", "without a matching", 1), ("repeat 2\nkey a", "never closed", 1),
                          ("if window a\nelse\nelse\nend", "'else' without", 3), ("if sunny\nend", "unknown condition", 1), ("if time 25:00-01:00\nend", "not a time", 1),
                          ("if weekday xyz\nend", "unknown weekday", 1), ("if window\nend", "needs one text", 1), ('if window "a\nend', "unbalanced", 1),
                          ("dance", "unknown command 'dance'", 1), ("key ctrl+a+b+c+d", "at most 4 keys", 1), ("key ctrl+nope", "unknown key 'nope'", 1)):
    e = fails(src, needle)
    assert e.line == line, (src, e.line, line)
try:
    sc.parse("x" * 7000); raise SystemExit("accepted a huge script")
except sc.ScriptError as e:
    assert "too long" in str(e)
assert sc.dry_run("", now=NOW) == ["(0 commands)"]
# ---- version 2 commands: jitter, exec + var conditions, card, alert, layout, http, ask, translate, moveto / clickat, do
class Rec2(sc.Recorder):
    def __init__(self, **kw):
        super().__init__(**kw); self.code = 0; self.waits = []
    def wait(self, ms): self.waits.append(ms); super().wait(ms)
    def exec(self, cmd): self.log.append(f"exec {cmd}"); return self.code, "line one\nline two"
    def http(self, arg): self.log.append(f"http {arg}"); return "200"
    def ask(self, prompt): self.log.append(f"ask {prompt!r}"); return "42"
    def translate(self, lang): self.log.append(f"translate {lang}"); return "hallo"
import random   # noqa: E402
r = Rec2(); sc.run("wait 200 jitter 50\nwait 100 jitter 0\nwait 20 jitter 1000", r, ctx={"rng": random.Random(3)})
assert 150 <= r.waits[0] <= 250 and r.waits[1] == 100 and 0 <= r.waits[2] <= 1020, r.waits
r = Rec2(); r.code = 0
sc.run("exec make test\nif var exit == 0\ncard BUILD | passed | {out} | done\nalert 00ff00 2\nelse\ncard BUILD | FAILED | exit {exit} |\nalert ff0000\nend", r, shell_ok=True)
assert r.log == ["exec make test", "card 'BUILD' 'passed' 'line one' 'done'", "alert 00ff00 x2"], r.log
r = Rec2(); r.code = 2
sc.run("exec make test\nif var exit == 0\nalert 00ff00\nelse\ncard BUILD | FAILED | exit {exit} |\nalert ff0000\nend", r, shell_ok=True)
assert r.log[-2:] == ["card 'BUILD' 'FAILED' 'exit 2' ''", "alert ff0000 x3"], r.log
fails("exec ls", "shell commands")
vr = Rec2(); sc.run("set n = 7\nif var n > 5\ntext big\nend\nif var n < 5\ntext small\nend\nif var n != 7\ntext no\nend\nif not var n == 8\ntext not8\nend\nset s = Hello World\nif var s contains world\ntext has\nend\nif var missing == \ntext empty\nend", vr)
assert [x for x in vr.log if x.startswith("type")] == ["type 'big'", "type 'not8'", "type 'has'", "type 'empty'"], vr.log
r = Rec2(); sc.run("layout save work\nlayout work\nhttp POST https://x.example/h {{\"a\": 1}}\nask Summarize {clipboard}\ntext {answer}\nhttp GET https://y.example\ntext {http}\ntranslate de\ntext {translated}\n"
                   "moveto 100 200\nclickat 5 6 right\nclickat 7 8\ndo dnd on\ndo shot default", r, ctx={"clipboard": "CLIP"})
assert r.log == ["layout save work", "layout work", 'http POST https://x.example/h {"a": 1}', "ask 'Summarize CLIP'", "type '42'", "http GET https://y.example", "type '200'", "translate de",
                 "type 'hallo'", "move the pointer to 100,200", "click right at 5,6", "click left at 7,8", "dnd on", "shot default"], r.log
for src, needle in (("alert zzz", "alert needs"), ("alert ff0000 11", "alert needs"), ("card", "card needs"), ("card a|b|c|d|e", "card needs"), ("moveto 1", "moveto needs"), ("moveto 1 2 left", "moveto needs"),
                    ("clickat a b", "clickat needs"), ("do fly high", "do needs"), ("do dnd", "do needs"), ("translate german", "translate needs"), ("wait 5 jitter", "wait needs"),
                    ("if var x ~ 1\nend", "unknown condition"), ("exec", "needs an argument"), ("http", "needs an argument")):
    fails(src, needle)
assert sc.dry_run("exec ls\nif var exit == 0\ntext fine\nend", now=NOW) == ["exec ls", "type 'fine'", "(2 commands)"], "a dry run answers (exit 0, '(dry run)') and walks on"
print("ALL SCRIPTING TESTS PASSED")
