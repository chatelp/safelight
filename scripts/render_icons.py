"""Render Safelight's icons and color tokens from the masters in app/Design/.

    uv run python scripts/render_icons.py

Reads the SVG masters in app/Design/masters/ and app/Design/tokens.json, and writes:

- app/Design/Assets.xcassets/: AppIcon, menu bar template images and color sets,
  ready to drop into the Xcode project of the menu bar app;
- app/Design/app-icon-1024.png and app/Design/banner.png;
- app/Design/Safelight.icns (macOS only, built with iconutil);
- app/Design/tokens.css for the website and docs.

Needs the Cairo library (macOS: `brew install cairo`; Debian/Ubuntu: `apt install libcairo2`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "app" / "Design"
MASTERS = DESIGN / "masters"
XCASSETS = DESIGN / "Assets.xcassets"

# macOS app icon slots: (point size, scale). Pixel size = point size x scale.
APP_ICON_SLOTS = [(points, scale) for points in (16, 32, 128, 256, 512) for scale in (1, 2)]
# At 32 px and below, the simplified master stays legible; above, the detailed one is used.
SMALL_ICON_MAX_PX = 32

# Menu bar template images: asset name -> master. Drawn on an 18 x 18 pt canvas.
MENU_BAR_IMAGES = {
    "MenuBarStopped": "menubar-stopped.svg",
    "MenuBarWaiting": "menubar-waiting.svg",
    "MenuBarConnected": "menubar-connected.svg",
    "MenuBarWorking1": "menubar-working-1.svg",
    "MenuBarWorking2": "menubar-working-2.svg",
    "MenuBarWorking3": "menubar-working-3.svg",
}
MENU_BAR_PT = 18

XCODE_INFO = {"author": "xcode", "version": 1}


def _load_cairosvg():
    """Import cairosvg, retrying once with Homebrew's lib folder on macOS."""
    try:
        import cairosvg
    except OSError:
        if sys.platform != "darwin" or os.environ.get("SAFELIGHT_CAIRO_RETRY"):
            raise
        libdirs = [d for d in ("/opt/homebrew/lib", "/usr/local/lib") if Path(d).is_dir()]
        env = dict(os.environ, SAFELIGHT_CAIRO_RETRY="1")
        env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
            [*libdirs, env.get("DYLD_FALLBACK_LIBRARY_PATH", "")]
        ).rstrip(":")
        os.execve(sys.executable, [sys.executable, *sys.argv], env)
    return cairosvg


def render(master: str, out: Path, width: int, height: int | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    CAIROSVG.svg2png(
        url=str(MASTERS / master),
        write_to=str(out),
        output_width=width,
        output_height=height or width,
    )


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def app_icon_filename(points: int, scale: int) -> str:
    # iconutil's naming, so the same files also build the .icns.
    suffix = "@2x" if scale == 2 else ""
    return f"icon_{points}x{points}{suffix}.png"


def render_app_icon() -> None:
    iconset = XCASSETS / "AppIcon.appiconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    images = []
    for points, scale in APP_ICON_SLOTS:
        px = points * scale
        master = "app-icon-small.svg" if px <= SMALL_ICON_MAX_PX else "app-icon.svg"
        name = app_icon_filename(points, scale)
        render(master, iconset / name, px)
        images.append(
            {"filename": name, "idiom": "mac", "scale": f"{scale}x", "size": f"{points}x{points}"}
        )
    write_json(iconset / "Contents.json", {"images": images, "info": XCODE_INFO})
    render("app-icon.svg", DESIGN / "app-icon-1024.png", 1024)

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "Safelight.iconset"
            src.mkdir()
            for png in iconset.glob("*.png"):
                shutil.copy(png, src / png.name)
            subprocess.run(
                ["iconutil", "-c", "icns", "-o", str(DESIGN / "Safelight.icns"), str(src)],
                check=True,
            )
    else:
        print("iconutil not found (not macOS): skipped Safelight.icns")


def render_menu_bar() -> None:
    for asset, master in MENU_BAR_IMAGES.items():
        imageset = XCASSETS / f"{asset}.imageset"
        if imageset.exists():
            shutil.rmtree(imageset)
        stem = master.removesuffix(".svg")
        images = []
        for scale in (1, 2):
            name = f"{stem}{'@2x' if scale == 2 else ''}.png"
            render(master, imageset / name, MENU_BAR_PT * scale)
            images.append({"filename": name, "idiom": "universal", "scale": f"{scale}x"})
        write_json(
            imageset / "Contents.json",
            {
                "images": images,
                "info": XCODE_INFO,
                "properties": {"template-rendering-intent": "template"},
            },
        )


def resolve(tokens: dict, ref: str) -> str:
    """Turn 'graphite.950' into a hex value."""
    group, _, key = ref.partition(".")
    return tokens["palette"][group][key]


def semantic_pair(tokens: dict, spec: str | dict) -> tuple[str, str]:
    if isinstance(spec, str):
        spec = tokens["semantic"][spec]
    return resolve(tokens, spec["light"]), resolve(tokens, spec["dark"])


def xcode_color(hex_value: str) -> dict:
    h = hex_value.lstrip("#")
    return {
        "color-space": "srgb",
        "components": {
            "alpha": "1.000",
            "red": f"0x{h[0:2]}",
            "green": f"0x{h[2:4]}",
            "blue": f"0x{h[4:6]}",
        },
    }


def write_colors(tokens: dict) -> None:
    for name, spec in tokens["xcode_colors"].items():
        light, dark = semantic_pair(tokens, spec)
        colors = [
            {"color": xcode_color(light), "idiom": "universal"},
            {
                "appearances": [{"appearance": "luminosity", "value": "dark"}],
                "color": xcode_color(dark),
                "idiom": "universal",
            },
        ]
        colorset = XCASSETS / f"{name}.colorset"
        if colorset.exists():
            shutil.rmtree(colorset)
        write_json(colorset / "Contents.json", {"colors": colors, "info": XCODE_INFO})

    lines = ["/* Generated by scripts/render_icons.py from app/Design/tokens.json. */", ":root {"]
    for group, scale in tokens["palette"].items():
        for key, value in scale.items():
            lines.append(f"  --sl-{group}-{key}: {value};")
    light_lines, dark_lines = [], []
    for name in tokens["semantic"]:
        light, dark = semantic_pair(tokens, name)
        light_lines.append(f"  --sl-{name}: {light};")
        dark_lines.append(f"    --sl-{name}: {dark};")
    lines += [*light_lines, "}", "@media (prefers-color-scheme: dark) {", "  :root {"]
    lines += [*dark_lines, "  }", "}"]
    (DESIGN / "tokens.css").write_text("\n".join(lines) + "\n")


def main() -> None:
    tokens = json.loads((DESIGN / "tokens.json").read_text())
    write_json(XCASSETS / "Contents.json", {"info": XCODE_INFO})
    render_app_icon()
    render_menu_bar()
    write_colors(tokens)
    render("banner.svg", DESIGN / "banner.png", 1600, 440)
    print(f"Rendered icons and tokens into {DESIGN.relative_to(ROOT)}/")


CAIROSVG = _load_cairosvg()

if __name__ == "__main__":
    main()
