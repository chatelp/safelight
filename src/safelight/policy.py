"""What each Lightroom command may do, so the hub can protect the catalog.

- read-only commands always run;
- mutating commands are refused in read-only mode;
- mutating commands that support a dry run are first simulated by the plugin; past the
  confirmation threshold the hub asks for confirmation instead of running them;
- destructive commands always need confirmation, and so does an export that replaces
  existing files (`if_exists: overwrite`).

Unknown commands are treated as mutating, so a new command is never less protected than
an old one by accident. tests/test_policy.py checks that every command the plugin
registers is listed here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class CommandPolicy:
    mutating: bool = True
    destructive: bool = False
    supports_dry_run: bool = False
    # Why it always needs confirmation, told to the user (destructive commands only).
    reason: str | None = None


READ_ONLY = CommandPolicy(mutating=False)
BULK = CommandPolicy(supports_dry_run=True)
SINGLE = CommandPolicy()
DESTRUCTIVE = CommandPolicy(destructive=True, supports_dry_run=True)

READ_ONLY_PREFIXES = ("system.", "inspect.")
READ_ONLY_COMMANDS = frozenset(
    {
        "echo",
        "catalog.info",
        "photos.get",
        "photos.search",
        "photos.preview",
        "collections.list",
        "collections.photos",
        "keywords.list",
        "develop.get",
        "presets.list",
        "snapshots.list",
        "masks.list",
    }
)

DEFAULT_POLICIES: dict[str, CommandPolicy] = {
    # Changes to one or many photos: simulated first, confirmed past the threshold.
    "photos.rotate": BULK,
    "photos.create_virtual_copies": BULK,
    "collections.add": BULK,
    "collections.remove_photos": BULK,
    "metadata.set": BULK,
    "keywords.add": BULK,
    "keywords.remove": BULK,
    "develop.apply": BULK,
    "develop.copy": BULK,
    "develop.auto_tone": BULK,
    "develop.auto_white_balance": BULK,
    "develop.reset": BULK,
    "develop.apply_preset": BULK,
    "snapshots.create": BULK,
    "masks.adjust": BULK,
    "masks.create": BULK,
    "masks.invert": BULK,
    "export.photos": BULK,
    # Small changes without a dry run.
    "collections.create": SINGLE,
    "snapshots.apply": SINGLE,
    # Hard to undo: always confirmed.
    "collections.delete": DESTRUCTIVE,
    # Run by the hub itself: Safelight's own collections, and the colors of validated photos.
    "safelight.collections": SINGLE,
    "safelight.tidy": SINGLE,
    "snapshots.delete": DESTRUCTIVE,
    "masks.delete": DESTRUCTIVE,
    # Development commands understood by safelight.devtools.fake_plugin.
    "fake.bulk_edit": BULK,
    "fake.remove_photos": DESTRUCTIVE,
}


# Commands that change a photo's edits. The plugin takes the photos the user validated
# (Safelight's "Validated" field) out of them, whatever the AI client asked; the list is
# mirrored in plugin/Safelight.lrplugin/CommandRouter.lua (EDITING), and a test checks it.
EDITING_COMMANDS = frozenset(
    {
        "develop.apply",
        "develop.copy",
        "develop.auto_tone",
        "develop.auto_white_balance",
        "develop.reset",
        "develop.apply_preset",
        "snapshots.create",
        "snapshots.apply",
        "snapshots.delete",
        "masks.adjust",
        "masks.create",
        "masks.invert",
        "masks.delete",
        "photos.rotate",
    }
)
VALIDATED_ERROR = "VALIDATED: the user validated this photo, so Safelight does not change its edits"


def policy_for(
    command: str,
    policies: dict[str, CommandPolicy] | None = None,
    params: dict[str, Any] | None = None,
) -> CommandPolicy:
    table = DEFAULT_POLICIES if policies is None else policies
    if command in table:
        policy = table[command]
    elif command.startswith(READ_ONLY_PREFIXES) or command in READ_ONLY_COMMANDS:
        return READ_ONLY
    else:
        policy = CommandPolicy()
    if command == "export.photos" and (params or {}).get("if_exists") == "overwrite":
        # Files on disk replaced by an export cannot be got back from Lightroom.
        return replace(
            policy,
            destructive=True,
            reason="This export replaces files that already exist in the destination folder.",
        )
    return policy


def is_classified(command: str) -> bool:
    return (
        command in DEFAULT_POLICIES
        or command in READ_ONLY_COMMANDS
        or command.startswith(READ_ONLY_PREFIXES)
    )
