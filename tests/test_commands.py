"""The plugin's commands.json: every command declares its parameters, and a wrong one is
refused, by the plugin's router and by the fake catalog alike (never silently ignored)."""

import asyncio
import re
from pathlib import Path

import pytest

from safelight.devtools.fake_catalog import FakeCatalog, check_params, declared_commands
from safelight.devtools.fake_plugin import CommandError

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "Safelight.lrplugin"
ROUTER = PLUGIN / "CommandRouter.lua"


def registered() -> dict[str, str | None]:
    """Command name -> handler (Module.function) registered in CommandRouter.lua; None for
    a handler written inline."""
    text = ROUTER.read_text(encoding="utf-8")
    named = dict(re.findall(r'register\("([^"]+)",\s*([\w.]+)\)', text))
    return {name: named.get(name) for name in re.findall(r'register\("([^"]+)"', text)}


def lua_functions() -> dict[str, str]:
    """Every function of the plugin's modules, by name (Module.fn or local fn) -> body."""
    out: dict[str, str] = {}
    for path in PLUGIN.glob("*.lua"):
        text = path.read_text(encoding="utf-8")
        starts = list(re.finditer(r"^(?:local )?function ([\w.]+)\(", text, re.M))
        for i, start in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
            out[start.group(1)] = text[start.start() : end]
    return out


def params_read(body: str, functions: dict[str, str], seen: set[str]) -> set[str]:
    """The params.<name> a function reads, with those of helpers it hands params to."""
    names = set(re.findall(r"\bparams\.(\w+)", body))
    for helper in re.findall(r"\b([\w.]+)\(\s*(?:[\w.]+,\s*)?params\b", body):
        if helper in functions and helper not in seen:
            seen.add(helper)
            names |= params_read(functions[helper], functions, seen)
    return names


def test_commands_json_is_found():
    assert declared_commands() is not None, "the fake catalog must check commands.json"


def test_every_registered_command_is_declared_and_back():
    declared = set(declared_commands()["commands"])
    assert set(registered()) == declared


def test_the_fake_catalog_only_answers_declared_commands():
    assert set(FakeCatalog().handlers()) <= set(declared_commands()["commands"])


def test_every_parameter_a_handler_reads_is_declared():
    declared = declared_commands()
    functions = lua_functions()
    missing = {}
    for command, handler in registered().items():
        if handler is None:
            continue  # an inline handler (system.*), declared by hand
        assert handler in functions, f"the scan cannot find {handler}: update this test"
        spec = declared["commands"][command]
        known = {*declared["common"], *spec["params"]}
        if spec.get("photos"):
            known |= {"uuids", "target"}
        extra = params_read(functions[handler], functions, {handler}) - known
        if extra:
            missing[command] = sorted(extra)
    assert missing == {}, f"declare these in plugin/Safelight.lrplugin/commands.json: {missing}"


@pytest.mark.parametrize(
    ("command", "params", "problem"),
    [
        ("develop.get", {"photo": "X"}, "Unknown parameter for develop.get: photo"),
        ("develop.get", {}, 'needs photos: photo ids (uuids), or target "selected"'),
        ("develop.get", {"uuids": []}, "needs photos"),
        ("develop.apply", {"uuids": ["X"], "target": "selected"}, "not both"),
        ("develop.apply", {"uuids": [], "target": "selected"}, "not both"),
        ("develop.get", {}, "restart it"),  # what an older relay sends for the selection
        ("develop.apply", {"target": "everything"}, "needs photos"),
        ("catalog.info", {"uuids": ["X"]}, "Unknown parameter for catalog.info: uuids"),
        ("brand.new", {}, "No parameters are declared for brand.new"),
    ],
)
def test_wrong_parameters_are_refused(command, params, problem):
    assert problem in (check_params(command, params) or "")


@pytest.mark.parametrize(
    ("command", "params"),
    [
        ("develop.get", {"uuids": ["X"], "keys": ["Exposure"]}),
        ("develop.get", {"target": "active"}),
        ("develop.apply", {"target": "selected", "settings": {}, "dry_run": True}),
        ("develop.apply", {"uuids": ["X"], "target": None, "settings": {}}),  # JSON null
        ("catalog.info", {}),
    ],
)
def test_right_parameters_pass(command, params):
    assert check_params(command, params) is None


def test_the_fake_catalog_refuses_instead_of_guessing():
    catalog = FakeCatalog()

    async def no_progress(*_):
        return False

    develop_get = catalog.handlers()["develop.get"]
    with pytest.raises(CommandError) as refused:
        asyncio.run(develop_get({"photo": "FAKE-0002"}, no_progress))
    assert refused.value.code == "BAD_REQUEST"
    answer = asyncio.run(develop_get({"uuids": ["FAKE-0002"]}, no_progress))
    assert answer["uuid"] == "FAKE-0002"
