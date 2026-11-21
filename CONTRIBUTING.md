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

## Lightroom Classic SDK

Adobe's license does not allow redistributing the SDK, so it is not in the
repository. Download it from the
[Adobe Developer Console](https://developer.adobe.com/console/servicesandapis)
(Downloads > Lightroom Classic) and unzip it into
`vendor/lightroom-classic-sdk-<version>/`, which git ignores. Then search the
API Reference from the command line:

```bash
uv run python scripts/sdk_inventory.py list LrDevelopController
uv run python scripts/sdk_inventory.py show LrPhoto requestJpegThumbnail
uv run python scripts/sdk_inventory.py since 15.0
```

[docs/LIGHTROOM_API.md](docs/LIGHTROOM_API.md) summarizes what the API allows.

## Lightroom plugin

The plugin lives in `plugin/Safelight.lrplugin`. Load it straight from your checkout
once: in Lightroom Classic, **File > Plug-in Manager > Add**, then choose that folder.
After changing Lua code, select Safelight in the Plug-in Manager and click
**Reload Plug-in** (under Plug-in Author Tools); there is no need to restart Lightroom,
and the plugin reconnects to the hub on its own. (Plugins placed in Lightroom's
`Modules` folder cannot be reloaded that way.)

Its log is written to `safelight.log` in the macOS temporary folder; the path is shown
under **File > Plug-in Extras > Safelight Status**.

## Talking to Lightroom from the terminal

```bash
uv run safelight status                          # hub and Lightroom state
uv run safelight bench -n 100                    # round-trip latency
uv run safelight call inspect.app                # any plugin command, with JSON params
uv run safelight call inspect.thumbnails '{"sizes": [512]}'
uv run safelight call system.progress_test '{"steps": 20}' --wait 1   # returns a running job
uv run safelight job <job_id> --wait 5                                 # follow it
uv run safelight cancel <job_id>
uv run safelight activity                         # what the hub has done
uv run safelight settings --confirm-threshold 20 --read-only off
```

The `inspect.*` and `system.*` commands are read-only.

## Working without Lightroom

`uv run safelight fake-plugin` connects to the hub like the Lightroom plugin and
answers a few commands (`system.ping`, `echo`, `fake.bulk_edit`,
`fake.remove_photos`) with progress, cancellation and dry runs. Quit Lightroom
first, or the two will share the work. The test suite uses the same fake
(`safelight.devtools.fake_plugin`).
