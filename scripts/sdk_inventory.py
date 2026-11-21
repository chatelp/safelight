"""Search the Lightroom Classic SDK API Reference from the command line.

The SDK is not part of the repository (Adobe's license does not allow
redistributing it). Download it from the Adobe Developer Console and unzip it
under vendor/, for example vendor/lightroom-classic-sdk-16.0/.

Examples:
    uv run python scripts/sdk_inventory.py build
    uv run python scripts/sdk_inventory.py list LrDevelopController
    uv run python scripts/sdk_inventory.py show LrPhoto requestJpegThumbnail
    uv run python scripts/sdk_inventory.py since 15.0
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"

ENTRY = re.compile(
    r'<dt><a name="([^"]+)"></a><strong>([^<]+)</strong>(.*?)</dt>\s*<dd>(.*?)</dd>'
    r"\s*(?=<dt><a name=|</dl>)",
    re.S,
)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def find_sdk() -> Path:
    candidates = sorted(VENDOR.glob("lightroom-classic-sdk-*/API Reference/modules"))
    if not candidates:
        sys.exit(f"No Lightroom Classic SDK found under {VENDOR}. See this script's docstring.")
    return candidates[-1]


def build(modules: Path) -> dict[str, list[dict]]:
    inventory: dict[str, list[dict]] = {}
    for page in sorted(modules.glob("*.html")):
        raw = page.read_text(encoding="utf-8", errors="replace")
        entries = []
        for _anchor, name, signature, body in ENTRY.findall(raw):
            text = _text(body)
            since = re.search(r"First supported in version ([\d.]+)", text)
            entries.append(
                {
                    "name": name.strip(),
                    "params": _text(signature),
                    "since": since.group(1) if since else None,
                    "develop_module_only": "Develop module is active" in text,
                    "doc": text,
                }
            )
        inventory[page.stem] = entries
    return inventory


def load(modules: Path) -> dict[str, list[dict]]:
    cache = modules.parent.parent / "inventory.json"
    if not cache.exists():
        cache.write_text(json.dumps(build(modules), indent=1, ensure_ascii=False))
    return json.loads(cache.read_text())


def short_name(entry: dict) -> str:
    return re.split(r"[.:]", entry["name"])[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="(re)build vendor/.../inventory.json")
    list_cmd = sub.add_parser("list", help="list the functions of one namespace or class")
    list_cmd.add_argument("module")
    show_cmd = sub.add_parser("show", help="print the full documentation of one function")
    show_cmd.add_argument("module")
    show_cmd.add_argument("function")
    since_cmd = sub.add_parser("since", help="functions first supported in this version or later")
    since_cmd.add_argument("version")
    args = parser.parse_args(argv)

    modules = find_sdk()
    if args.command == "build":
        inventory = build(modules)
        (modules.parent.parent / "inventory.json").write_text(
            json.dumps(inventory, indent=1, ensure_ascii=False)
        )
        total = sum(len(entries) for entries in inventory.values())
        print(f"{total} entries in {len(inventory)} modules")
        return 0

    inventory = load(modules)
    if args.command == "list":
        for entry in inventory.get(args.module, []):
            flag = " [Develop module]" if entry["develop_module_only"] else ""
            print(f"{short_name(entry)}{entry['params']}  since {entry['since']}{flag}")
    elif args.command == "show":
        matches = [e for e in inventory.get(args.module, []) if short_name(e) == args.function]
        for entry in matches:
            print(entry["doc"])
        if not matches:
            print(f"Not found: {args.module} {args.function}", file=sys.stderr)
            return 1
    elif args.command == "since":
        floor = _version_key(args.version)
        for module, entries in inventory.items():
            for entry in entries:
                if entry["since"] and _version_key(entry["since"]) >= floor:
                    print(f"{entry['since']:>5}  {module}.{short_name(entry)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
