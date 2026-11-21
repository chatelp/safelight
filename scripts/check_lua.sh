#!/usr/bin/env bash
# Syntax-check every Lua file of the Lightroom plugin (Lightroom runs Lua 5.1).
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v luac5.1 >/dev/null 2>&1; then
  check() { luac5.1 -p "$1"; }
elif command -v luajit >/dev/null 2>&1; then
  check() { luajit -b "$1" /dev/null; }
else
  echo "Need luac5.1 or luajit to check Lua syntax." >&2
  exit 1
fi

status=0
while IFS= read -r -d '' file; do
  if ! check "$file"; then
    status=1
  fi
done < <(find plugin -name '*.lua' -print0)

if [[ $status -eq 0 ]]; then
  echo "Lua syntax OK"
fi
exit $status
