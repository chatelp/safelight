"""Command-line entry point: `safelight <command>`."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="safelight",
        description="Safelight for Lightroom Classic. Not affiliated with Adobe.",
    )
    parser.add_argument("--version", action="version", version=f"safelight {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("mcp", help="run the MCP server over stdio (what AI clients launch)")

    args = parser.parse_args(argv)

    if args.command == "mcp":
        from .mcp_server import build_server

        build_server().run("stdio")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
