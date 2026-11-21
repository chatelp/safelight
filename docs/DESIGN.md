# Safelight design language

This is the visual identity of **Safelight for Lightroom Classic**: the menu bar
app, the app icon, the website, the README and the docs. Assets and their
sources live in [`app/Design/`](../app/Design/).

## Concept

A darkroom safelight is the lamp you can leave on while you handle paper: its
light lets you see and work without fogging the print. Safelight plays that role
in a Lightroom catalog. It lets an AI assistant work in the catalog without
ruining anything.

The mark is a lamp shade hanging from its cord, throwing a cone of amber light
onto a print. The lamp sits on a cool graphite body, and amber is the only warm
color. Amber in Safelight always means the light is on: Safelight is connected
or working. It is not used as decoration.

Principles:

- **Quiet by default.** Safelight sits in the menu bar and stays out of the way.
  Graphite and system colors carry the interface; amber appears only when
  something is live.
- **Light means work.** The more light in the icon, the more Safelight is doing.
- **Native first.** The app uses system fonts, system colors and SF Symbols. The
  brand shows up in the accent color, the app icon and the menu bar glyph.
- **Plain words.** Say what happened and what to do next, in short sentences.

## Color

Tokens are defined once in [`app/Design/tokens.json`](../app/Design/tokens.json).
`scripts/render_icons.py` turns them into `app/Design/tokens.css` (website,
docs) and into the Xcode color sets in `app/Design/Assets.xcassets`. Change the
JSON, then rerun the script. Do not edit the generated files by hand.

### Palette

Graphite is a cool, slightly blue grey. Amber is the color of an OC
safelight filter.

| Step | Graphite | Amber |
|---|---|---|
| 0 | `#FFFFFF` | |
| 50 | `#F4F5F7` | `#FFF5E6` |
| 100 | `#E3E6EA` | `#FFE6C2` |
| 200 | `#C9CDD3` | `#FFD089` |
| 300 | `#A3A9B3` | `#F9BC62` |
| 400 | `#7D848F` | **`#F2A33A`** (brand amber) |
| 500 | `#5B626D` | `#E08A26` |
| 600 | `#434951` | `#C26F12` |
| 700 | `#30353D` | `#A85A0C` |
| 800 | `#23272E` | `#7E430B` |
| 900 | `#1A1D22` | `#4F2A08` |
| 950 | `#121418` | `#2B1D0C` |

Two semantic hues sit outside the brand: green for success (`#17784F` light,
`#3FB27F` dark) and red for errors (`#C9372C` light, `#F2645A` dark).

### Semantic tokens (website, docs)

CSS variables are prefixed `--sl-`, for example `var(--sl-accent)`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `bg` | graphite 50 | graphite 950 | Page background |
| `surface` | white | graphite 900 | Cards, panels |
| `surface-raised` | white | graphite 800 | Popovers, menus |
| `border` | graphite 100 | graphite 700 | Hairlines, dividers |
| `text` | graphite 950 | graphite 100 | Body text |
| `text-secondary` | graphite 500 | graphite 300 | Captions, metadata |
| `accent` | amber 700 | amber 400 | Links, accent text |
| `accent-fill` | amber 400 | amber 400 | Primary buttons, highlights |
| `on-accent` | graphite 950 | graphite 950 | Text on `accent-fill` |
| `accent-subtle` | amber 50 | amber 950 | Tinted backgrounds |
| `lamp` | amber 400 | amber 400 | Illustrations, the lit lamp |

Contrast (WCAG):

- Body text is above 12:1 in both appearances.
- Secondary text: 6.2:1 on white and 7.2:1 on graphite 900.
- Accent text: amber 700 on white is 5.1:1; amber 400 on graphite 900 is 8.1:1.
- Graphite 950 text on amber 400 is 8.9:1.

Rules:

- Never set amber 400 text on a light background (2.1:1). Use `accent`, which
  switches to amber 700 in light mode.
- Primary buttons are amber with graphite text in both appearances, never amber
  with white text.
- Use one amber element per view, two at most. If everything is lit, nothing
  is.

### Status colors

The hub has three states, plus a "working" variant. The colors below are for
dots and glyphs inside the app. The menu bar icon stays monochrome (see below).

| State | Token | Light | Dark | Meaning |
|---|---|---|---|---|
| Hub stopped | `status-stopped` | graphite 400 | graphite 400 | Safelight is not running. AI clients cannot reach Lightroom. |
| Waiting for Lightroom | `status-waiting` | graphite 500 | graphite 300 | The hub is running; Lightroom Classic is closed or the plugin is off. |
| Connected | `status-connected` | amber 600 | amber 400 | The plugin is connected. AI clients can work in the catalog. |
| Working | `status-working` | amber 600 | amber 400 | A command or job is running. Same color, with motion. |
| Done | `success` | green | green | A job finished. Used in the activity log only. |
| Failed | `error` | red | red | A command failed or the hub could not start. |

