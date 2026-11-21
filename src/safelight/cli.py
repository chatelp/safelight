"""Command-line entry point: `safelight <command>`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import sys
import time

from . import __version__


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _on_off(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"on", "true", "yes", "1"}:
        return True
    if lowered in {"off", "false", "no", "0"}:
        return False
    raise argparse.ArgumentTypeError("expected on or off")


async def _bench(count: int) -> int:
    from .hub_client import HubClient
    from .protocol import JobState

    client = HubClient()
    timings = []
    for _ in range(count):
        started = time.perf_counter()
        result = await client.run("system.ping", wait_s=10.0)
        if result.get("state") != JobState.SUCCEEDED:
            _print_json(result)
            return 1
        timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
    print(
        f"{count} pings: median {statistics.median(timings):.1f} ms, "
        f"p95 {p95:.1f} ms, max {timings[-1]:.1f} ms"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="safelight",
        description="Safelight for Lightroom Classic. Not affiliated with Adobe.",
    )
    parser.add_argument("--version", action="version", version=f"safelight {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("mcp", help="run the MCP server over stdio (what AI clients launch)")
    sub.add_parser("hub", help="run the hub in the foreground (normally started automatically)")
    sub.add_parser("status", help="show hub and Lightroom status, starting the hub if needed")

    call = sub.add_parser("call", help="send a raw command to Lightroom (development)")
    call.add_argument("lr_command")
    call.add_argument("params", nargs="?", default="{}", help="JSON object")
    call.add_argument("--wait", type=float, default=30.0, help="seconds to wait for the result")
    call.add_argument("--confirm", help="confirmation token returned by an earlier call")

    job = sub.add_parser("job", help="show a job, optionally waiting for it")
    job.add_argument("job_id")
    job.add_argument("--wait", type=float, default=0.0)
    cancel = sub.add_parser("cancel", help="cancel a job")
    cancel.add_argument("job_id")
    sub.add_parser("jobs", help="list recent jobs")
    activity = sub.add_parser("activity", help="show the activity log")
    activity.add_argument("--after", type=int, default=0)

    settings = sub.add_parser("settings", help="show or change settings")
    settings.add_argument("--confirm-threshold", type=int, help="photos before asking to confirm")
    settings.add_argument("--read-only", type=_on_off, help="on or off")

    bench = sub.add_parser("bench", help="measure the round trip to Lightroom")
    bench.add_argument("-n", type=int, default=50)
    sub.add_parser("fake-plugin", help="act as a fake Lightroom plugin (development)")

    args = parser.parse_args(argv)

    if args.command == "mcp":
        from .mcp_server import build_server

        build_server().run("stdio")
        return 0

    if args.command == "hub":
        from .hub import run_hub
        from .paths import configured_port, safelight_home

        run_hub(safelight_home(), configured_port())
        return 0

    if args.command == "fake-plugin":
        from .devtools.fake_plugin import run_against_local_hub

        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run_against_local_hub())
        return 0

    if args.command == "bench":
        return asyncio.run(_bench(args.n))

    from .hub_client import HubClient

    client = HubClient()

    if args.command == "status":
        from .mcp_server import collect_status

        _print_json(asyncio.run(collect_status(client)).model_dump())
        return 0

    if args.command == "call":
        result = asyncio.run(
            client.run(
                args.lr_command, json.loads(args.params), wait_s=args.wait, confirm=args.confirm
            )
        )
        _print_json(result)
        return 0 if result.get("state") != "failed" else 1

    if args.command == "job":
        _print_json(asyncio.run(client.job(args.job_id, args.wait)))
        return 0
    if args.command == "cancel":
        _print_json(asyncio.run(client.cancel(args.job_id)))
        return 0
    if args.command == "jobs":
        _print_json(asyncio.run(client.jobs()))
        return 0
    if args.command == "activity":
        _print_json(asyncio.run(client.activity(args.after)))
        return 0
    if args.command == "settings":
        changes = {}
        if args.confirm_threshold is not None:
            changes["confirm_threshold"] = args.confirm_threshold
        if args.read_only is not None:
            changes["read_only"] = args.read_only
        _print_json(asyncio.run(client.settings(changes or None)))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
