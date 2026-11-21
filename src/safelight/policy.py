"""What each Lightroom command may do, so the hub can protect the catalog.

- read-only commands always run;
- mutating commands are refused in read-only mode;
- mutating commands that support a dry run are first simulated by the plugin; past the
  confirmation threshold the hub asks for confirmation instead of running them;
- destructive commands always need confirmation.

Unknown commands are treated as mutating, so a new command is never less protected than
an old one by accident.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandPolicy:
    mutating: bool = True
    destructive: bool = False
    supports_dry_run: bool = False


READ_ONLY = CommandPolicy(mutating=False)

DEFAULT_POLICIES: dict[str, CommandPolicy] = {
    # Development commands understood by safelight.devtools.fake_plugin.
    "fake.bulk_edit": CommandPolicy(supports_dry_run=True),
    "fake.remove_photos": CommandPolicy(destructive=True, supports_dry_run=True),
    # Lightroom commands are added here as the plugin gains them (roadmap lot L3).
}

READ_ONLY_PREFIXES = ("system.", "inspect.")
READ_ONLY_COMMANDS = {"echo"}


def policy_for(command: str, policies: dict[str, CommandPolicy] | None = None) -> CommandPolicy:
    table = DEFAULT_POLICIES if policies is None else policies
    if command in table:
        return table[command]
    if command.startswith(READ_ONLY_PREFIXES) or command in READ_ONLY_COMMANDS:
        return READ_ONLY
    return CommandPolicy()
