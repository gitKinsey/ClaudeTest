#!/usr/bin/env bash
# Every app / library test, headless (no display needed: the Qt tests use the offscreen platform):   tools/run_app_tests.sh
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
export HOME="$(mktemp -d)"          # the tests write configs, backups and reports: never into the real home folder
export DESK_COMPANION_CONFIG="$HOME/cfg.json"
export DESK_COMPANION_GIFS="$HOME/gifs"
fail=0
echo "== lint";           $PY -m pyflakes companion_qt.py deskcompanion_cli.py core ui_qt desk_lib tools firmware/flash.py packaging || fail=1
echo "== lib_test";       $PY -W error tools/lib_test.py || fail=1
for t in appextras_test data_test textops_test scheduler_test bridge_test extras_test scripting_test sysops_test system_test; do
  echo "== $t";           $PY tools/$t.py || fail=1
done
# the engine (no widgets) against the simulated pad
for t in tools/engine/*_test.py tools/engine/selftest.py; do
  echo "== $t";           $PY "$t" || fail=1
done
# the Qt widgets against the simulated pad (offscreen)
for t in shell keys display rules scripts padapp monkey; do
  echo "== qt/$t";        $PY tools/qt/${t}_test.py 2>&1 | grep -v "does not support" ; [ "${PIPESTATUS[0]}" = 0 ] || fail=1
done
echo "== qt layout (5 sizes x 4 languages, light and dark)"
for l in en de es fr; do $PY tools/qt_layout_test.py --lang $l 2>&1 | grep -v "does not support" | tail -3; [ "${PIPESTATUS[0]}" = 0 ] || fail=1; done
$PY tools/qt_layout_test.py --light 2>&1 | grep -v "does not support" | tail -3; [ "${PIPESTATUS[0]}" = 0 ] || fail=1
[ $fail = 0 ] && echo "ALL APP TESTS PASSED" || { echo "SOME TESTS FAILED"; exit 1; }
