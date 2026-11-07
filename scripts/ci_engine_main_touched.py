"""Diff-selected, public-input integration with engine main (CI, not a release pair).

The workflow definition supplies this driver; the separate candidate checkout
supplies *all* product code and existing tests, including in dispatch proof mode.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys


CONSTRUCTION = (
    "tests/test_coded_run_stops.py"
    "::test_checked_construction_exposes_the_original_error"
)
SMOKE = (
    CONSTRUCTION,
    "tests/test_compile_smoke.py::test_minimal_strategy_compiles",
    "tests/test_checked_settings.py::test_metadata_and_exception_wrappers_are_emitted",
    "tests/test_checked_settings.py::test_checked_values_and_receipt_at_runtime",
)
CONTROL_PATHS = {
    ".github/workflows/engine-main-touched.yml",
    "scripts/ci_engine_main_touched.py",
    "tests/test_ci_engine_main_touched.py",
    "scripts/gen_host_members.py",
}
NATIVE_CALLS = {
    "compile_cpp", "run_emitted_tu", "narrowing_diagnostics",
    "skip_if_no_compile_env", "skip_if_no_engine_lib", "skip_unless_e2e_env",
}
PRIVATE_INPUTS = {
    "private_fixture", "PINEFORGE_BTCUSDT_1H_FEED",
    "PINEFORGE_STRING_SETTINGS_SOURCES",
}
CPP_SUFFIXES = (
    ".cpp", ".cc", ".cxx", ".c", ".hpp", ".hh", ".hxx", ".h", ".inl", ".tpp", ".ipp",
    ".cpp.in", ".hpp.in", ".h.in", ".cpp.j2", ".hpp.j2",
    ".cc.in", ".cxx.in", ".hxx.in", ".tpl", ".tmpl", ".template", ".j2", ".jinja", ".jinja2",
)


def workers(value: str) -> int:
    if not re.fullmatch(r"[1-8]", value):
        raise ValueError("workers must be a decimal integer from 1 through 8")
    return int(value)


def commit_ref(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("codegen refs must be full lowercase 40-hex commit IDs")
    return value


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True,
    ).strip()


def parse_diff(raw: bytes) -> list[dict]:
    """Read --name-status -z, retaining BOTH paths of renames/copies."""
    if raw and not raw.endswith(b"\0"):
        raise ValueError("unterminated Git diff")
    fields = raw.decode("utf-8", errors="strict").split("\0")[:-1]
    changes = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not re.fullmatch(r"(?:[ADMT]|[RC][0-9]{1,3})", status):
            raise ValueError(f"invalid Git diff status: {status!r}")
        count = 2 if status.startswith(("R", "C")) else 1
        paths = fields[index:index + count]
        if len(paths) != count or not all(paths):
            raise ValueError("incomplete Git diff record")
        for path in paths:
            if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
                raise ValueError(f"unsafe diff path: {path!r}")
        changes.append({"status": status, "paths": paths})
        index += count
    return changes


def native_path(path: str) -> bool:
    return (
        path.startswith("pineforge_codegen/")
        or path in CONTROL_PATHS
        or path in {"tests/_compile.py", "tests/_e2e.py"}
        or path.lower().endswith(CPP_SUFFIXES)
    )


def identifiers(node: ast.AST) -> set[str]:
    names = {
        child.id if isinstance(child, ast.Name) else child.attr
        for child in ast.walk(node)
        if isinstance(child, (ast.Name, ast.Attribute))
    }
    names.update(child.arg for child in ast.walk(node) if isinstance(child, ast.arg))
    return names


def test_inventory(repo: Path) -> dict[str, dict]:
    """Static discovery only: never import a candidate while deciding changes."""
    inventory = {}
    for path in sorted((repo / "tests").glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef) and cls.name.startswith("Test"):
                functions.update({
                    f"{cls.name}::{node.name}": node for node in cls.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                })
        helpers = {name: identifiers(node) for name, node in functions.items()}
        imports = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imports.update(
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        )
        cases = {}
        for name, node in functions.items():
            if not name.rsplit("::", 1)[-1].startswith("test_"):
                continue
            names = identifiers(node)
            pending = list(names & helpers.keys())
            seen = set()
            while pending:
                helper = pending.pop()
                if helper in seen:
                    continue
                seen.add(helper)
                names.update(helpers[helper])
                pending.extend((helpers[helper] & helpers.keys()) - seen)
            private = sorted(names & PRIVATE_INPUTS)
            # String-named environment reads also carry input requirements.
            dependency_nodes = [node, *(functions[helper] for helper in seen)]
            texts = [
                child.value for dependency in dependency_nodes for child in ast.walk(dependency)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            ]
            env_reads = set()
            for dependency in dependency_nodes:
                for child in ast.walk(dependency):
                    if isinstance(child, ast.Call) and child.args and (
                        isinstance(child.func, ast.Name) and child.func.id == "getenv"
                        or isinstance(child.func, ast.Attribute) and child.func.attr in {"get", "getenv"}
                    ):
                        key = child.args[0]
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            env_reads.add(key.value)
                    if isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant):
                        if isinstance(child.value, ast.Attribute) and child.value.attr == "environ":
                            env_reads.add(child.slice.value)
            private.extend(sorted(env_reads & PRIVATE_INPUTS))
            corpus = (
                "skip_unless_e2e_env" in names
                or any("corpus" in item.lower() for item in names)
                or any("PINEFORGE_ENGINE_CORPUS" in text for text in texts)
                or any(module.startswith("tests._e2e") for module in imports)
            )
            cases[name] = {
                "native": bool(names & NATIVE_CALLS or "tests._e2e" in imports),
                "private": sorted(set(private)),
                "corpus": corpus,
                "historical": "reference_codegen" in names,
            }
        inventory[path.relative_to(repo).as_posix()] = {
            "imports": imports, "cases": cases,
        }
    return inventory


def select_tests(changes: list[dict], inventory: dict[str, dict]) -> dict:
    paths = sorted({path for change in changes for path in change["paths"]})
    changed_tests = {path for path in paths if path in inventory}
    native_tests = {
        path for path in changed_tests
        if any(case["native"] for case in inventory[path]["cases"].values())
    }
    removed_tests = {
        path for path in paths
        if path.startswith("tests/test_") and path.endswith(".py") and path not in inventory
    }
    triggers = [
        path for path in paths if native_path(path) or path in native_tests or path in removed_tests
    ]
    if not triggers:
        return {
            "native": False, "reason": "No emitter, generated C++, native harness/test or CI driver changes",
            "changes": changes, "triggers": [], "selected": [], "excluded": [],
            "needs_corpus": False,
        }

    modules = {
        path[:-3].replace("/", ".") for path in paths
        if path.startswith("pineforge_codegen/") and path.endswith(".py")
    }
    topics = {
        re.sub(r"^(emit_|visit_|test_)", "", PurePosixPath(path).stem)
        for path in paths if path.startswith("pineforge_codegen/")
    } - {"__init__", "base", "types", "helpers", "tables"}
    selected = {node: ["mandatory public integration smoke"] for node in SMOKE}
    excluded = [
        {"nodeid": path, "reason": "test file absent at tested head; native smoke retained"}
        for path in sorted(removed_tests)
    ]
    needs_corpus = False
    for path, entry in inventory.items():
        reasons = []
        if path in changed_tests:
            reasons.append("changed test file")
        if entry["imports"] & modules:
            reasons.append("imports a changed module")
        if any(f"_{topic}_" in f"_{PurePosixPath(path).stem}_" for topic in topics):
            reasons.append("test filename matches changed module topic")
        if path == "tests/test_codegen_golden.py" and any(
            name.startswith("tests/golden/") for name in paths
        ):
            reasons.append("generated C++ golden input changed")
        if path == "tests/test_script_run_prepare.py" and any(
            name.endswith(("/emit_top.py", "/base.py", "/checked_settings.py"))
            for name in paths
        ):
            reasons.append("constructor/lifecycle emitter changed")
        if not reasons:
            continue
        for name, case in entry["cases"].items():
            node = f"{path}::{name}"
            if case["private"]:
                excluded.append({"nodeid": node, "reason": "non-public inputs",
                                 "inputs": case["private"]})
                continue
            if case.get("historical"):
                excluded.append({"nodeid": node, "reason": "historical comparison outside touched scope"})
                continue
            selected.setdefault(node, []).extend(reasons)
            needs_corpus |= case["corpus"]
    for node in SMOKE:
        path, name = node.split("::")
        if name not in inventory.get(path, {}).get("cases", {}):
            raise ValueError(f"mandatory smoke missing from tested checkout: {node}")
    return {
        "native": True, "reason": "Actual diff touches native integration inputs",
        "changes": changes, "triggers": triggers,
        "selected": sorted(selected), "selection_reasons": selected,
        "excluded": excluded, "needs_corpus": needs_corpus,
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def summary(message: str) -> None:
    print(message, flush=True)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(message + "\n\n")


def plan(repo: Path, base: str, head: str, definition: str, count: int, out: Path) -> dict:
    base, head, definition = map(commit_ref, (base, head, definition))
    definition_repo = Path(__file__).resolve().parents[1]
    if git(definition_repo, "rev-parse", "HEAD") != definition:
        raise ValueError("workflow driver checkout differs from workflow definition SHA")
    if git(repo, "rev-parse", "HEAD") != head:
        raise ValueError("tested checkout HEAD differs from requested codegen head")
    for sha in (base, head):
        if git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}") != sha:
            raise ValueError("unresolvable codegen input")
    raw = subprocess.check_output([
        "git", "-C", str(repo), "diff", "--no-ext-diff", "--no-textconv",
        "--find-renames", "--ignore-submodules=none", "--name-status", "-z",
        f"{base}...{head}", "--",
    ])
    result = select_tests(parse_diff(raw), test_inventory(repo))
    result.update({
        "codegen_base_sha": base, "codegen_sha": head,
        "codegen_tree": git(repo, "rev-parse", "HEAD^{tree}"),
        "diff_merge_base": git(repo, "merge-base", base, head),
        "workflow_definition_sha": definition, "workers": count,
        "workflow_definition_tree": git(definition_repo, "rev-parse", "HEAD^{tree}"),
    })
    write_json(out / "selection.json", result)
    (out / "selected-tests.txt").write_text(
        "".join(node + "\n" for node in result["selected"]), encoding="utf-8",
    )
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            for key, value in {
                "native": str(result["native"]).lower(),
                "base": base, "head": head, "definition": definition, "workers": count,
            }.items():
                stream.write(f"{key}={value}\n")
    summary(
        f"{'RUN' if result['native'] else 'SKIP native'}: {result['reason']}\n\n"
        f"Workflow definition `{definition}`; tested codegen `{head}` "
        f"(tree `{result['codegen_tree']}`); base `{base}`; "
        f"merge base `{result['diff_merge_base']}`.\n\n"
        f"Triggers: `{json.dumps(result['triggers'])}`. "
        f"Selected function selectors: {len(result['selected'])}; "
        f"public corpus required: {result['needs_corpus']}. "
        f"Excluded input-dependent cases: `{json.dumps(result['excluded'])}`."
    )
    return result


def logged(command: list[str], log: Path, *, cwd: Path | None = None) -> None:
    """Stream the real command and retain its exit; never use a shell pipeline."""
    with log.open("a", encoding="utf-8") as stream:
        line = "$ " + shlex.join(command)
        print(line, flush=True)
        stream.write(line + "\n")
        with subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
                stream.write(line)
            code = process.wait()
        stream.write(f"\nexit={code}\n")
        print(f"exit={code}", flush=True)
    if code:
        raise subprocess.CalledProcessError(code, command)


def native_environment(engine: Path, build: Path, compiler: str) -> dict[str, str]:
    resolved = shutil.which(compiler)
    if resolved is None:
        raise ValueError(f"native compiler missing: {compiler}")
    eigen = next((path for path in (
        build / "_deps/eigen-src", Path("/usr/include/eigen3"),
    ) if (path / "Eigen/Dense").is_file()), None)
    if eigen is None:
        raise ValueError("real Eigen headers missing")
    env = {
        "PINEFORGE_ENGINE_INCLUDE": str(engine / "include"),
        "PINEFORGE_GENERATED_INCLUDE": str(build / "include"),
        "PINEFORGE_ENGINE_LIB": str(build / "lib/libpineforge.a"),
        "PINEFORGE_EIGEN_INCLUDE": str(eigen),
        "CXX": resolved,
    }
    required = (
        engine / "include/pineforge/engine.hpp",
        engine / "include/pineforge/checked_settings.hpp",
        build / "include/pineforge/version.h",
        build / "lib/libpineforge.a",
    )
    for path in required:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"required native input missing or empty: {path}")
    if re.search(r"@[A-Z][A-Z0-9_]*@", required[2].read_text(encoding="utf-8")):
        raise ValueError("generated version header contains unresolved template fields")
    with required[3].open("rb") as archive:
        if archive.read(8) != b"!<arch>\n":
            raise ValueError("runtime is not a static archive")
    return env


class ExecutionAudit:
    """Controller-side hooks also receive xdist's collection and test reports."""

    def __init__(self) -> None:
        self.collected: set[str] = set()
        self.reports: list[dict] = []

    def pytest_xdist_node_collection_finished(self, node, ids) -> None:
        self.collected.update(ids)

    def pytest_runtest_logreport(self, report) -> None:
        self.reports.append({
            "nodeid": report.nodeid, "when": report.when,
            "outcome": report.outcome, "xfail": hasattr(report, "wasxfail"),
            "detail": str(report.longrepr) if report.failed or report.skipped else "",
        })

    def result(self, selected: list[str], exit_code: int) -> dict:
        errors = []
        if not self.collected:
            errors.append("zero tests collected")
        for selector in selected:
            if not any(node == selector or node.startswith(selector + "[")
                       or node.startswith(selector + "::") for node in self.collected):
                errors.append(f"selected target did not collect: {selector}")
        for legacy in ("False", "True"):
            if f"{CONSTRUCTION}[{legacy}]" not in self.collected:
                errors.append(f"checked-construction smoke parameter missing: {legacy}")
        executed = {
            row["nodeid"] for row in self.reports
            if row["when"] == "call" and row["outcome"] in {"passed", "failed"}
            and not row["xfail"]
        }
        for node in sorted(self.collected - executed):
            errors.append(f"selected case did not execute an ordinary test call: {node}")
        skips = [row for row in self.reports if row["outcome"] == "skipped" or row["xfail"]]
        if skips:
            errors.append("selected public tests skipped or xfailed")
        return {
            "pytest_exit": exit_code, "audit_errors": errors,
            "collected_count": len(self.collected), "executed_count": len(executed),
            "call_outcomes": dict(Counter(
                row["outcome"] for row in self.reports if row["when"] == "call"
            )),
            "skips": skips, "collected_nodeids": sorted(self.collected),
            "reports": self.reports,
        }


