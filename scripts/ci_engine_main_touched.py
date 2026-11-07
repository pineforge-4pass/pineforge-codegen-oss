"""Diff-selected, public-input integration with engine main (CI, not a release pair).

The trusted base definition supplies this driver and selection policy. Git
objects at admitted base/head are data; only the native job imports the tested
head's product and tests. Dispatch uses the identical implementation.
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
    ".github/workflows/gke-pr.yml",
    ".github/workflows/engine-main-touched.yml",
    "scripts/ci_engine_main_touched.py",
    "tests/test_ci_engine_main_touched.py",
    "scripts/gen_host_members.py",
}
# These stages can change emitted C++, its refusal/diagnostic path, or factory
# inputs. Metadata-only request inventory and the timeframe CLI are outside this
# list; an unknown product module fails closed to native work below.
SHARED_EMITTER_MODULES = {
    "__init__", "array_binding", "ast_nodes", "block_locals", "builtin_keywords",
    "collection_history", "cpp_allowlist", "cpp_names", "diagnostic_codes",
    "drawing_all", "errors", "external_requests", "finite_ta_length",
    "function_overloads", "input_keys", "intrinsic_binding", "lexer",
    "library_inline", "library_modules", "library_v5", "limits", "loop_types",
    "matrix_overloads", "method_binding", "parser", "pine_libraries",
    "pine_spelling", "pragmas", "request_discovery", "security_contexts", "session_reads",
    "signatures", "support_checker", "symbols", "tokens", "tv_input_choices",
}
NON_NATIVE_PRODUCT_PATHS = {
    "pineforge_codegen/request_feed_inventory.py",  # metadata after generation
    "pineforge_codegen/timeframe_token_cli.py",  # CLI around timeframe validation
}
NATIVE_TEST_PATHS = {
    "tests/test_compile_corpus.py", "tests/test_host_member_names.py",
}
NATIVE_HARNESSES = {
    "tests/_compile.py", "tests/_e2e.py", "tests/_legacy_cpp.py",
    "tests/_cpp_tokens.py", "tests/_emitted_member_assertions.py",
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
    product = PurePosixPath(path)
    return (
        path.startswith(("pineforge_codegen/codegen/", "pineforge_codegen/analyzer/"))
        or (path.startswith("pineforge_codegen/") and (
            product.stem in SHARED_EMITTER_MODULES
            or product.name.startswith("emit_")
            or any(word in product.stem for word in ("factory", "creation", "checked_settings"))
            or path not in NON_NATIVE_PRODUCT_PATHS
        ))
        or path in CONTROL_PATHS
        or path in NATIVE_HARNESSES
        or path in NATIVE_TEST_PATHS
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


def test_inventory(repo: Path, ref: str | None = None) -> dict[str, dict]:
    """Static discovery; CI reads regular Git blobs, never candidate modules."""
    inventory = {}
    if ref is None:  # unit fixtures; production always supplies an immutable ref
        sources = {
            path.relative_to(repo).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted((repo / "tests").rglob("test_*.py"))
        }
    else:
        commit_ref(ref)
        entries = subprocess.check_output([
            "git", "-C", str(repo), "ls-tree", "-rz", ref, "--", "tests",
        ]).decode("utf-8", errors="strict")
        sources = {}
        for record in entries.split("\0"):
            if not record:
                continue
            metadata, path = record.split("\t", 1)
            if not re.fullmatch(r"tests/(?:[A-Za-z0-9_.-]+/)*test_[A-Za-z0-9_]+\.py", path):
                continue
            mode, kind, _ = metadata.split()
            if mode not in {"100644", "100755"} or kind != "blob":
                raise ValueError(f"test source is not a regular Git blob: {path}")
            source = subprocess.check_output([
                "git", "-C", str(repo), "show", f"{ref}:{path}",
            ])
            if len(source) > 1024 * 1024:
                raise ValueError(f"test source exceeds 1 MiB: {path}")
            sources[path] = source.decode("utf-8", errors="strict")
    for path, source in sources.items():
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
            f"{node.module}.{alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names if alias.name != "*"
        )
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
                "native": bool(
                    names & NATIVE_CALLS or imports & {"tests._compile", "tests._e2e"}
                    or path in NATIVE_TEST_PATHS or path.startswith("tests/test_e2e_")
                ),
                "private": sorted(set(private)),
                "corpus": corpus,
                "historical": "reference_codegen" in names,
            }
        inventory[path] = {
            "imports": imports, "cases": cases,
        }
    return inventory


def merge_inventory(trusted: dict, head: dict) -> dict:
    """Head data may add requirements/targets, never erase or exempt base ones."""
    result = {}
    for path in sorted(trusted.keys() | head.keys()):
        old = trusted.get(path, {"imports": set(), "cases": {}})
        new = head.get(path, {"imports": set(), "cases": {}})
        cases = {}
        for name in sorted(old["cases"].keys() | new["cases"].keys()):
            base_case = old["cases"].get(name)
            head_case = new["cases"].get(name, {})
            cases[name] = {
                "native": bool((base_case or {}).get("native") or head_case.get("native")),
                "corpus": bool((base_case or {}).get("corpus") or head_case.get("corpus")),
                # Only installed/base case metadata can authorize exclusions.
                "private": (base_case or {}).get("private", []),
                "historical": (base_case or {}).get("historical", False),
            }
        result[path] = {"imports": old["imports"] | new["imports"], "cases": cases}
    return result


def select_tests(changes: list[dict], inventory: dict[str, dict],
                 source_event: str = "pull_request") -> dict:
    if source_event not in {"pull_request", "push_main"}:
        raise ValueError("unknown source event")
    paths = sorted({path for change in changes for path in change["paths"]})
    changed_tests = {path for path in paths if path in inventory}
    native_tests = {
        path for path in changed_tests
        if any(case["native"] for case in inventory[path]["cases"].values())
    }
    triggers = [
        path for path in paths if native_path(path) or path in native_tests
    ]
    if not triggers and source_event == "pull_request":
        return {
            "native": False, "reason": "No emitter, generated C++, native harness/test or CI driver changes",
            "changes": changes, "triggers": [], "selected": [], "excluded": [],
            "needs_corpus": False,
        }

    modules = {
        path[:-3].replace("/", ".") for path in paths
        if path.endswith(".py") and (
            path.startswith("pineforge_codegen/") or path in NATIVE_HARNESSES
        )
    }
    topics = {
        re.sub(r"^(emit_|visit_|test_)", "", PurePosixPath(path).stem)
        for path in paths if path.startswith("pineforge_codegen/")
    } - {"__init__", "base", "types", "helpers", "tables"}
    selected = {node: ["mandatory public integration smoke"] for node in SMOKE}
    excluded = []
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
            selected.setdefault(node, []).extend(reasons)
            needs_corpus |= case["corpus"]
    for node in SMOKE:
        path, name = node.split("::")
        if name not in inventory.get(path, {}).get("cases", {}):
            raise ValueError(f"mandatory smoke missing from tested checkout: {node}")
    return {
        "native": True, "reason": (
            "Every main push requires native touched tests and smoke"
            if source_event == "push_main" else "Actual diff touches native integration inputs"
        ),
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


def plan(repo: Path, base: str, head: str, definition: str, count: int, out: Path,
         source_event: str = "pull_request") -> dict:
    count = workers(str(count))
    base, head, definition = map(commit_ref, (base, head, definition))
    definition_repo = Path(__file__).resolve().parents[1]
    if git(definition_repo, "rev-parse", "HEAD") != definition:
        raise ValueError("workflow driver checkout differs from workflow definition SHA")
    if git(repo, "rev-parse", "HEAD") != head:
        raise ValueError("tested checkout HEAD differs from requested codegen head")
    if source_event not in {"pull_request", "push_main"}:
        raise ValueError("unknown source event")
    initial_push = source_event == "push_main" and base == "0" * 40
    if source_event == "push_main" and not initial_push:
        present = subprocess.run([
            "git", "-C", str(repo), "cat-file", "-e", f"{base}^{{commit}}",
        ], capture_output=True).returncode == 0
        if not present:
            # Fetch only the immutable event-before commit, never today's main.
            subprocess.run([
                "git", "-C", str(repo), "fetch", "--no-tags", "origin", base,
            ], check=True)
    for sha in ((head,) if initial_push else (base, head)):
        if git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}") != sha:
            raise ValueError("unresolvable codegen input")
    if initial_push:
        paths = subprocess.check_output([
            "git", "-C", str(repo), "ls-tree", "-rz", "--name-only", head,
        ]).split(b"\0")
        raw = b"".join(b"A\0" + path + b"\0" for path in paths if path)
        merge_base = None
    else:
        # PRs use their merge base; pushes compare the exact before/after trees,
        # including a non-fast-forward event's before commit.
        refs = [base, head] if source_event == "push_main" else [f"{base}...{head}"]
        raw = subprocess.check_output([
            "git", "-C", str(repo), "diff", "--no-ext-diff", "--no-textconv",
            "--find-renames", "--ignore-submodules=none", "--name-status", "-z",
            *refs, "--",
        ])
        merge_base = base if source_event == "push_main" else git(repo, "merge-base", base, head)
    # This baseline survives deletes/renames and changes to native calls; only
    # its metadata can supply non-public exclusions, not candidate code.
    baseline = {} if initial_push else test_inventory(repo, merge_base)
    inventory = merge_inventory(baseline, test_inventory(repo, head))
    result = select_tests(parse_diff(raw), inventory, source_event)
    result.update({
        "source_event": source_event,
        "diff_kind": "before_after" if source_event == "push_main" else "merge_base",
        "codegen_base_sha": base, "codegen_sha": head,
        "codegen_base_tree": None if initial_push else git(repo, "rev-parse", f"{base}^{{tree}}"),
        "codegen_tree": git(repo, "rev-parse", "HEAD^{tree}"),
        "diff_merge_base": merge_base,
        "workflow_definition_sha": definition, "workers": count,
        "workflow_definition_tree": git(definition_repo, "rev-parse", "HEAD^{tree}"),
    })
    result["plan_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    write_json(out / "selection.json", result)
    (out / "selected-tests.txt").write_text(
        "".join(node + "\n" for node in result["selected"]), encoding="utf-8",
    )
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            for key, value in {
                "native": str(result["native"]).lower(),
                "base": base, "head": head, "definition": definition, "workers": count,
                "tree": result["codegen_tree"], "plan": result["plan_sha256"],
                "source_event": source_event,
            }.items():
                stream.write(f"{key}={value}\n")
    summary(
        f"{'RUN' if result['native'] else 'SKIP native'}: {result['reason']}\n\n"
        f"Workflow definition `{definition}`; tested codegen `{head}` "
        f"(tree `{result['codegen_tree']}`); base `{base}`; "
        f"diff baseline `{result['diff_merge_base']}`; source event `{source_event}`.\n\n"
        f"Triggers: `{json.dumps(result['triggers'])}`. "
        f"Selected function selectors: {len(result['selected'])}; "
        f"public corpus required: {result['needs_corpus']}. "
        f"Excluded input-dependent cases: `{json.dumps(result['excluded'])}`."
    )
    return result


def logged(command: list[str], log: Path, *, cwd: Path | None = None) -> None:
    """Stream the real command and retain its exit; never use a shell pipeline."""
    with log.open("a", encoding="utf-8") as stream:
        if stream.tell() == 0:
            identity = log.parent / "native-identity.json"
            if identity.is_file():
                stream.write("Native identity:\n" + identity.read_text(encoding="utf-8") + "\n")
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
        if exit_code == 0 and any(row["outcome"] == "failed" for row in self.reports):
            errors.append("pytest returned zero despite a failed phase")
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
        "plan_sha256": selection["plan_sha256"],
        "codegen_base_sha": selection["codegen_base_sha"],
        "source_event": selection["source_event"],
        "codegen_sha": selection["codegen_sha"], "codegen_tree": selection["codegen_tree"],
        "engine_ref": "refs/heads/main", "engine_sha": engine_sha,
        "engine_tree": git(engine, "rev-parse", "HEAD^{tree}"),
    }
    write_json(out / "native-identity.json", identity)
    summary("Native checkout identity:\n\n```json\n" + json.dumps(identity, indent=2) + "\n```")
    logged([
        sys.executable, "-m", "pip", "install", "pytest>=7.0", "pytest-xdist>=3.5",
    ], out / "install.log")
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
            print("Native identity: " + json.dumps(identity, sort_keys=True), flush=True)
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
    status = code if code else (1 if result["audit_errors"] else 0)
    if output := os.environ.get("GITHUB_OUTPUT"):
        values = {
            "completed_plan": selection["plan_sha256"],
            "pytest_exit": code, "audit_errors": len(result["audit_errors"]),
        }
        for legacy in ("False", "True"):
            rows = [row for row in audit.reports
                    if row["nodeid"] == f"{CONSTRUCTION}[{legacy}]"
                    and row["when"] == "call" and not row["xfail"]]
            values[f"construction_{legacy.lower()}"] = (
                rows[0]["outcome"] if len(rows) == 1 else "missing"
            )
        if status == 0:
            values["executed_plan"] = selection["plan_sha256"]
        with open(output, "a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    return status


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
    parser.add_argument("--expected-plan")
    parser.add_argument("--source-event", choices=("pull_request", "push_main"), default="pull_request")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        repo = args.repo.resolve()
        selection = plan(repo, args.base, args.head, args.definition, workers(args.workers), out,
                         args.source_event)
        if args.mode == "plan":
            return 0
        if args.engine is None:
            raise ValueError("--engine is required for run")
        if not re.fullmatch(r"[0-9a-f]{64}", args.expected_plan or ""):
            raise ValueError("run requires the trusted changes job's plan digest")
        if args.expected_plan != selection["plan_sha256"]:
            raise ValueError("native selection differs from the changes job")
        return run_native(repo, args.engine.resolve(), out, selection)
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as error:
        code = error.returncode if isinstance(error, subprocess.CalledProcessError) else 1
        write_json(out / "failure.json", {"error": str(error), "exit": code})
        summary(f"Native integration refused/failed: {error}; exit={code}")
        return code


if __name__ == "__main__":
    raise SystemExit(main())
