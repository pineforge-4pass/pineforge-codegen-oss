"""Where an imported library's source comes from (``pine_libraries``).

``transpile(source, libraries={path: text})`` takes the sources from the
caller. With ``libraries=None`` they come from the environment the case
runner sets (pineforge-workflow ``docs/xsym-requests.md``, "The environment
contract"): a case-wide ``PINEFORGE_PINE_LIBRARIES`` directory holding every
library any probe of the case pins, and one
``PINEFORGE_REQUESTS_ROOT/<slug>/requests.json`` per probe. A script resolves
its imports ONLY through its own manifest (review XSYM-B P2-3): otherwise a
probe pinning nothing would transpile on a shard neighbour's pin and measure
differently by case composition.

The frozen verifier (pineforge-lab 3bac0b7b ``scripts/verify-engine-local.py``
line 1698) calls ``transpile((d / "strategy.pine").read_text())``: no slug, no
options. So the manifest is found by content: the one whose
``probe.strategySha256`` is the sha256 of the script. That sha is of the
file's bytes, and ``read_text()`` folds CRLF and CR line ends to LF, so the
text matches the bytes as read, or with every LF spelled CRLF, or CR; a file
mixing line ends matches none. The sources are the clean-room fixtures of
``fixtures/pine_libraries``.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level
from pineforge_codegen.pine_libraries import library_resolver, source_digests
from tests._pine_libraries import Layout, library_sources, library_text, sha256

SCRIPT = """//@version=6
strategy("T")
import pftest/Signals/1 as S
x = S.scaled(close)
plot(x)
if S.pin() == "BUY"
    strategy.entry("L", strategy.long)
"""

TODAY = "Import is not supported: 'pftest/Signals/1 as S'"


def _messages(script: str, **kw) -> list[str]:
    with pytest.raises(CompileError) as err:
        transpile(script, **kw)
    return [d.message for d in err.value.diagnostics if d.level == Level.ERROR]


@pytest.fixture
def env(monkeypatch):
    def apply(values: dict[str, str]) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
    for key in ("PINEFORGE_PINE_LIBRARIES", "PINEFORGE_REQUESTS_ROOT"):
        monkeypatch.delenv(key, raising=False)
    return apply


def test_without_sources_the_refusal_is_todays(env):
    assert _messages(SCRIPT) == [TODAY]


def test_pinned_libraries_resolve(env, tmp_path):
    layout = Layout(tmp_path)
    env(layout.pin_all("probe", SCRIPT, "pftest/Signals/1", "pftest/Base/1"))
    resolver = library_resolver(SCRIPT, None)
    assert resolver.configured
    assert resolver.load("pftest/Signals/1").text == library_text("pftest/Signals/1")
    assert resolver.load("pftest/Base/1").sha256 == sha256(
        library_text("pftest/Base/1").encode())


def test_a_script_transpiles_on_its_own_pins(env, tmp_path):
    """The verifier's path: ``transpile(source_text)`` with no options."""
    layout = Layout(tmp_path)
    env(layout.pin_all("probe", SCRIPT, "pftest/Signals/1", "pftest/Base/1"))
    cpp = transpile(SCRIPT)
    assert "Signals_v1__scaled" in cpp and "Base_v1__twice" in cpp


def test_a_neighbours_pin_resolves_nothing(env, tmp_path):
    """The case-wide directory lists the library (another probe pins it),
    but no manifest pins this script: its import keeps its refusal."""
    layout = Layout(tmp_path)
    digest = layout.add_library("pftest/Signals/1")
    layout.add_library("pftest/Base/1")
    layout.add_probe("neighbour", b"//@version=6\nstrategy(\"N\")\n",
                     {"pftest/Signals/1": digest})
    env(layout.env)
    assert _messages(SCRIPT) == [
        TODAY + ": no requests manifest under $PINEFORGE_REQUESTS_ROOT pins "
                "this script's source"]


def test_two_manifests_of_one_source_resolve_nothing(env, tmp_path):
    layout = Layout(tmp_path)
    pins = {"pftest/Signals/1": layout.add_library("pftest/Signals/1"),
            "pftest/Base/1": layout.add_library("pftest/Base/1")}
    layout.add_probe("a", SCRIPT.encode(), pins)
    layout.add_probe("b", SCRIPT.encode(), pins)
    env(layout.env)
    assert _messages(SCRIPT) == [
        TODAY + ": 2 requests manifests under $PINEFORGE_REQUESTS_ROOT pin "
                "this script's source (a, b)"]


def test_libraries_without_requests_root_resolve_nothing(env, tmp_path):
    layout = Layout(tmp_path)
    layout.add_library("pftest/Signals/1")
    env({"PINEFORGE_PINE_LIBRARIES": str(layout.libraries)})
    (message,) = _messages(SCRIPT)
    assert message.startswith(TODAY + ": $PINEFORGE_PINE_LIBRARIES is set but "
                                      "$PINEFORGE_REQUESTS_ROOT is not")


