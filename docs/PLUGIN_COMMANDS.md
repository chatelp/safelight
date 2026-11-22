# Lightroom plugin commands

The commands the Safelight plugin runs inside Lightroom Classic. AI clients do not call
them directly: the MCP tools (roadmap lot L4) are built on top of them. During
development you can run any of them with `uv run safelight call <command> '<json>'`.

## Conventions

- **Photos are addressed by UUID** (`uuid` in every photo description). Commands that
  work on photos take one of:
  - `"uuids": ["…", "…"]` — those photos; unknown ids are listed in `missing`;
  - `"target": "active"` — the active photo;
  - nothing — the photos selected in Lightroom.
- **Dry run.** Commands that change photos accept `"dry_run": true` and only answer
  `{affected, summary}`. The hub uses this before asking the user to confirm a large
  change (see `src/safelight/policy.py`).
- **Batches** run in chunks of 50 photos inside catalog write gates, report progress,
  stop between chunks when cancelled, and create one Lightroom undo step per chunk.
  Answers contain `requested`, `updated`, `failures` (`uuid`, `error`) and `missing`.
- **Develop UI path.** Commands marked *(Develop)* open the photo in the Develop module
  (they change what Lightroom shows) and work one photo at a time.
- **Values** are as shown in Lightroom (Exposure in EV, most sliders −100…100).

Policy column: R read-only · M changes the catalog (dry run, confirmation past the
threshold) · S changes the catalog without a dry run · D destructive (always confirmed).

## Catalog and photos

| Command | Policy | Params | Answer |
|---|---|---|---|
| `catalog.info` | R | — | Lightroom version, catalog path, current module, active photo, selection size |
| `photos.get` | R | photos, `limit`, `offset` | photo descriptions (file, format, size, capture date, rating, flag, label, camera, lens, exposure, GPS, title, caption, keywords, position in a stack) |
| `photos.search` | R | `search` (one criterion, or `{combine, items: [...]}`), `sort`, `ascending`, `limit`, `offset` | matching photo descriptions and `total` |
| `photos.preview` | R | photo, `size` (64–4096, a minimum) | base64 JPEG rendered by Lightroom with the edits |
| `photos.rotate` | M | photos, `direction` (`left`/`right`) | batch result |
| `photos.create_virtual_copies` | M | photos, `name` | ids of the new copies (one per photo) |

## Collections

| Command | Policy | Params | Answer |
|---|---|---|---|
| `collections.list` | R | — | every collection, smart collection and set, with `id`, `kind`, `parent_id` |
| `collections.photos` | R | `collection_id`, `limit`, `offset` | photo descriptions |
| `collections.create` | S | `name`, `parent_id`, `kind` (`collection`, `collection_set`, `smart_collection`), `search` | the new collection |
| `collections.add` / `collections.remove_photos` | M | `collection_id`, photos | count |
| `collections.delete` | D | `collection_id` | deletes the collection (photos stay in the catalog) |

## Metadata and keywords

| Command | Policy | Params | Answer |
|---|---|---|---|
| `metadata.set` | M | photos, `fields`: `rating` (0–5), `pick` (−1, 0, 1), `label` (`red`…`purple`, `none`), `title`, `caption`, `headline`, `copyright`, `creator`, `location`, `city`, `state`, `country`, `country_code`, `event`, `alt_text`, `extended_description`, `gps` (`{latitude, longitude}` or `false`), `gps_altitude` | batch result |
| `keywords.list` | R | `query`, `limit` | keyword paths (`A > B > C`) |
| `keywords.add` | M | photos, `keywords` (paths; missing ones are created) | batch result, `keywords_not_created` |
| `keywords.remove` | M | photos, `keywords` (a name matches anywhere, a path exactly) | batch result, `keywords_not_found` |

## Develop

| Command | Policy | Params | Answer |
|---|---|---|---|
| `develop.get` | R | photo, `keys` (optional) | settings (masks excluded; plain names such as `Exposure` map to the Process Version 2012 keys) |
| `develop.apply` | M | photos, `settings`, `relative`, `history_name`, `snapshot` | batch result; values clamped to the photo's slider ranges, with `notes`. With `snapshot`, each photo first gets a snapshot of that name, unless it already has one |
| `develop.copy` | M | `from` (uuid), photos, `keys` | copies the settings (by default everything except crop, masks and red eye) |
| `develop.auto_tone` | M | photos *(Develop)* | count |
| `develop.auto_white_balance` | M | photos | batch result |
| `develop.reset` | M | photos *(Develop)* | count (undoable in history) |
| `presets.list` | R | — | develop presets with `uuid`, `name`, `folder` |
| `develop.apply_preset` | M | `preset_uuid`, photos, `amount` (0–200) | batch result |
| `snapshots.list` | R | photo | snapshots (`id`, `name`) |
| `snapshots.create` | M | photos, `name`, `update_existing` | batch result |
| `snapshots.apply` | S | photo, `snapshot_id` or `name` | applied snapshot |
| `snapshots.delete` | D | photo, `snapshot_id` or `name` | deleted snapshot |

## Masks

| Command | Policy | Params | Answer |
|---|---|---|---|
| `masks.list` | R | photo | masks with `id`, `name`, components (type, inverted) and non-zero adjustments |
| `masks.adjust` | M | photo, `mask_id` (id or name), `adjustments` (`Exposure`, `Contrast`, `Highlights`, `Shadows`, `Whites`, `Blacks`, `Texture`, `Clarity`, `Dehaze`, `Temperature`, `Tint`, `Hue`, `Saturation`, `Sharpness`, `Noise`, `Moire`, `Defringe`, `Grain`, `Amount`), `relative` | the updated mask — works on any photo without the Develop module |
| `masks.create` | M | photo, `kind` (`subject`, `sky`, `background`, `people`) *(Develop)* | id of the new mask |
| `masks.invert` | M | photo, `mask_id` *(Develop)* | — |
| `masks.delete` | D | photo, `mask_id` *(Develop)* | — |

## Export

| Command | Policy | Params | Answer |
|---|---|---|---|
| `export.photos` | M | photos, `destination` (absolute folder), `format` (`JPEG`, `TIFF`, `PNG`, `DNG`, `ORIGINAL`), `quality`, `long_edge_px`, `color_space` (`sRGB`, `AdobeRGB`, `DisplayP3`, `ProPhotoRGB`), `if_exists` (`rename` default, `overwrite`, `skip`), `sharpen` (`screen`, `matte`, `glossy`), `strip_metadata`, `remove_location`, `bit_depth` (TIFF) | absolute path of every exported file, failures |

## System and inspection

| Command | Policy | Purpose |
|---|---|---|
| `system.ping`, `system.list_commands` | R | health check, list of commands |
| `system.progress_test` | R | harmless long command to check progress and cancellation |
| `inspect.*` | R | development: what Lightroom exposes (`inspect.app`, `inspect.develop_settings`, `inspect.raw_metadata`, `inspect.thumbnails`, `inspect.develop_controller`, `inspect.develop_ranges` *(Develop)*) |
