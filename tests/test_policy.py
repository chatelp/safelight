"""Every command the Lightroom plugin registers must have an explicit policy."""

import re
from pathlib import Path

from safelight.policy import DEFAULT_POLICIES, is_classified, policy_for

ROUTER = Path(__file__).resolve().parents[1] / "plugin" / "Safelight.lrplugin" / "CommandRouter.lua"


def plugin_commands() -> list[str]:
    return re.findall(r'register\("([^"]+)"', ROUTER.read_text(encoding="utf-8"))


def test_plugin_registers_commands():
    assert len(plugin_commands()) > 30


def test_every_plugin_command_is_classified():
    unclassified = [c for c in plugin_commands() if not is_classified(c)]
    assert unclassified == [], f"add these commands to src/safelight/policy.py: {unclassified}"


def test_every_policy_matches_a_known_command():
    known = set(plugin_commands()) | {"fake.bulk_edit", "fake.remove_photos"}
    assert set(DEFAULT_POLICIES) <= known


def test_unknown_commands_are_treated_as_mutating():
    assert policy_for("brand.new_thing").mutating is True
    assert policy_for("photos.get").mutating is False
    assert policy_for("collections.delete").destructive is True


def test_an_export_that_overwrites_files_is_always_confirmed():
    assert policy_for("export.photos").destructive is False
    assert policy_for("export.photos", params={"if_exists": "rename"}).destructive is False
    overwrite = policy_for("export.photos", params={"if_exists": "overwrite"})
    assert overwrite.destructive and overwrite.supports_dry_run
    assert "replaces files" in overwrite.reason
