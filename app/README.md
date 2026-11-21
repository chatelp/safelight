# Safelight menu bar app

Native macOS app (SwiftUI `MenuBarExtra`, macOS 14 or later) that shows whether
Safelight is running and connected to Lightroom Classic, which AI clients use it,
what they did recently, and lets the user change settings and set things up.

| Path | Contents |
|---|---|
| `Package.swift` | Swift package: `SafelightCore` (logic, tested) and `Safelight` (the app) |
| `Sources/SafelightCore/` | Hub API client and models, activity wording, AI client configuration, hub start/stop, plugin install |
| `Sources/Safelight/` | `SafelightApp` (menu bar entry), `AppModel` (state, polling, actions), `StatusView` (popover) |
| `Tests/SafelightCoreTests/` | Swift Testing tests |
| `Design/` | Icons, menu bar template images, colors (see [docs/DESIGN.md](../docs/DESIGN.md)) |

## Build and run

```bash
swift test --package-path app      # tests
./scripts/build_app.sh             # builds build/Safelight.app
open build/Safelight.app
```

`build_app.sh` compiles the asset catalog with `actool`, bundles the Lightroom plugin,
and points the app at this checkout's `.venv/bin/safelight` for starting the hub.
Signing, notarization, an embedded hub and the `.dmg` come with roadmap lot L7.

To check the popover layout without clicking in the menu bar:

```bash
build/Safelight.app/Contents/MacOS/Safelight --render-preview /tmp/preview.png
```

It draws the popover with sample data in light and dark appearance. Native controls
(switches, steppers, menus) show as placeholders in this rendering.

## What it does

- **Menu bar icon**: hub stopped, waiting for Lightroom, connected, or working (animated,
  shown only after 300 ms and for at least 1.2 s; no animation with Reduce Motion).
- **Popover**: Lightroom version and catalog, AI clients seen in the last 15 minutes,
  the last activity in plain words, read-only mode, confirmation threshold, launch at
  login, start/stop hub, hub log.
- **Set Up menu**: registers Safelight with Claude Desktop, Claude Code, Cursor and
  Codex (each configuration file is backed up first), and installs the Lightroom plugin
  into Lightroom's Modules folder.
