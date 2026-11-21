# Architecture

This document describes the target design for Safelight 1.0. Parts marked
*planned* do not exist yet.

## Goals

1. **AI clients never fail because Lightroom is closed.** The MCP server starts
   instantly and reports Lightroom's state through its tools.
2. **Lightroom can be opened, closed or restarted at any time** without
   restarting anything else.
3. **Several AI clients at once** (Claude Desktop and Claude Code, for example)
   share one Lightroom session.
4. **Safe by default:** localhost only, authenticated, and bulk or destructive
   changes need confirmation.
5. **Fast on large shoots:** hundreds of photos per command, long jobs with
   progress.

## Components

### MCP relay (`safelight mcp`)

The process an AI client launches over stdio. It exposes Safelight's tools,
validates arguments, and forwards each call to the hub. If the hub is not
running, the relay starts it in the background (in about half a second).
*Today it only exposes `safelight_status`.*

### Hub (`safelight hub`)

A small local daemon, one per user session:

- listens on `127.0.0.1:58763` only (`SAFELIGHT_PORT` overrides it);
- writes `~/Library/Application Support/Safelight/hub.json` (port and a random
  token, readable by the user only) and removes it when it stops;
- requires that token on every request, refuses requests carrying a browser
  `Origin` or an unexpected `Host`, so web pages cannot reach it;
- keeps the queue of commands from all relays and hands them to the plugin;
- never retries a command: one Lightroom did not pick up in time is dropped and
  reported as not run (`NOT_DELIVERED`); one it picked up but did not answer is
  reported as possibly run (`TIMEOUT`);
- tracks long jobs (export, analysis of a whole shoot) and their progress
  (*planned*);
- applies the confirmation policy: past a configurable number of photos, or for
  destructive actions, a command first returns a dry-run summary and only runs
  once confirmed (*planned*);
- serves a status API (`GET /v1/status`) for relays and the menu bar app.

### Lightroom plugin (`plugin/Safelight.lrplugin`)

Lightroom Classic's SDK can listen on sockets (`LrSocket`), but each socket
serves a single client and has to be re-bound, on a new port, after every
disconnect. Outgoing HTTP requests (`LrHttp`) are simple and robust, so the
plugin acts as a **client of the hub**:

1. when Lightroom starts, the plugin reads `hub.json` and opens a long-poll
   request to the hub (`POST /v1/plugin/poll`, held up to 20 s);
2. the hub answers as soon as a command is queued;
3. the plugin runs each command in its own Lightroom task through the SDK
   (catalog write gates, `LrDevelopController`, export sessions) and posts the
   answer to `POST /v1/plugin/result`;
4. if the hub is not running, the plugin retries every 1 to 5 seconds at
   negligible cost.

Measured on Lightroom Classic 16.0.1 (roadmap lot L1): a command round trip
takes 1.7 ms median (2.1 ms at the 95th percentile); the plugin connects about
8 s after Lightroom loads it and reconnects within 1 to 2.5 s after a hub
restart; three AI clients at once share one hub without slowdown that matters
(3.6 ms median).

This replaces the upstream design, where Lightroom opened two sockets on random
ports, advertised them in a temporary file, and restarted them after every
disconnect.

The plugin and hub exchange a protocol version at connection time, so a
mismatched plugin is reported clearly instead of failing on individual commands.

### Menu bar app (*planned*, `app/`)

A native SwiftUI `MenuBarExtra` app that talks to the hub's status API:

- status icon: hub running, Lightroom connected, or not;
- connected AI clients and a live log of commands;
- open Lightroom, start or stop the hub, open logs;
- install or update the Lightroom plugin, register Safelight with Claude Desktop,
  Claude Code, Cursor and Codex;
- settings: confirmation threshold, read-only mode, launch at login.

The app embeds the Python hub, so users install a single signed `.dmg`.

## Versioning

The repository's `VERSION` file is the single source of truth.
`scripts/sync_version.py` copies it into the plugin's `Info.lua`, and a test
fails if they drift.
