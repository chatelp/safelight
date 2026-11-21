# Safelight for Lightroom Classic

Safelight lets Claude and other MCP clients work in your Adobe Lightroom Classic
catalog: inspect photos, edit Develop settings and masks, cull large shoots, write
metadata, organize collections and export, with Lightroom doing the actual work.

> **Status: pre-alpha, under active rewrite.** Nothing here is usable yet. The
> first public release (1.0) will ship as a signed macOS app.

Safelight is not affiliated with or endorsed by Adobe. Lightroom and Lightroom
Classic are trademarks of Adobe Inc.

## How it works

```
Claude Desktop ─┐
Claude Code ────┼─► MCP relay ─► Safelight hub ◄── Safelight plugin (inside Lightroom Classic)
Cursor, Codex ──┘     (stdio)    (always on,               ▲
                                  localhost only)           │
                                         ▲            connects when Lightroom opens
                                         │
                                  Menu bar app (status, setup)
```

- The **hub** runs on your Mac independently of Lightroom. AI clients connect to
  it at any time, so they never fail at startup because Lightroom is closed.
- The **Lightroom plugin** connects to the hub whenever Lightroom Classic is open,
  and runs every command through the Lightroom SDK. Safelight never edits the
  catalog file directly.
- Several AI clients can share the same Lightroom session.
- The **menu bar app** shows what is connected and what the AI is doing, installs
  the plugin and configures your AI clients. Everything also works without it.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Planned for 1.0

- A reliable connection that survives Lightroom being opened, closed or restarted
- About twenty clear tools covering Develop settings, masks, metadata, keywords,
  collections, snapshots, search and export
- Photo previews the AI can look at on request, including contact sheets
- Confirmation before bulk or destructive changes
- Built for large shoots: batch operations and long-running jobs with progress
- Burst and duplicate grouping, quality checks (focus, exposure, horizon) and
  natural-language catalog search
- macOS menu bar app, signed and notarized `.dmg`
- Lightroom Classic 14 or later, macOS only

## Repository layout

| Path | Contents |
|---|---|
| `src/safelight/` | Python package: MCP server, hub (in progress) |
| `plugin/Safelight.lrplugin/` | Lightroom Classic plugin (Lua) |
| `app/` | macOS menu bar app (Swift, not started) |
| `docs/` | Architecture and design notes |
| `scripts/` | Development helpers |
| `tests/` | Python tests |

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

Safelight started as a fork of
[lightroom-classic-mcp](https://github.com/4xiomdev/lightroom-classic-mcp) by
4xiom, released under the MIT License. Its Lightroom SDK action code is the
starting point of Safelight's plugin; the transport, server and tooling are being
rewritten.

## License

MIT. See [LICENSE](LICENSE).