Shape always carries the state as well as color: a hollow dot for stopped and
waiting, a filled dot for connected, and a filled dot with a progress
indicator for working. Color is never the only signal.

### In the native app

- Use system colors for text, backgrounds and separators (`.primary`,
  `.secondary`, `Color(nsColor: .windowBackgroundColor)`, materials). The app
  then follows the user's appearance, accent and contrast settings.
- The asset catalog ships these color sets:
  - `AccentColor`: amber 700 in light mode, amber 600 in dark mode. Dark mode
    keeps a darker amber so white control labels stay above 3:1.
  - `LampAmber`: amber 400.
  - `StatusStopped`, `StatusWaiting`, `StatusConnected` and `StatusWorking`.
  - `Success` and `Error`.
- Prefer `Color("StatusConnected")` over hard-coded values, so the tokens stay
  the single source.

## Typography

### Native app

The app uses the system font (SF Pro) only, through SwiftUI text styles, so
Dynamic Type and accessibility sizes work. Do not bundle fonts.

| Role | SwiftUI | Notes |
|---|---|---|
| Popover title | `.headline` | "Safelight" and the state |
| Body | `.body` | 13 pt on macOS |
| Secondary, timestamps | `.callout` or `.caption` with `.secondary` | |
| Section labels | `.caption` `.weight(.semibold)` | Sentence case, not all caps |
| Paths, ports, tool names | `.body.monospaced()` (SF Mono) | `127.0.0.1:58763`, `develop.set` |
| Counts, durations | `.monospacedDigit()` | Columns that update stay still |

### Website and docs

- **Geist** for headings and text, **Geist Mono** for code, commands and
  numbers. Both are free on Google Fonts. They are close to SF Pro in
  proportion, so the site and the app look like one family.
- Fallbacks: `Geist, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`
  and `"Geist Mono", ui-monospace, "SF Mono", Menlo, monospace`.
- Scale (px): 13, 15 (body), 18, 22, 28, 40. Headings use weight 600 and
  -0.01em tracking. Body text uses weight 400 with a line height of 1.55. Keep
  lines near 65 characters.
- The README uses GitHub's own fonts. It adds only the banner.

## App icon

Masters: [`app/Design/masters/app-icon.svg`](../app/Design/masters/app-icon.svg)
and [`app-icon-small.svg`](../app/Design/masters/app-icon-small.svg).

- The icon follows the macOS (Big Sur and later) grid: a 1024 × 1024 canvas, an
  824 × 824 body at (100, 100) with continuous corners, and a soft drop shadow
  in the margin. The script builds the shadow from stacked layers, so it renders
  without SVG filters.
- Detailed master (64 px and up): the cord, a metal shade, the bulb, the cone
  of light, a pool of light and the print with a faint image.
- Small master (16 and 32 px): no cord and no picture on the print. The shade
  is a flat light grey, and the cone and print are larger and brighter. The
  render script picks it automatically for slots of 32 px or less.
- Never put letters in the icon, and never "Lr", "Lightroom" or anything that
  imitates Adobe's square logos.

## Menu bar icon

The menu bar icon is a **template image**: black and alpha only. macOS tints it
for light and dark menu bars and for the selected state. It is drawn on an
18 × 18 pt canvas, with content about 15 pt tall. The asset catalog has @1x and @2x
PNGs, and the masters are in `app/Design/masters/menubar-*.svg`.

The glyph is the app icon's lamp, from top to bottom:

- a short cord, 2 pt long;
- the shade: 5.2 pt wide at the top, 14.8 pt at the rim, 4.2 pt tall, so
  it is wide and flat like a lamp shade;
- a 1.6 pt gap;
- the cone of light: 6 pt wide at the top and 12.4 pt at the bottom, always
  narrower than the shade's rim.

These proportions keep it from reading as a fir tree. Two triangles stacked
with no gap, the lower one wider, look like a tree at 18 pt. The cord, the gap,
a wide shade and a cone narrower than the shade make it read as a lamp hanging
from the ceiling.

The silhouette never changes size. The amount of ink shows the state:

| State | Asset | Drawing |
|---|---|---|
| Hub stopped | `MenuBarStopped` | Shade and cone as outlines. The lamp is off. |
| Hub running, waiting for Lightroom | `MenuBarWaiting` | Shade filled, cone outlined. Power is on, but no light yet. |
| Lightroom connected | `MenuBarConnected` | Shade and cone filled. The light is on. |
| Working | `MenuBarWorking1` to `MenuBarWorking3` | The cone fills from top to bottom, frame by frame. |

