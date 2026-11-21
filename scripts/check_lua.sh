#!/usr/bin/env bash
# Syntax-check every Lua file of the Lightroom plugin (Lightroom runs Lua 5.1), then run
# the pure-Lua tests in tests/lua.
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v luac5.1 >/dev/null 2>&1; then
  check() { luac5.1 -p "$1"; }
  run=lua5.1
elif command -v luajit >/dev/null 2>&1; then
  check() { luajit -b "$1" /dev/null; }
  run=luajit
else
  echo "Need luac5.1 or luajit to check Lua syntax." >&2
  exit 1
fi

status=0
while IFS= read -r -d '' file; do
  if ! check "$file"; then
    status=1
  fi
done < <(find plugin tests/lua -name '*.lua' -print0)

if [[ $status -ne 0 ]]; then
  exit $status
fi
echo "Lua syntax OK"

# Pure-Lua tests (modules that do not need the Lightroom SDK).
for test in tests/lua/test_*.lua; do
  "$run" "$test" || status=1
done
exit $status
