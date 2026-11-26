# Privacy policy

Last updated: November 26, 2026. This covers the Safelight software: the MCP relay, the
hub, the Lightroom Classic plugin and the menu bar app.

## In short

- Safelight runs on your Mac. It has no account, no telemetry, no analytics and no
  advertising, and its authors receive nothing about you or your photos.
- Safelight's own components only talk to each other, on your Mac.
- When an AI client you connected calls a Safelight tool, the answer goes to that client.
  **Cloud-based AI clients send it on to their provider**, under that provider's terms.

## What Safelight reads

Safelight reads your Lightroom Classic catalog through the Lightroom SDK, when an AI client
(or you, with the command line) asks for something. Depending on the tool, that can be:

- photo details: file name and path, date, camera, lens, exposure, rating, flag, label,
  title, caption, keywords, and **GPS coordinates** when the photo has them;
- Develop settings, masks, snapshots, presets and collections;
- previews of photos, which can show people and places;
- the Lightroom version, the catalog's path and what is selected.

It changes your catalog, and writes exported files where asked, only through the tools an
AI client calls. Changes to many photos, and deletions such as a collection, snapshot or
mask, ask for your approval first, and a read-only mode refuses every change.

## What leaves your Mac

Safelight itself sends nothing over the internet. The answers of its tools go to the AI
client that called them. If that client uses a cloud service (Claude, for example), they
become part of your conversation there: how long they are kept, and whether they are used
for training, depends on that provider and your settings with them. Safelight cannot
delete them from there.

Lightroom Classic is Adobe's software and follows Adobe's own privacy policy, including
for features that use Adobe's servers.

## What is stored on your Mac

| Where | What |
|---|---|
| `~/Library/Application Support/Safelight/hub.json` | the hub's local port and a random token, readable by your user account only, removed when the hub stops |
| `~/Library/Application Support/Safelight/settings.json` | your settings |
| `~/Library/Application Support/Safelight/hub.log` | technical log of the hub |
| `~/Library/Application Support/Safelight/review.json` | the photos to check shown in the app: photo id, file name and why (at most 200, until you mark them done) |
| `safelight.log` in the temporary folder, and Lightroom's plug-in log folder | technical log of the plugin: connections, photo ids, and error messages, which can contain file names or paths |
| `~/Library/Application Support/Safelight/straighten/` | for each photo straightened with `straighten_photos`: the preview it analysed (1600 px, without the correction), the straight lines found and the correction written, so the AI client can choose other lines later. Readable by your user account only; files older than 90 days are removed, and you can delete the folder at any time |
| memory only | recent activity and results of recent jobs (results for about an hour), lost when the hub stops |

Other previews made for a tool call are not written to disk by Safelight. If you use the menu bar
app to set up an AI client, it adds a Safelight entry to that client's configuration and
keeps a backup copy next to it.

## Security

The hub listens on `127.0.0.1` only, requires its token on every request, and refuses
requests coming from web pages. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Your choices

- Disconnect Safelight from an AI client at any time, in that client's settings.
- Turn on read-only mode to prevent any change to your catalog.
- To remove everything, remove the Safelight plugin in Lightroom and delete
  `~/Library/Application Support/Safelight` and the log files above.

## Changes and contact

If a future version sends anything elsewhere (a check for updates, for example), this page
will say so before that version is released. Its history is public in the repository.
Questions: [open an issue](https://github.com/chatelp/safelight/issues), without personal
information, since issues are public.

Safelight is not affiliated with or endorsed by Adobe.
