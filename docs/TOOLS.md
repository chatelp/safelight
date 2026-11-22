# Safelight tools

The tools an AI client (Claude, Cursor, Codex…) sees when Safelight is connected. They
are built on the [plugin commands](PLUGIN_COMMANDS.md) and go through the hub's policy:
tools marked **read-only** never change the catalog, so clients may run them without
asking; the others respect read-only mode and ask for approval past the confirmation
threshold or for changes that are hard to undo.

Photos are identified by id. Tools that work on photos take `photo_ids`, or use the
photos selected in Lightroom (`scope: "selected"`, the default) or the active photo
(`scope: "active"`).

| Tool | Kind | What it does |
|---|---|---|
| `safelight_status` | read-only | Whether Lightroom is connected; Lightroom version, catalog, module, active photo, selection size, read-only mode and confirmation threshold. Never fails. |
| `safelight_job` | read-only | Follow a long job (state, progress, result). |
| `safelight_cancel_job` | change | Stop a job at its next step. |
| `get_photos` | read-only | Describe photos (file, date, rating, flag, label, camera, lens, exposure, GPS, title, caption, keywords). |
| `search_photos` | read-only | Find photos in the whole catalog with simple filters: text, keywords, title, caption, file name, folder, collection, camera, lens, rating range, flag, label, capture dates or last N days, ISO range, format, GPS, edited, cropped, orientation; plus focal length, aperture and shutter speed ranges, checked on the results (up to 5,000 photos scanned). |
| `preview_photo` | read-only | The photo as Lightroom renders it now, as an image. |
| `contact_sheet` | read-only | Up to 48 photos in one numbered image with stars, flags and labels, plus a legend mapping numbers to ids. For culling and comparing. |
| `analyze_photos` | read-only | Help cull a shoot: groups bursts and near-duplicates and picks the best frame of each (sharpest, well exposed, flagged or rated), and lists photos that look blurred, have blown highlights or crushed shadows, are tilted (with the angle), or keep a thin empty border left by a perspective correction or HDR merge. Frames stacked under another photo (HDR brackets, panorama sources) are left out unless asked. Lightroom's search already returns only the top of each stack. Works on Lightroom's previews; heuristics to review, not verdicts. |
| `get_develop_settings` | read-only | Develop settings, masks with their adjustments, and snapshots of a photo. |
| `edit_develop` | change | Set develop sliders on one or many photos, absolute or relative; out-of-range values are limited and reported. |
| `copy_develop_settings` | change | Sync settings from one photo to others (everything but crop, masks and red eye, or chosen settings). |
| `auto_develop` | change | Auto Tone, Auto White Balance, or reset. |
| `apply_preset` | change | Apply one of the user's develop presets, with an amount. |
| `snapshot` | change, delete | Create, apply or delete a named snapshot. |
| `create_mask` | change | AI mask of the subject, sky, background or people, optionally adjusted right away. |
| `edit_mask` | change | Change a mask's local adjustments. |
| `change_mask` | change, delete | Invert or delete a mask. |
| `set_metadata` | change | Rating, flag, label, title, caption, headline, alt text, copyright, creator, location, event, GPS. |
| `edit_keywords` | change | Add keywords (hierarchies created as needed) or remove them. |
| `list_catalog_items` | read-only | Collections, keywords or develop presets, optionally filtered by name. |
| `manage_collection` | change, delete | Create collections, smart collections and sets; add, remove or list photos; delete a collection. |
| `organize_photos` | change | Rotate photos or create virtual copies. |
| `export_photos` | change | Export with format, size, quality, color space, sharpening and metadata options; returns file paths. |

## Approvals

When a change needs approval, Safelight asks the user directly if the AI client
supports it (MCP elicitation). Otherwise the tool answers
`state: "confirmation_required"` with a summary and a single-use token; the AI shows the
summary and, only after the user approves, calls the tool again with
`confirmation_token`. The AI can neither change the threshold nor turn off read-only
mode.
