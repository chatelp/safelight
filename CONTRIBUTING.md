# Contributing

## Requirements

- macOS with Adobe Lightroom Classic 14 or later (only needed for live testing)
- [uv](https://docs.astral.sh/uv/)
- `luajit` or `luac5.1` for the Lua syntax check (`brew install luajit`)

## Setup

```bash
uv sync
```

## Checks

Run all of these before opening a pull request; CI runs the same.

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest
./scripts/check_lua.sh
```

## Running the MCP server

```bash
uv run safelight mcp
```

It speaks MCP over stdio, so it is meant to be launched by an MCP client.

## Versioning

Change the version only in `VERSION`, then run:

```bash
uv run python scripts/sync_version.py
```

## Lightroom plugin

The plugin lives in `plugin/Safelight.lrplugin`. To try it, add that folder in
Lightroom Classic under **File > Plug-in Manager > Add**. Its log is written to
`safelight.log` in the macOS temporary folder; the path is shown under
**File > Plug-in Extras > Safelight Status**.
