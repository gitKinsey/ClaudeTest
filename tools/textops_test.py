"""Pure-logic tests for snippets with variables and clipboard transforms (no GUI).   python3 tools/textops_test.py"""
import os
import random
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from desk_lib import hostactions, textops   # noqa: E402

NOW = datetime(2026, 10, 3, 14, 5, 9)
counters = {}


def counter(name):
    counters[name] = counters.get(name, 0) + 1
    return counters[name]


ctx = {"now": NOW, "clipboard": "CLIP", "counter": counter, "user": "kim", "host": "box", "rng": random.Random(1)}
e = lambda t: textops.expand(t, ctx)   # noqa: E731
assert e("{date} {time}") == "2026-10-03 14:05"
assert e("{datetime}") == "2026-10-03 14:05" and e("{weekday}") == "Saturday" and e("{iso}") == "2026-10-03T14:05:09"
assert e("{date:%d.%m.%Y}") == "03.10.2026" and e("{time:%H-%M-%S}") == "14-05-09"
assert e("[{clipboard}]") == "[CLIP]" and e("{user}@{host}") == "kim@box"
assert e("#{counter:inv} #{counter:inv} #{counter:other}") == "#1 #2 #1" and counters == {"inv": 2, "other": 1}
assert e("{{literal}} {nope} {date:") == "{literal} {nope} {date:", "escapes work; unknown / unterminated stay visible"
assert 1 <= int(e("{random:1-6}")) <= 6 and 5 <= int(e("{random:9-5}")) <= 9 and e("{random:x}") == "{random:x}"
u = e("{uuid}"); assert len(u) == 36 and u[14] == "4", u
assert textops.expand("{counter:x}", {"now": NOW}) == "{counter:x}", "no counter store -> untouched"
assert len(textops.expand("a" * 5000)) == textops.MAX_TEXT

t = textops.transform
assert t("upper", "ab") == "AB" and t("lower", "AB") == "ab" and t("title", "hello big world") == "Hello Big World"
assert t("sentence", "HELLO there. HOW are you") == "Hello there. How are you"
assert t("trim", "  a  \n  b ") == "a\nb" and t("oneline", "a \n b\t c") == "a b c"
assert t("snake", "Hello World-fooBar") == "hello_world_foo_bar" and t("kebab", "Hello World") == "hello-world"
assert t("camel", "hello big_world") == "helloBigWorld" and t("pascal", "hello big_world") == "HelloBigWorld"
assert t("json_pretty", '{"a":[1,2]}') == '{\n  "a": [\n    1,\n    2\n  ]\n}' and t("json_min", '{ "a" : [1, 2] }') == '{"a":[1,2]}'
for bad in ("json_pretty", "json_min"):
    try:
        t(bad, "{nope"); raise SystemExit("accepted bad json")
    except ValueError as ex:
        assert "not valid JSON" in str(ex)
assert t("url_encode", "a b&c/é") == "a%20b%26c%2F%C3%A9" and t("url_decode", "a%20b%26c") == "a b&c"
assert t("b64_encode", "héllo") == "aMOpbGxv" and t("b64_decode", "aMOpbGxv") == "héllo"
try:
    t("b64_decode", "***"); raise SystemExit("accepted bad base64")
except ValueError as ex:
    assert "Base64" in str(ex)
assert t("sort_lines", "b\nA\nc") == "A\nb\nc" and t("unique_lines", "a\nb\na\nb\nc") == "a\nb\nc" and t("reverse_lines", "1\n2\n3") == "3\n2\n1"
assert t("quote", "a\nb") == "> a\n> b" and t("count", "one two\nthree") == "3 words, 13 characters, 2 lines"
try:
    t("nope", "x"); raise SystemExit("accepted unknown transform")
except ValueError:
    pass
assert {k for k, _ in textops.transform_labels()} == set(textops.TRANSFORMS) and len(textops.TRANSFORMS) >= 20
for k in textops.TRANSFORMS:
    if k not in ("json_pretty", "json_min", "b64_decode"):
        t(k, "Some Text\nsecond line")                       # every transform runs on ordinary text
print("textops OK")

# ------------------------------------------------------------------ host ops snippet / clip
typed, allowed = [], {("snippet", "Hi {clipboard} {counter:n}"), ("clip", "upper"), ("clip", "json_pretty"), ("snippet", "x")}
ha = hostactions.HostActions(lambda: allowed, lambda: False, type_text=typed.append, read_clipboard=lambda: "web", counter=counter)
assert ha.run("snippet", "Hi {clipboard} {counter:n}") == (True, "typed 8 characters") and typed[-1] == "Hi web 1"
assert ha.run("clip", "upper")[0] and typed[-1] == "WEB"
ok, msg = ha.run("clip", "json_pretty"); assert not ok and "not valid JSON" in msg and typed[-1] == "WEB", "a failed transform types nothing"
ok, msg = ha.run("snippet", "not configured"); assert not ok and "not part of your key configuration" in msg
assert not hostactions.HostActions(lambda: allowed, lambda: False).run("snippet", "x")[0], "no typer available -> refused, no crash"
print("host snippet/clip OK")
print("ALL TEXTOPS TESTS PASSED")
