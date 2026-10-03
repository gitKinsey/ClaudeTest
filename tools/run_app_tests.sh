#!/usr/bin/env bash
# Every app / library test, headless (needs xvfb-run for the GUI ones):   tools/run_app_tests.sh
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
fail=0
echo "== lint";           $PY -m pyflakes companion_app.py deskcompanion_cli.py desk_lib tools firmware/flash.py packaging || fail=1
echo "== lib_test";       $PY -W error tools/lib_test.py || fail=1
for t in textops_test scheduler_test bridge_test extras_test app_selftest macro_test giflib_test layers_test profiles_test info_test features_test compat_test core_test automation_test fw13_test; do
  echo "== $t"
  if command -v xvfb-run >/dev/null; then xvfb-run -a $PY tools/$t.py || fail=1; else $PY tools/$t.py || fail=1; fi
done
[ $fail = 0 ] && echo "ALL APP TESTS PASSED" || { echo "SOME TESTS FAILED"; exit 1; }
