"""Unit targets for the new selector and its native execution accounting."""

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import ci_engine_main_touched as ci


def _inventory():
    inventory = {}
    for node in ci.SMOKE:
        path, name = node.split("::")
        inventory.setdefault(path, {"imports": set(), "cases": {}})["cases"][name] = {
            "native": True, "private": [], "corpus": False,
        }
    inventory["tests/test_array_slice.py"] = {
        "imports": {"pineforge_codegen.codegen.array_slice"},
        "cases": {"test_alias": {"native": True, "private": [], "corpus": False}},
    }
    inventory["tests/test_drawing.py"] = {
        "imports": {"pineforge_codegen.codegen.drawing"},
        "cases": {"test_handles": {"native": True, "private": [], "corpus": False}},
    }
    return inventory


def _change(path, status="M"):
    return [{"status": status, "paths": [path]}]


def test_distinct_real_diff_inputs_change_the_selection():
    inventory = _inventory()
    docs = ci.select_tests(_change("docs/usage.md"), inventory)
    arrays = ci.select_tests(_change("pineforge_codegen/codegen/array_slice.py"), inventory)
    drawing = ci.select_tests(_change("pineforge_codegen/codegen/drawing.py"), inventory)
    assert docs["native"] is False and docs["selected"] == []
    assert "tests/test_array_slice.py::test_alias" in arrays["selected"]
    assert "tests/test_drawing.py::test_handles" not in arrays["selected"]
    assert "tests/test_drawing.py::test_handles" in drawing["selected"]
    assert "tests/test_array_slice.py::test_alias" not in drawing["selected"]
    assert set(ci.SMOKE) <= set(arrays["selected"]) & set(drawing["selected"])


@pytest.mark.parametrize("path", [
    "pineforge_codegen/codegen/emit_top.py",
    "pineforge_codegen/codegen/checked_settings.py",
    "pineforge_codegen/codegen/future_factory.py",
    "pineforge_codegen/analyzer/types.py",
    "tests/golden/matrix_eigen_pca.cpp",
    "templates/new_strategy.hpp.in",
    "templates/new_strategy.cpp.j2",
    "scripts/gen_host_members.py",
])
def test_emitter_and_generated_paths_trigger_without_a_producer_sha(path):
    assert ci.select_tests(_change(path), _inventory())["native"]


def test_nul_diff_preserves_deleted_and_both_renamed_paths():
    changes = ci.parse_diff(
        b"D\0pineforge_codegen/codegen/deleted.py\0"
        b"R100\0pineforge_codegen/codegen/old.py\0docs/moved file.md\0"
        b"A\0docs/new\nname.md\0"
    )
    result = ci.select_tests(changes, _inventory())
    assert result["native"]
    assert "pineforge_codegen/codegen/deleted.py" in result["triggers"]
    assert changes[1]["paths"] == [
        "pineforge_codegen/codegen/old.py", "docs/moved file.md",
    ]
    assert changes[2]["paths"] == ["docs/new\nname.md"]


@pytest.mark.parametrize("raw", [
    b"M\0missing-final-nul", b"R100\0only-one-path\0", b"X\0unknown\0",
    b"M\0../escape.py\0", b"M\0/absolute.cpp\0",
])
def test_malformed_diff_cannot_become_a_cheap_skip(raw):
    with pytest.raises(ValueError):
        ci.parse_diff(raw)


def test_removed_native_target_survives_but_removed_pure_test_does_not_trigger():
    baseline = _inventory()
    baseline["tests/test_pure.py"] = {
        "imports": {"pineforge_codegen.timeframe_token_cli"},
        "cases": {"test_json": {"native": False, "private": [], "corpus": False}},
    }
    remaining = {path: entry for path, entry in baseline.items()
                 if path not in {"tests/test_drawing.py", "tests/test_pure.py"}}
    inventory = ci.merge_inventory(baseline, remaining)
    native = ci.select_tests(_change("tests/test_drawing.py", "D"), inventory)
    pure = ci.select_tests(_change("tests/test_pure.py", "D"), inventory)
    assert native["native"] and "tests/test_drawing.py::test_handles" in native["selected"]
    assert pure["native"] is False and pure["selected"] == []


