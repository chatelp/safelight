"""Safelight's know-how for AI clients: recipes and background guides.

Recipes are step-by-step methods for common jobs (MCP prompts, with the folder as
argument). Guides are background documents (MCP resources). Both are Markdown files in
`data/recipes` and `data/guides`, and the safelight_guide tool returns any of them, for
AI clients that show neither prompts nor resources. Where each kind of knowledge goes:
see docs/ARCHITECTURE.md ("Where knowledge lives").
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Annotated

from pydantic import Field

DATA = resources.files("safelight") / "data"


@dataclass(frozen=True)
class Entry:
    name: str
    title: str
    description: str
    kind: str  # "recipe" or "guide"

    def text(self, folder: str | None = None) -> str:
        raw = (DATA / f"{self.kind}s" / f"{self.name}.md").read_text(encoding="utf-8")
        if self.kind == "recipe":
            return raw.format(folder=folder or "the folder the user names")
        return raw


RECIPES = (
    Entry(
        "straighten-exhibition-folder",
        "Straighten an exhibition folder",
        "Straighten the photos of an exhibition (prints and panels on walls) in one folder, "
        "check each result and choose the guides where the automatic ones are wrong.",
        "recipe",
    ),
    Entry(
        "sort-duplicates",
        "Sort duplicates",
        "Keep the best frame of each burst or group of near-duplicates in one folder, with "
        "the user deciding, and record why the others are rejected.",
        "recipe",
    ),
)

GUIDES = (
    Entry(
        "guided-upright",
        "Lightroom's Guided Upright",
        "How Lightroom's Guided Upright works, how Safelight straightens photos, and which "
        "lines make good guides.",
        "guide",
    ),
    Entry(
        "shift-lenses",
        "Shift and tilt-shift lenses",
        "What changes for photos made with a shift lens: optical centre, lens names, "
        "straightening.",
        "guide",
    ),
)

ENTRIES = {entry.name: entry for entry in (*RECIPES, *GUIDES)}


def register(server) -> None:
    """Adds the recipes as prompts and the guides as resources."""
    for entry in RECIPES:
        server.prompt(name=entry.name, title=entry.title, description=entry.description)(
            _prompt(entry)
        )
    for entry in GUIDES:
        server.resource(
            f"safelight://guides/{entry.name}",
            name=entry.name,
            title=entry.title,
            description=entry.description,
            mime_type="text/markdown",
        )(_guide(entry))


def _prompt(entry: Entry):
    def prompt(
        folder: Annotated[
            str, Field(description="The folder's name, as Lightroom's Folders panel shows it")
        ],
    ) -> str:
        return entry.text(folder)

    return prompt


def _guide(entry: Entry):
    def guide() -> str:
        return entry.text()

    return guide
