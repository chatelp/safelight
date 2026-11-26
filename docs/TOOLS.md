# Safelight tools

The tools an AI client (Claude, Cursor, Codex…) sees when Safelight is connected. They
are built on the [plugin commands](PLUGIN_COMMANDS.md) and go through the hub's policy:
tools marked **read-only** never change the catalog, so clients may run them without
asking; the others respect read-only mode and ask for approval past the confirmation
threshold or for changes that are hard to undo. When the Safelight menu bar app is
open, the user answers there; otherwise the AI client asks.

Photos are identified by id. Tools that work on photos take `photo_ids`, or use the
photos selected in Lightroom (`scope: "selected"`, the default) or the active photo
(`scope: "active"`).

| Tool | Kind | What it does |
|---|---|---|
| `safelight_status` | read-only | Whether Lightroom is connected; Lightroom version, catalog, module, active photo, selection size, read-only mode, confirmation threshold, the user's color for changed photos and snapshot choice. Never fails. |
| `safelight_guide` | read-only | Safelight's method for a common job (`straighten-exhibition-folder`, `sort-duplicates`, with an optional `folder`) or background (`guided-upright`, `shift-lenses`). The same texts are offered as MCP prompts and resources (`safelight://guides/…`). |
| `safelight_job` | read-only | Follow a long job (state, progress, result). |
| `safelight_cancel_job` | change | Stop a job at its next step. |
| `get_photos` | read-only | Describe photos (file, date, rating, flag, label, camera, lens, exposure, GPS, title, caption, keywords). A lens the user renamed in Safelight's settings shows its real name, with `lens_in_lightroom`. Also Safelight's fields: `validated`, `geometry_by` (who last corrected the perspective), `reject_reason`. |
| `search_photos` | read-only | Find photos in the whole catalog with simple filters: text, keywords, title, caption, file name, folder, collection, camera, lens (Lightroom's name, or the real name the user gave it), rating range, flag, label, capture dates or last N days, ISO range, format, GPS, edited, cropped, orientation; plus focal length, aperture and shutter speed ranges, validated, and who corrected the geometry, checked on the results (up to 5,000 photos scanned). |
| `preview_photo` | read-only | The photo as Lightroom renders it now, as an image. |
| `contact_sheet` | read-only | Up to 48 photos in one numbered image with stars, flags and labels, plus a legend mapping numbers to ids. For culling and comparing. |
| `analyze_photos` | read-only | Help cull a shoot: groups bursts and near-duplicates and picks the best frame of each (sharpest, well exposed, flagged or rated), and lists photos that look blurred, have blown highlights or crushed shadows, are tilted (with the angle), or keep a thin empty border left by a perspective correction or HDR merge. Frames stacked under another photo (HDR brackets, panorama sources) are left out unless asked. Lightroom's search already returns only the top of each stack. Works on Lightroom's previews; heuristics to review, not verdicts: sharpness alone does not choose the best frame (lens flare, passers-by, reflections). |
| `straighten_photos` | changes | Straightens verticals (and the horizontal edges of prints or frames facing the camera) with Lightroom's Guided Upright, each photo with its own correction: keeps a snapshot "Before Safelight straighten" (unless the user turned snapshots off), resets earlier transform corrections and the crop, finds straight lines on a fresh preview, then writes up to four Upright guides and the matching transform. The correction is a camera rotation, so prints and walls keep their proportions; with two vertical and two horizontal guides it is made exact, like Lightroom's own, keeping the proportions of the rotation. Empty corners are not cropped. Photos the user validated are never changed (listed in `validated_skipped`); photos that already have an Upright correction are skipped unless `replace_existing_upright` is set: it may be the user's own work. The photos it changes are marked "Straightened by Safelight" in Safelight's Geometry field and get the user's color label (purple by default; `label` overrides it, `none` for no label); a photo with another label keeps it. Rotated photos are skipped for now. A photo that cannot be straightened (no line found, error, cancelled) gets its earlier transform and crop back. Photos straightened less precisely than 0.3° come back with warnings; those, and the photos left to the user, also go to the app's list of photos to check. With `chosen_lines` (photo id -> 2 to 4 numbers from `straighten_lines`), it rewrites the correction of photos it straightened before from the lines the AI chose, without rendering them again; a photo whose correction was changed since in Lightroom is skipped, and one whose chosen verticals disagree with the automatic ones by more than 0.45° is left unchanged and listed in `left_to_user`. |
| `straighten_lines` | read-only | The straight lines found on photos `straighten_photos` straightened, numbered and drawn on the preview it analysed (without correction), with the guides in use highlighted and a list of the lines. For the AI to choose the guides by eye: long edges of works and walls, never shadows, lines inside prints or ceiling rails. Reads what Safelight kept on the Mac (see [PRIVACY.md](../PRIVACY.md)). |
| `get_develop_settings` | read-only | Develop settings, masks with their adjustments, and snapshots of a photo. |
| `edit_develop` | change | Set develop sliders on one or many photos, absolute or relative; out-of-range values are limited and reported. |
| `copy_develop_settings` | change | Sync settings from one photo to others (everything but crop, masks and red eye, or chosen settings). |
| `auto_develop` | change | Auto Tone, Auto White Balance, or reset. |
| `apply_preset` | change | Apply one of the user's develop presets, with an amount. |
| `snapshot` | change, delete | Create (on many photos), apply or delete (one photo at a time) a named snapshot. |
| `create_mask` | change | AI mask of the subject, sky, background or people, optionally adjusted right away. |
| `edit_mask` | change | Change a mask's local adjustments. |
| `change_mask` | change, delete | Invert or delete a mask. |
| `set_metadata` | change | Rating, flag, label, title, caption, headline, alt text, copyright, creator, location, event, GPS; and Safelight's fields: `validated` (the user approves the photo's edits: Safelight never changes them), `reject_reason`, `geometry_by`. |
| `edit_keywords` | change | Add keywords (hierarchies created as needed) or remove them. |
| `list_catalog_items` | read-only | Collections, keywords or develop presets, optionally filtered by name. |
| `manage_collection` | change, delete | Create collections, smart collections and sets; add, remove or list photos; delete a collection. |
| `organize_photos` | change | Rotate photos or create virtual copies. |
| `export_photos` | change | Export with format, size, quality, color space, sharpening and metadata options; returns file paths. Existing files are kept (renamed or skipped) unless `if_exists` is `overwrite`, which always needs the user's approval. |

## Approvals

When a change needs approval, Safelight asks the user directly if the AI client
supports it (MCP elicitation). Otherwise the tool answers
`state: "confirmation_required"` with a summary and a single-use token; the AI shows the
summary and, only after the user approves, calls the tool again with
`confirmation_token`. The AI can neither change the threshold nor turn off read-only
mode.