@pytest.mark.parametrize("line_end", ["\n", "\r\n", "\r"])
def test_the_manifest_is_found_by_the_files_bytes(env, tmp_path, line_end):
    """``read_text()`` hands transpile() LF text whatever the file's line
    ends; the manifest's sha is of the file's bytes."""
    layout = Layout(tmp_path)
    pins = {"pftest/Signals/1": layout.add_library("pftest/Signals/1"),
            "pftest/Base/1": layout.add_library("pftest/Base/1")}
    layout.add_probe("probe", SCRIPT.replace("\n", line_end).encode(), pins)
    env(layout.env)
    assert library_resolver(SCRIPT, None).configured
    (tmp_path / "strategy.pine").write_bytes(SCRIPT.replace("\n", line_end).encode())
    as_read = (tmp_path / "strategy.pine").read_text()
    assert as_read == SCRIPT
    assert library_resolver(as_read, None).configured


def test_mixed_line_ends_match_no_manifest(env, tmp_path):
    layout = Layout(tmp_path)
    mixed = SCRIPT.replace("\n", "\r\n", 1)
    layout.add_probe("probe", mixed.encode(),
                     {"pftest/Signals/1": layout.add_library("pftest/Signals/1")})
    env(layout.env)
    assert not library_resolver(mixed.replace("\r\n", "\n"), None).configured
    assert sha256(mixed.encode()) not in source_digests(SCRIPT)


def test_an_import_the_manifest_does_not_pin_is_refused_by_name(env, tmp_path):
    """libraries.json lists pftest/Base/1 (a neighbour pins it), but this
    probe's manifest pins pftest/Signals/1 only: its import of Base fails."""
    layout = Layout(tmp_path)
    layout.add_library("pftest/Base/1")
    layout.add_probe("probe", SCRIPT.encode(),
                     {"pftest/Signals/1": layout.add_library("pftest/Signals/1")})
    env(layout.env)
    assert _messages(SCRIPT) == [
        "Import is not supported: 'pftest/Base/1': library 'pftest/Base/1' is "
        "not pinned by this script's requests manifest (probe/requests.json)"]


def test_a_source_that_does_not_match_its_sha_is_refused(env, tmp_path):
    layout = Layout(tmp_path)
    layout.add_library("pftest/Base/1")
    good = sha256(library_text("pftest/Signals/1").encode())
    layout.add_library("pftest/Signals/1", data=b"//@version=6\nlibrary(\"X\")\n")
    layout.add_probe("probe", SCRIPT.encode(), {
        "pftest/Signals/1": good,
        "pftest/Base/1": sha256(library_text("pftest/Base/1").encode())})
    env(layout.env)
    (message,) = _messages(SCRIPT)
    assert message.startswith(
        "Import is not supported: 'pftest/Signals/1': library 'pftest/Signals/1' "
        f"source does not match its pinned sha256 {good[:12]}...")


def test_a_missing_source_is_refused(env, tmp_path):
    layout = Layout(tmp_path)
    layout.add_probe("probe", SCRIPT.encode(), {"pftest/Signals/1": "a" * 64})
    env(layout.env)
    (message,) = _messages(SCRIPT)
    assert message.startswith(
        "Import is not supported: 'pftest/Signals/1': library 'pftest/Signals/1' "
        "source is missing")


@pytest.mark.parametrize("where", ["manifest", "libraries.json"])
def test_a_library_that_is_not_open_is_refused(env, tmp_path, where):
    layout = Layout(tmp_path)
    access = {"manifest": "open_no_auth", "libraries.json": "protected"}
    digest = layout.add_library("pftest/Signals/1", access=access[where])
    layout.add_library("pftest/Base/1")
    layout.add_probe("probe", SCRIPT.encode(), {
        "pftest/Signals/1": digest,
        "pftest/Base/1": sha256(library_text("pftest/Base/1").encode())},
        access="closed" if where == "manifest" else "open_no_auth")
    env(layout.env)
    (message,) = _messages(SCRIPT)
    assert "library 'pftest/Signals/1' is not open-source" in message
    assert "PineForge inlines open libraries only" in message


def test_the_probes_own_file_wins_over_the_case_wide_one(env, tmp_path):
    """``<slug>/files/<sha256>`` holds the probe's own bytes; the case-wide
    file of the same path may hold other bytes."""
    layout = Layout(tmp_path)
    layout.add_library("pftest/Base/1")
    layout.add_library("pftest/Signals/1", data=b"not these bytes")
    data = library_text("pftest/Signals/1").encode()
    layout.add_probe("probe", SCRIPT.encode(), {
        "pftest/Signals/1": sha256(data),
        "pftest/Base/1": sha256(library_text("pftest/Base/1").encode())},
        files={sha256(data): data})
    env(layout.env)
    assert library_resolver(SCRIPT, None).load("pftest/Signals/1").text == data.decode()


def test_the_argument_supplies_the_sources(env):
    resolver = library_resolver(SCRIPT, library_sources("pftest/Signals/1"))
    assert resolver.configured and resolver.pinned("pftest/Signals/1")
    assert not resolver.pinned("pftest/Base/1")
    assert _messages(SCRIPT, libraries=library_sources("pftest/Signals/1")) == [
        "Import is not supported: 'pftest/Base/1': library 'pftest/Base/1' is "
        "not among the libraries passed to transpile()"]


def test_the_argument_overrides_the_environment(env, tmp_path):
    layout = Layout(tmp_path)
    layout.add_library("pftest/Signals/1")
    env(layout.env)
    resolver = library_resolver(SCRIPT, {"pftest/Base/1": library_text("pftest/Base/1")})
    assert resolver.pinned("pftest/Base/1") and not resolver.pinned("pftest/Signals/1")