Behavior:

- Show **Working** only while a command has been running for more than 300 ms,
  and keep it at least 1.2 s, so quick calls don't make the icon flicker. Cycle
  frames 1 → 2 → 3 at 0.35 s each.
- When Reduce Motion is on, do not animate. Show `MenuBarConnected`; the
  popover still shows the running job.
- An error (for example the hub cannot bind its port) uses `MenuBarStopped`.
  The popover explains what happened. The menu bar never turns red.
- Set `.accessibilityLabel` and a tooltip on the status item: "Safelight: hub
  stopped", "Safelight: waiting for Lightroom", "Safelight: connected",
  "Safelight: working".

In SwiftUI:

```swift
MenuBarExtra {
    StatusView()
} label: {
    Image(state.menuBarImageName)   // "MenuBarConnected", …
        .accessibilityLabel(state.accessibilityLabel)
}
.menuBarExtraStyle(.window)
```

## Iconography in the app

Use SF Symbols for everything except the brand glyph. Use one weight
(`.regular`) at the default scale. Use monochrome rendering for controls and
hierarchical rendering for larger status illustrations.

| Concept | SF Symbol |
|---|---|
| AI client (Claude Desktop, Cursor…) | `sparkle` or `app.connected.to.app.below.fill` |
| Lightroom catalog | `photo.stack` |
| Plugin | `puzzlepiece.extension` |
| Activity log | `list.bullet.rectangle` |
| Pause AI access | `pause.circle` |
| Settings | `gearshape` |
| Confirmation needed | `hand.raised` |
| Error | `exclamationmark.triangle` (colored `Error`) |
| Done | `checkmark.circle` (colored `Success`) |

Do not draw the lamp in place of SF Symbols inside the interface. It appears in
the menu bar, in the app icon and in empty states only.

## Spacing and shape

- Use a 4 pt grid with spacing steps of 4, 8, 12, 16, 24 and 32. The popover
  has 16 pt padding with 12 pt between sections. List rows are at least 24 pt
  tall.
- The popover is about 320 pt wide and uses `.menuBarExtraStyle(.window)` with
  the system material. Don't paint a custom background.
- Corner radii: let the system handle windows and buttons. Inner cards use
  8 pt, chips and pills use full rounding. On the website, use 6 px for inputs
  and code, 10 px for cards and 16 px for large panels.
- Use separators, not boxes, to split popover sections. Reserve fills and
  borders for the one element that needs to stand apart.

## Voice and tone

Safelight talks like a careful assistant in someone else's darkroom: calm,
brief and specific.

- Use plain English and short sentences, in sentence case. Don't end buttons or
  labels with a period.
- Name things the way photographers see them: photos, catalog, collections,
  keywords, Develop settings. Avoid MCP, relay, long-poll and JSON in the UI.
  They belong in the docs.
- Say what happened, then what to do. Don't apologize, don't add exclamation
  marks, don't be cute.
- Use numbers, not adjectives: "24 photos", not "several photos".
- Buttons say exactly what happens: "Install Plugin", "Pause AI Access",
  "Open Activity Log". Title case for buttons and menu items, as macOS does.

| Do | Don't |
|---|---|
| Waiting for Lightroom Classic | Oops! Lightroom isn't connected |
| Set Exposure +0.30 on 24 photos | Successfully applied changes |
| Claude wants to edit 312 photos. Allow? | Bulk operation requested by MCP client |
| Port 58763 is in use. Quit the other app or choose another port in Settings. | Error: EADDRINUSE |
| Safelight can't reach the plugin. Restart Lightroom Classic. | Something went wrong |

## Trademarks

Safelight is not affiliated with Adobe. Never use the Lightroom name or logo in
the mark, never use "Lr" letterforms, and never use Adobe's blue or purple
rounded square. In text, write "Lightroom Classic" only to say what Safelight
works with, as in the subtitle "for Lightroom Classic".

## Files and regeneration

| Path | Contents |
|---|---|
| `app/Design/masters/` | SVG masters: app icon (detailed and small), menu bar states, README banner |
| `app/Design/tokens.json` | Color tokens, the single source |
| `app/Design/Assets.xcassets/` | Generated. Drop into the Xcode project: `AppIcon`, `MenuBar*`, color sets |
| `app/Design/Safelight.icns` | Generated on macOS with `iconutil` |
| `app/Design/app-icon-1024.png`, `banner.png`, `tokens.css` | Generated |

To regenerate the assets, run:

```bash
uv run python scripts/render_icons.py
```

The script needs the Cairo library (`brew install cairo`). Commit the masters
and the generated files together.
