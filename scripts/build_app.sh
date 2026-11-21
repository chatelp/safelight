#!/usr/bin/env bash
# Builds the menu bar app into build/Safelight.app (development build, not signed).
#
#   ./scripts/build_app.sh            # release build
#   ./scripts/build_app.sh --debug    # debug build
#
# The app bundles the Lightroom plugin and, for development, points at this checkout's
# `safelight` command (.venv/bin/safelight). Release packaging (embedded hub, signing,
# notarization, .dmg) is roadmap lot L7.
set -euo pipefail
cd "$(dirname "$0")/.."

configuration=release
[[ "${1:-}" == "--debug" ]] && configuration=debug

version="$(tr -d '[:space:]' < VERSION)"
numeric="$(echo "$version" | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+')"
app="build/Safelight.app"

swift build --package-path app -c "$configuration" --product Safelight
binary="$(swift build --package-path app -c "$configuration" --show-bin-path)/Safelight"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$binary" "$app/Contents/MacOS/Safelight"
cp app/Design/Safelight.icns "$app/Contents/Resources/Safelight.icns"
cp -R plugin/Safelight.lrplugin "$app/Contents/Resources/Safelight.lrplugin"

# Asset catalog: menu bar template images, status colors, app icon.
xcrun actool app/Design/Assets.xcassets \
  --compile "$app/Contents/Resources" \
  --platform macosx \
  --minimum-deployment-target 14.0 \
  --app-icon AppIcon \
  --output-partial-info-plist build/assets-info.plist \
  --errors --warnings > /dev/null

cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>io.github.chatelp.safelight</string>
  <key>CFBundleName</key><string>Safelight</string>
  <key>CFBundleDisplayName</key><string>Safelight</string>
  <key>CFBundleExecutable</key><string>Safelight</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${numeric}</string>
  <key>CFBundleVersion</key><string>${version}</string>
  <key>CFBundleIconFile</key><string>Safelight</string>
  <key>CFBundleIconName</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>Safelight is not affiliated with Adobe.</string>
  <key>SafelightCLIPath</key><string>${PWD}/.venv/bin/safelight</string>
</dict>
</plist>
PLIST

# Ad-hoc signature so macOS accepts the bundle locally (Developer ID signing is lot L7).
codesign --force --sign - "$app" > /dev/null 2>&1 || true
echo "Built $app ($version, $configuration)"