def run_native(repo: Path, engine: Path, out: Path, selection: dict) -> int:
    if not selection["native"] or not selection["selected"]:
        raise ValueError("native job requires a nonempty native selection")
    if git(repo, "rev-parse", "HEAD") != selection["codegen_sha"]:
        raise ValueError("codegen checkout changed after selection")
    engine_sha = git(engine, "rev-parse", "HEAD")
    if engine_sha != git(engine, "rev-parse", "refs/remotes/origin/main"):
        raise ValueError("engine checkout is not the main resolved by this run")
    identity = {
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "workflow_definition_sha": selection["workflow_definition_sha"],
        "workflow_definition_tree": selection["workflow_definition_tree"],
        "codegen_sha": selection["codegen_sha"], "codegen_tree": selection["codegen_tree"],
        "engine_ref": "refs/heads/main", "engine_sha": engine_sha,
        "engine_tree": git(engine, "rev-parse", "HEAD^{tree}"),
    }
    write_json(out / "native-identity.json", identity)
    summary("Native checkout identity:\n\n```json\n" + json.dumps(identity, indent=2) + "\n```")
    build = engine / "build"
    if build.exists():
        raise ValueError("fresh engine build directory already exists")
    compiler = os.environ.get("CXX", "/usr/bin/g++")
    if shutil.which(compiler) is None:
        raise ValueError(f"native compiler missing: {compiler}")
    count = str(workers(str(selection["workers"])))
    logged([
        "cmake", "-S", str(engine), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
        "-DPINEFORGE_BUILD_TESTS=OFF", "-DPINEFORGE_BUILD_SOURCE_LAYER=ON",
        f"-DCMAKE_CXX_COMPILER={compiler}",
    ], out / "build.log")
    logged(["cmake", "--build", str(build), "--parallel", count], out / "build.log")
    env = native_environment(engine, build, compiler)
    if selection["needs_corpus"]:
        logged(["git", "-C", str(engine), "submodule", "update", "--init", "corpus"],
               out / "corpus.log")
        logged(["git", "-C", str(engine / "corpus"), "lfs", "pull"], out / "corpus.log")
        corpus = engine / "corpus"
        if not list(corpus.glob("*/*/strategy.pine")):
            raise ValueError("selected corpus tests require public strategies")
        feed = corpus / "data/ohlcv_ETH-USDT-USDT_1m.csv"
        if not feed.is_file():
            raise ValueError("selected public replay inputs missing")
        with feed.open("rb") as stream:
            if stream.read(128).startswith(b"version https://git-lfs.github.com/spec/"):
                raise ValueError("public replay feed is an unsmudged LFS pointer")
        if not (engine / "scripts/run_strategy.py").is_file():
            raise ValueError("public engine replay driver missing")
        env["PINEFORGE_ENGINE_CORPUS"] = str(corpus)
    os.environ.update(env)
    identity["native_environment"] = env
    identity["generated_header_sha256"] = hashlib.sha256(
        (build / "include/pineforge/version.h").read_bytes()
    ).hexdigest()
    write_json(out / "native-identity.json", identity)
    summary("Native paths:\n\n```json\n" + json.dumps(env, indent=2) + "\n```")
    logged([env["CXX"], "--version"], out / "preflight.log")
    # This prevents the existing regression's #ifdef branch silently vanishing.
    probe = out / "native-contract.cpp"
    probe.write_text(
        "#include <pineforge/version.h>\n#include <pineforge/checked_settings.hpp>\n"
        "#ifndef PF_SETTINGS_API_VERSION\n#error checked settings API is required\n#endif\n",
        encoding="utf-8",
    )
    logged([
        env["CXX"], "-std=c++17", "-fsyntax-only",
        "-I", env["PINEFORGE_ENGINE_INCLUDE"],
        "-I", env["PINEFORGE_GENERATED_INCLUDE"],
        "-I", env["PINEFORGE_EIGEN_INCLUDE"], str(probe),
    ], out / "preflight.log")

    os.chdir(repo)
    sys.path.insert(0, str(repo))
    import pineforge_codegen
    if Path(pineforge_codegen.__file__).resolve().parent != repo / "pineforge_codegen":
        raise ValueError("imported codegen is not the tested checkout")
    import pytest
    audit = ExecutionAudit()
    # Disable environment-supplied options that could deselect or xfail smoke.
    os.environ.pop("PYTEST_ADDOPTS", None)
    args = [
        "-o", "addopts=", "-v", "-ra", "-n", count,
        "--junitxml", str(out / "junit.xml"), *selection["selected"],
    ]
    with (out / "pytest.log").open("w", encoding="utf-8") as stream:
        class Tee:
            def __init__(self, original):
                self.original = original

            def write(self, text):
                self.original.write(text)
                stream.write(text)

            def flush(self):
                self.original.flush()
                stream.flush()

            def __getattr__(self, name):
                return getattr(self.original, name)

        stdout, stderr = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = Tee(stdout), Tee(stderr)
            print("pytest arguments: " + shlex.join(args), flush=True)
            code = int(pytest.main(args, plugins=[audit]))
        finally:
            sys.stdout, sys.stderr = stdout, stderr
    result = audit.result(selection["selected"], code)
    write_json(out / "execution.json", result)
    summary(
        f"pytest exit={code}; collected={result['collected_count']}; "
        f"executed={result['executed_count']}; call outcomes={result['call_outcomes']}; "
        f"skips={len(result['skips'])}; audit errors={json.dumps(result['audit_errors'])}"
    )
    return code if code else (1 if result["audit_errors"] else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "run"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--definition", required=True)
    parser.add_argument("--workers", default="8")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--engine", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        repo = args.repo.resolve()
        selection = plan(repo, args.base, args.head, args.definition, workers(args.workers), out)
        if args.mode == "plan":
            return 0
        if args.engine is None:
            raise ValueError("--engine is required for run")
        return run_native(repo, args.engine.resolve(), out, selection)
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as error:
        code = error.returncode if isinstance(error, subprocess.CalledProcessError) else 1
        write_json(out / "failure.json", {"error": str(error), "exit": code})
        summary(f"Native integration refused/failed: {error}; exit={code}")
        return code


if __name__ == "__main__":
    raise SystemExit(main())