@pytest.mark.parametrize("path", [
    "docs/usage.md", "README.md", "tests/test_license_entity.py",
    "pineforge_codegen/request_feed_inventory.py",
    "pineforge_codegen/timeframe_token_cli.py",
])
def test_non_native_changes_have_an_explicit_empty_selection(path):
    result = ci.select_tests(_change(path), _inventory())
    assert result["native"] is False and result["selected"] == []


def test_docs_main_push_runs_the_same_smoke_that_docs_pr_skips():
    changes = _change("docs/PUBLIC_CONTRACT.md")
    pr = ci.select_tests(changes, _inventory(), "pull_request")
    main = ci.select_tests(changes, _inventory(), "push_main")
    assert pr["native"] is False and pr["selected"] == []
    assert main["native"] is True and set(ci.SMOKE) <= set(main["selected"])
    assert main["triggers"] == [] and main["needs_corpus"] is False


def test_candidate_cannot_erase_native_calls_or_authorize_a_private_exemption():
    baseline = _inventory()
    candidate = {
        "tests/test_drawing.py": {
            "imports": set(),
            "cases": {
                "test_handles": {
                    "native": False, "private": ["private_fixture"], "corpus": False,
                    "historical": True,
                },
            },
        },
    }
    merged = ci.merge_inventory(baseline, candidate)
    result = ci.select_tests(_change("tests/test_drawing.py"), merged)
    assert result["native"] and "tests/test_drawing.py::test_handles" in result["selected"]
    assert result["excluded"] == []


def test_touched_native_test_is_selected_without_a_product_module_change():
    result = ci.select_tests(_change("tests/test_drawing.py"), _inventory())
    assert result["native"]
    assert "tests/test_drawing.py::test_handles" in result["selected"]


def test_missing_mandatory_smoke_fails_closed():
    with pytest.raises(ValueError, match="mandatory smoke missing"):
        ci.select_tests(_change("pineforge_codegen/codegen/new.py"), {})


def test_ast_discovery_follows_helpers_and_separates_nonpublic_inputs(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_topic.py").write_text(
        "from tests._compile import run_emitted_tu\n"
        "def _public():\n    return run_emitted_tu('cpp', 'main', opt='-O2')\n"
        "def _private():\n    return private_fixture('secret.pine')\n"
        "def _population():\n    return getenv('PINEFORGE_STRING_SETTINGS_SOURCES')\n"
        "def test_public():\n    _public()\n"
        "def test_private():\n    _private()\n"
        "def test_population():\n    _population()\n",
        encoding="utf-8",
    )
    inventory = _inventory() | ci.test_inventory(tmp_path)
    result = ci.select_tests(_change("tests/test_topic.py"), inventory)
    assert "tests/test_topic.py::test_public" in result["selected"]
    assert "tests/test_topic.py::test_private" not in result["selected"]
    assert "tests/test_topic.py::test_population" not in result["selected"]
    assert {row["nodeid"] for row in result["excluded"]} == {
        "tests/test_topic.py::test_private", "tests/test_topic.py::test_population",
    }


def test_documenting_a_private_environment_name_does_not_exclude_a_unit_target(tmp_path):
    directory = tmp_path / "tests"
    directory.mkdir()
    (directory / "test_topic.py").write_text(
        "def test_documentation():\n"
        "    assert 'PINEFORGE_STRING_SETTINGS_SOURCES'.startswith('PINEFORGE')\n",
        encoding="utf-8",
    )
    inventory = _inventory() | ci.test_inventory(tmp_path)
    result = ci.select_tests(
        _change("pineforge_codegen/codegen/topic.py"), inventory,
    )
    assert "tests/test_topic.py::test_documentation" in result["selected"]
    assert result["excluded"] == []


def test_public_corpus_is_requested_only_when_a_selected_case_needs_it():
    inventory = _inventory()
    inventory["tests/test_array_slice.py"]["cases"]["test_alias"]["corpus"] = True
    arrays = ci.select_tests(_change("pineforge_codegen/codegen/array_slice.py"), inventory)
    drawing = ci.select_tests(_change("pineforge_codegen/codegen/drawing.py"), inventory)
    assert arrays["needs_corpus"] is True
    assert drawing["needs_corpus"] is False


