#!/usr/bin/env bash
# Development only: make Lightroom Classic load the plugin straight from this checkout.
# Creates a symlink in Lightroom's Modules folder; Lightroom loads it at its next start.
# Remove it with: scripts/dev_link_plugin.sh --remove
set -euo pipefail
cd "$(dirname "$0")/.."

modules="$HOME/Library/Application Support/Adobe/Lightroom/Modules"
link="$modules/Safelight.lrdevplugin"

if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$link"
  echo "Removed $link"
  exit 0
fi

mkdir -p "$modules"
if [[ -e "$link" && ! -L "$link" ]]; then
  echo "$link exists and is not a symlink; leaving it alone." >&2
  exit 1
fi
ln -sfn "$PWD/plugin/Safelight.lrplugin" "$link"
echo "Linked $link -> $PWD/plugin/Safelight.lrplugin"
echo "Restart Lightroom Classic (or use File > Plug-in Manager > Reload Plug-in) to load it."
