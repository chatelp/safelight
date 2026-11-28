# Safelight menu bar app

Native macOS app (SwiftUI `MenuBarExtra`, macOS 14 or later). While it is open it runs
the Safelight hub as its own child process; AI clients open it when they need the hub.
It shows whether Safelight is connected to Lightroom Classic, which AI clients use it,
what they did recently, and lets the user change settings and set things up.

| Path | Contents |
|---|---|
| `Package.swift` | Swift package: `SafelightCore` (logic, tested) and `Safelight` (the app) |
| `Sources/SafelightCore/` | Hub API client and models, activity wording, AI client configuration, running the hub (`HubHost`), plugin install |
| `Sources/Safelight/` | `SafelightApp` (menu bar entry, windows), `AppModel` (state, polling, actions), `StatusView` (popover), `MainWindow` and `LibraryModel` (the main window) |
| `Tests/SafelightCoreTests/` | Swift Testing tests |
| `Design/` | Icons, menu bar template images, colors (see [docs/DESIGN.md](../docs/DESIGN.md)) |

## Build and run

```bash
swift test --package-path app      # tests
./scripts/build_app.sh             # builds build/Safelight.app
open build/Safelight.app
```

Opening the app once registers it with macOS, so relays can then open it by its bundle
id (`open -g -b io.github.chatelp.safelight`). `SAFELIGHT_OPEN_APP=0` stops a relay from
opening it; relays never open it when `SAFELIGHT_HOME` points elsewhere (tests).

`build_app.sh` compiles the asset catalog with `actool`, bundles the Lightroom plugin,
and points the app at this checkout's `.venv/bin/safelight` for starting the hub.
Signing, notarization, an embedded hub and the `.dmg` come with roadmap lot L7.

To check the popover layout without clicking in the menu bar:

```bash
build/Safelight.app/Contents/MacOS/Safelight --render-preview /tmp/preview.png
```

It draws the popover with sample data in light and dark appearance. Native controls
(switches, steppers, menus) show as placeholders in this rendering.

The main window holds lists and scroll views, which that rendering can't draw. To see it
with sample data and no hub, run:

```bash
build/Safelight.app/Contents/MacOS/Safelight --sample-window
```

It opens the window on sample photos and prints its window number, for
`screencapture -l <number>` (which needs the screen recording permission).

## What it does

- **Menu bar icon**: hub stopped, waiting for Lightroom, connected, or working (animated,
  shown only after 300 ms and for at least 1.2 s; no animation with Reduce Motion).
- **Hub**: started with `safelight hub --host app --parent-pid <app>` when the app opens,
  started again if it stops (after three failed starts in a row the app waits for
  Restart Hub), stopped when the app quits. A hub an AI client's relay started on its own
  (app closed or not installed) is replaced by the app's once it has no job running.
- **Popover**: Lightroom version and catalog, AI clients seen in the last 15 minutes,
  the last activity in plain words, read-only mode, confirmation threshold, launch at
  login, restart hub, preferences, hub log.
- **Approvals**: changes waiting for the user's approval show at the top of the popover
  and as a notification with Approve / Decline. The AI client's relay waits up to
  2 minutes for the answer, then asks in the AI client instead.
- **Photos to check**: photos an AI client changed that need the user's eye, with why;
  the checkmark (or All Done) takes them off the list.
- **Main window** (Open Safelight in the menu, ⌘0, or a click on the Dock icon):
  Safelight's three lists of photos, named like its smart collections in Lightroom and
  holding the same photos: to check, to straighten by hand, validated. Lightroom's
  previews (kept on disk, so they show while Lightroom is closed), the reason Safelight
  gave, and an inspector. Validate (one photo or several, ⌘↩) sets the Validated field
  and takes Safelight's color off; above the user's limit the window asks first. Show
  in Lightroom (⌘L) selects the photos in Lightroom's Library. The lists are read again
  every 5 seconds while the window is open.
- **Dock**: Safelight shows in the Dock as well as in the menu bar.
- **Preferences window**: color labels by state (changed by Safelight, to straighten by
  hand, validated), names of Safelight's smart collections in Lightroom, snapshot before
  a geometry change, real names of lenses Lightroom names wrongly. Check its layout with
  `Safelight --render-preferences /tmp/prefs.png`.
- **Set Up menu**: registers Safelight with Claude Desktop, Claude Code, Cursor and
  Codex (each configuration file is backed up first), and installs the Lightroom plugin
  into Lightroom's Modules folder.