def test_class_tests_and_public_replay_harness_are_discovered(tmp_path):
    directory = tmp_path / "tests"
    directory.mkdir()
    (directory / "test_replay.py").write_text(
        "from tests._e2e import execute\n"
        "class TestReplay:\n"
        "    def test_replay(self):\n"
        "        execute()\n",
        encoding="utf-8",
    )
    inventory = _inventory() | ci.test_inventory(tmp_path)
    result = ci.select_tests(_change("tests/test_replay.py"), inventory)
    assert result["native"] and result["needs_corpus"]
    assert "tests/test_replay.py::TestReplay::test_replay" in result["selected"]


@pytest.mark.parametrize("missing", [
    "include/pineforge/engine.hpp", "include/pineforge/checked_settings.hpp",
    "build/include/pineforge/version.h", "build/lib/libpineforge.a",
])
def test_each_missing_native_input_is_an_error_not_a_skip(tmp_path, monkeypatch, missing):
    files = {
        "include/pineforge/engine.hpp": b"// header\n",
        "include/pineforge/checked_settings.hpp": b"#define PF_SETTINGS_API_VERSION 1\n",
        "build/include/pineforge/version.h": b"#define PINEFORGE_VERSION_MAJOR 1\n",
        "build/lib/libpineforge.a": b"!<arch>\nmember",
        "build/_deps/eigen-src/Eigen/Dense": b"// Eigen\n",
    }
    for name, data in files.items():
        if name != missing:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    monkeypatch.setattr(ci.shutil, "which", lambda value: "/usr/bin/g++")
    with pytest.raises(ValueError, match="native input missing"):
        ci.native_environment(tmp_path, tmp_path / "build", "g++")


@pytest.mark.parametrize("value", ["0", "9", "-1", "auto", "all", "8.0", "01", ""])
def test_unbounded_or_nonnumeric_parallelism_is_refused(value):
    with pytest.raises(ValueError, match="workers"):
        ci.workers(value)


@pytest.mark.parametrize("value", ["1", "4", "8"])
def test_numeric_parallelism_is_consumed_as_an_integer(value):
    assert ci.workers(value) == int(value)


def _audit(outcome="passed", phase="call", xfail=False):
    audit = ci.ExecutionAudit()
    ids = [f"{ci.CONSTRUCTION}[{legacy}]" for legacy in ("False", "True")]
    audit.pytest_xdist_node_collection_finished(None, ids)
    for node in ids:
        report = SimpleNamespace(
            nodeid=node, when=phase, outcome=outcome, longrepr="actual report",
            failed=outcome == "failed", skipped=outcome == "skipped",
        )
        if xfail:
            report.wasxfail = "unexpected expected failure"
        audit.pytest_runtest_logreport(report)
    return audit


@pytest.mark.parametrize("outcome,phase,xfail", [
    ("skipped", "setup", False), ("skipped", "call", True),
    ("passed", "call", True), ("passed", "setup", False),
])
def test_skips_xfails_and_setup_only_reports_do_not_count_as_execution(outcome, phase, xfail):
    result = _audit(outcome, phase, xfail).result([ci.CONSTRUCTION], 0)
    assert result["audit_errors"]
    assert result["executed_count"] == 0


def test_audit_records_actual_assertion_failures_without_turning_them_green():
    result = _audit("failed").result([ci.CONSTRUCTION], 1)
    assert result["pytest_exit"] == 1
    assert result["call_outcomes"] == {"failed": 2}
    assert result["collected_count"] == result["executed_count"] == 2
    assert result["audit_errors"] == []
    assert _audit("failed").result([ci.CONSTRUCTION], 0)["audit_errors"]


def test_empty_or_partial_collection_cannot_claim_smoke():
    empty = ci.ExecutionAudit().result([ci.CONSTRUCTION], 0)
    assert "zero tests collected" in empty["audit_errors"]
    audit = _audit()
    audit.collected.remove(f"{ci.CONSTRUCTION}[True]")
    assert any("parameter missing" in text
               for text in audit.result([ci.CONSTRUCTION], 0)["audit_errors"])


