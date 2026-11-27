# Working on Safelight

Notes for AI coding agents (and humans) building Safelight. What the product does is in
[README.md](README.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); setup and checks
are in [CONTRIBUTING.md](CONTRIBUTING.md). This file holds what those don't: how we work.

## Layout

- `src/safelight/`: the MCP relay (`mcp_server.py`, tools), the hub (`hub.py`, policy in
  `policy.py`, settings in `settings.py`), photo analysis and straightening
  (`analysis.py`, `upright.py`), recipes and guides (`knowledge.py`, `data/`).
- `plugin/Safelight.lrplugin/`: the Lightroom Classic plugin (Lua 5.1).
- `app/`: the SwiftUI menu bar app; it runs the hub while it is open.
- `src/safelight/devtools/`: a fake plugin and fake catalog, so the tools can be tested
  without Lightroom. Keep the fake catalog's answers identical to the plugin's.

## Workflow

- Docs and small fixes go straight to `main`. Code goes through a branch and a pull
  request; CI must pass (ruff, ruff format, pytest on 3.11 to 3.13, Lua syntax, Swift
  tests and app build) before merging. The maintainer merges unless they said otherwise.
- Run every check in CONTRIBUTING.md before pushing. `ruff format` is checked in CI.
- Ask a second agent to review a change before merging it, giving it the diff and what
  to look for, not your conclusions.

## Testing against the real Lightroom

- The plugin is loaded from this checkout through Lightroom's Plug-in Manager. After a
  Lua change, reload it there (Plug-in Manager, Safelight, Reload). A new Lua file is
  only seen after restarting Lightroom: a reload then fails with "No script by the name
  …" and disables the plugin (enable it again after the restart). A change to
  `MetadataDefinition.lua` needs a new `schemaVersion`.
- The catalog is the maintainer's real one. Read freely (searches, previews). Write only
  on test photos: virtual copies named "Safelight Test …" in the "Safelight Tests"
  collection, unless the maintainer says otherwise. Never overwrite their corrections.
- After reloading the plugin, `uv run python scripts/check_photo_fields.py` checks the
  photo fields and the Validated guard on a test copy, and puts it back as it was.
- `uv run safelight call <command> '<json>'` runs one plugin command through the hub;
  `uv run safelight status` shows the hub and Lightroom.
- The menu bar app hosts the hub: after changing hub code, restart the app (or Restart
  Hub) so the hub runs the new code. Build it with `./scripts/build_app.sh`.

## Rules that are easy to break

- Never commit the Lightroom Classic SDK (`vendor/` is git-ignored: Adobe's license).
- The MCP server uses MCP SDK 2.x (`mcp.server.mcpserver.MCPServer`); tool results are
  pydantic models (TypedDict breaks on Python 3.11).
- Every plugin command must have a policy in `policy.py` and its parameters in
  `plugin/Safelight.lrplugin/commands.json` (tests check both). The plugin refuses an
  undeclared parameter and never falls back to the selection: say `uuids` or `target`. Commands
  that change edits are listed in `EDITING_COMMANDS` and in `CommandRouter.lua`
  (`EDITING`); a test checks they match.
- Lua: use `LrTasks.pcall`, not `pcall`, around SDK calls that may yield.
- The version lives only in `VERSION`; run `uv run python scripts/sync_version.py`.
- Wording in the product follows docs/DESIGN.md: plain words, no jargon.

## Where new knowledge goes

Use the order in docs/ARCHITECTURE.md ("Where knowledge lives"): rules that must hold go
into code, facts about one photo into the catalog, the user's choices into settings,
methods into recipes, background into guides. Knowledge about building Safelight goes
here or in the docs.
