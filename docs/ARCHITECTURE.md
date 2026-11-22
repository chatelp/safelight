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
running, the relay starts it in the background (in about half a second). It
passes the AI client's own name (for example "Claude Desktop") to the hub for the
activity log. It exposes 24 tools, listed in [TOOLS.md](TOOLS.md): read-only tools
are annotated as such; the others go through the hub's policy. When a change needs
approval, the relay asks the user through MCP elicitation if the client supports it,
and otherwise returns the confirmation token to the AI. Previews and contact sheets
are returned as images (contact sheets are composed by the relay with Pillow).
Photo analysis (`analyze_photos`, in `src/safelight/analysis.py`) also runs in the relay,
on 512 px Lightroom previews, with NumPy: focus from the 99th percentile of the
Laplacian, clipping and brightness from the histogram, tilt from projection profiles of
the edge map (verticals first, so receding lines in interiors don't count), empty
borders from flat white or black edge pixels, near-duplicates from a 64-bit difference hash, and bursts from capture
times closer than a few seconds. Straightening (`straighten_photos`, in
`src/safelight/upright.py`) finds long straight lines on a preview rendered without
transform and fits a camera rotation to them; it writes the result as Lightroom Guided
Upright guides (`UprightFourSegments_N`) plus the transform Lightroom would have computed
from them (`UprightTransform_5`, a 3x3 matrix on 0..1 image coordinates), because
Lightroom only computes that transform for guides drawn by hand.

### Hub (`safelight hub`)

A small local daemon, one per user session:

- listens on `127.0.0.1:58763` only (`SAFELIGHT_PORT` overrides it);
- writes `~/Library/Application Support/Safelight/hub.json` (port and a random
  token, readable by the user only) and removes it when it stops;
- requires that token on every request, refuses requests carrying a browser
  `Origin` or an unexpected `Host`, so web pages cannot reach it;
- turns every command into a **job** and hands it to the plugin;
- applies the **command policy** before running anything that changes the
  catalog;
- keeps an **activity log** and the list of recently seen AI clients;
- stores the user's **settings** in `settings.json` next to `hub.json`.

#### Jobs

Each command becomes a job with an id and a state: `queued`, `running`,
`succeeded`, `failed` or `cancelled`. A client waits up to `wait_s` seconds
(30 by default) for the result; if the job is still running it gets the job id
and follows it with `GET /v1/jobs/{id}`. While a job runs, the plugin may report
progress (`done`, `total`, `message`).

- **Never retried.** A job Lightroom did not pick up within 10 s is cancelled and
  reported as not run (`NOT_DELIVERED`). A job Lightroom picked up keeps running
  as long as Lightroom stays connected; if Lightroom disconnects, it fails with
  `LIGHTROOM_DISCONNECTED` ("it may or may not have completed").
- **Cancellation is cooperative.** `POST /v1/jobs/{id}/cancel` removes a queued
  job at once; for a running job, the plugin learns about it in the answer to
  its next progress report and stops. Commands that report no progress run to
  completion. Work already done is kept.
- Finished jobs stay readable for an hour.

#### Command policy and confirmations

`src/safelight/policy.py` describes each command: read-only, mutating,
destructive, and whether the plugin can simulate it (`dry_run`). Unknown
commands count as mutating.

1. **Read-only mode** (a setting) refuses every mutating command.
2. A mutating command that supports a dry run is first simulated: the plugin
   returns how many photos it would touch.
3. If that number exceeds the **confirmation threshold** (a setting, 50 by
   default), or if the command is destructive, nothing runs. The hub answers
   `confirmation_required` with a summary and a single-use token, valid 10
   minutes and bound to the exact command and parameters.
4. The AI client shows the summary to the user and, only after the user
   approves, sends the same command again with the token.

The AI cannot change settings: they are only reachable through the local API
(the CLI today, the menu bar app later).

#### HTTP API

| Endpoint | Used by | Purpose |
|---|---|---|
| `GET /v1/status` | relays, app | hub, plugin, jobs, clients, settings |
| `POST /v1/rpc` | relays, CLI | run a command (`command`, `params`, `wait_s`, `confirm`) |
| `GET /v1/jobs`, `GET /v1/jobs/{id}?wait_s=` | relays, app | recent jobs, one job |
| `POST /v1/jobs/{id}/cancel` | relays, app | cancel a job |
| `GET /v1/activity?after=` | app | activity events after a sequence number |
| `GET`/`PUT /v1/settings` | app, CLI | confirmation threshold, read-only mode |
| `POST /v1/plugin/poll` | plugin | long-poll for jobs |
| `POST /v1/plugin/progress` | plugin | report progress, learn about cancellation |
| `POST /v1/plugin/result` | plugin | deliver a job's result |

Every request carries the `X-Safelight-Token` header; relays and the CLI also
send `X-Safelight-Client` with the client's name.

### Lightroom plugin (`plugin/Safelight.lrplugin`)

Lightroom Classic's SDK can listen on sockets (`LrSocket`), but each socket
serves a single client and has to be re-bound, on a new port, after every
disconnect. Outgoing HTTP requests (`LrHttp`) are simple and robust, so the
plugin acts as a **client of the hub**:

1. when Lightroom starts, the plugin reads `hub.json` and opens a long-poll
   request to the hub (`POST /v1/plugin/poll`, held up to 20 s);
2. the hub answers as soon as a command is queued;
3. the plugin runs each command in its own Lightroom task through the SDK
   (catalog write gates, `LrDevelopController`, export sessions), reports
   progress of long commands to `POST /v1/plugin/progress` (stopping if the
   answer says the job was cancelled) and posts the answer to
   `POST /v1/plugin/result`. Commands that change photos accept a `dry_run`
   parameter that only counts what they would change;
4. if the hub is not running, the plugin retries every 1 to 5 seconds at
   negligible cost.

The commands the plugin understands are listed in
[PLUGIN_COMMANDS.md](PLUGIN_COMMANDS.md).

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

### Menu bar app (`app/`)

A native SwiftUI `MenuBarExtra` app that talks to the hub's status API:

- status icon: hub running, Lightroom connected, or not;
- connected AI clients and a live log of commands;
- open Lightroom, start or stop the hub, open logs;
- install or update the Lightroom plugin, register Safelight with Claude Desktop,
  Claude Code, Cursor and Codex;
- settings: confirmation threshold, read-only mode, launch at login.

The app polls `GET /v1/status` and `GET /v1/activity` every 1.5 s and changes settings
with `PUT /v1/settings`; see [app/README.md](../app/README.md). In release builds
(roadmap lot L7) it will embed the Python hub, so users install a single signed `.dmg`.

## Versioning

The repository's `VERSION` file is the single source of truth.
`scripts/sync_version.py` copies it into the plugin's `Info.lua`, and a test
fails if they drift.