def test_git_read_failure_propagates_instead_of_returning_an_empty_diff(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args[0])
    monkeypatch.setattr(ci.subprocess, "check_output", fail)
    with pytest.raises(subprocess.CalledProcessError) as error:
        ci.git(Path("."), "rev-parse", "HEAD")
    assert error.value.returncode == 128


def test_plan_uses_real_base_head_diffs_and_keeps_the_definition_separate(tmp_path, monkeypatch):
    """A docs head and an emitter head produce different plans from one base."""
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    repo = tmp_path / "candidate"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True,
        ).strip()

    git("init", "--quiet")
    git("config", "user.name", "selector unit")
    git("config", "user.email", "selector@example.invalid")
    for node in ci.SMOKE:
        path, name = node.split("::")
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(
                f"def {name}():\n"
                "    raise AssertionError('selection data must never execute')\n"
            )
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git("add", ".")
    git("commit", "--quiet", "-m", "base fixture")
    base = git("rev-parse", "HEAD")
    # Only driver identity is relocated; no candidate code is imported.
    monkeypatch.setattr(ci, "__file__", str(repo / "scripts/ci_engine_main_touched.py"))
    (repo / "README.md").write_text("real docs diff\n", encoding="utf-8")
    git("add", ".")
    git("commit", "--quiet", "-m", "docs fixture")
    docs_head = git("rev-parse", "HEAD")
    docs = ci.plan(repo, base, docs_head, docs_head, 2, tmp_path / "docs")
    assert docs["native"] is False and docs["selected"] == []
    docs_push = ci.plan(repo, base, docs_head, docs_head, 2, tmp_path / "docs-push", "push_main")
    assert docs_push["native"] and docs_push["source_event"] == "push_main"
    assert docs_push["codegen_sha"] == docs_head and docs_push["codegen_base_sha"] == base
    assert docs_push["diff_kind"] == "before_after"
    assert docs_push["plan_sha256"] != docs["plan_sha256"]
    emitter = repo / "pineforge_codegen/codegen/factory.py"
    emitter.parent.mkdir(parents=True)
    emitter.write_text("# emitter diff data, never imported\n", encoding="utf-8")
    git("add", ".")
    git("commit", "--quiet", "-m", "emitter fixture")
    native_head = git("rev-parse", "HEAD")
    advanced_branch = git("symbolic-ref", "--short", "HEAD")
    # Native candidates do not choose the trusted definition identity.
    definition_repo = tmp_path / "definition"
    subprocess.run(["git", "clone", "--quiet", str(repo), str(definition_repo)], check=True)
    subprocess.run(["git", "-C", str(definition_repo), "checkout", "--quiet", docs_head], check=True)
    monkeypatch.setattr(ci, "__file__", str(definition_repo / "scripts/ci_engine_main_touched.py"))
    native = ci.plan(repo, base, native_head, docs_head, 2, tmp_path / "native")
    assert native["native"] and set(ci.SMOKE) <= set(native["selected"])
    assert native["workflow_definition_sha"] == docs_head
    assert native["codegen_sha"] == native_head
    assert native["plan_sha256"] != docs["plan_sha256"]
    assert native["changes"] != docs["changes"]
    # The branch can move after the event; the already computed pushed head
    # remains a separate immutable input rather than a fresh main lookup.
    assert docs_push["codegen_sha"] != native_head
    git("checkout", "--quiet", "--detach", docs_head)
    retained = ci.plan(repo, base, docs_head, docs_head, 2,
                       tmp_path / "retained-event", "push_main")
    assert retained["codegen_sha"] == docs_head
    assert git("rev-parse", advanced_branch) == native_head
    git("checkout", "--quiet", "--detach", native_head)
    initial = ci.plan(repo, "0" * 40, native_head, docs_head, 2,
                      tmp_path / "initial", "push_main")
    assert initial["native"] and initial["codegen_base_tree"] is None
    assert all(change["status"] == "A" for change in initial["changes"])
