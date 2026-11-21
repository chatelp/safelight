"""Command-line entry point: `safelight <command>`."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

from . import __version__


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


async def _bench(count: int) -> int:
    from .hub_client import HubClient

    client = HubClient()
    timings = []
    for _ in range(count):
        started = time.perf_counter()
        result = await client.call("system.ping", timeout_s=10.0)
        if not result.get("success"):
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
    call.add_argument("--timeout", type=float, default=30.0)
    bench = sub.add_parser("bench", help="measure the round trip to Lightroom")
    bench.add_argument("-n", type=int, default=50)

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

    if args.command == "status":
        from .hub_client import HubClient
        from .mcp_server import collect_status

        _print_json(asyncio.run(collect_status(HubClient())).model_dump())
        return 0

    if args.command == "call":
        from .hub_client import HubClient

        result = asyncio.run(
            HubClient().call(args.lr_command, json.loads(args.params), args.timeout)
        )
        _print_json(result)
        return 0 if result.get("success") else 1

    if args.command == "bench":
        return asyncio.run(_bench(args.n))

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
