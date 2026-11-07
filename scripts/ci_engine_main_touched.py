"""Full-main / touched-PR integration with engine main (not a release pair).

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


def historical_refs(tree: ast.AST) -> list[str]:
    """Resolve literal/constant reference arguments without importing tests."""
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(node.value)

    def literals(node, seen):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value} if re.fullmatch(r"[0-9a-f]{7,40}", node.value) else set()
        if isinstance(node, ast.Name) and node.id not in seen:
            return set().union(*(literals(value, seen | {node.id})
                                 for value in bindings.get(node.id, [])))
        return set()

    refs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and (
            isinstance(node.func, ast.Name) and node.func.id == "reference_codegen"
            or isinstance(node.func, ast.Attribute) and node.func.attr == "reference_codegen"
        ):
            refs.update(literals(node.args[0], set()))
    return sorted(refs)


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
            # Resolve real historical objects before pytest imports. Full
            # history normally supplies abbreviated refs; a missing full ref
            # can be fetched by its immutable ID, never replaced with HEAD.
            "history_refs": historical_refs(tree),
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
        result[path] = {
            "imports": old["imports"] | new["imports"], "cases": cases,
            "history_refs": sorted(set(old.get("history_refs", []))
                                   | set(new.get("history_refs", []))),
        }
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
    if source_event == "push_main":
        # The changed paths are provenance only. Never use PR impact selection
        # or its private-input exclusions to narrow the main suite.
        required = sorted(
            f"{path}::{name}" for path, entry in inventory.items()
            for name in entry["cases"]
        )
        if not required:
            raise ValueError("full main suite has no test inventory")
        return {
            "native": True, "mode": "full",
            "reason": "Every main push requires the full repository suite",
            "changes": changes, "triggers": triggers,
            "selected": ["tests"], "required_tests": required,
            "suite_files": sorted(inventory),
            "selection_reasons": {"tests": ["full repository suite on exact main SHA"]},
            "excluded": [], "needs_corpus": True,
            "history_refs": sorted({
                ref for entry in inventory.values() for ref in entry.get("history_refs", [])
            }),
            "input_needs": [
                {"nodeid": f"{path}::{name}", "inputs": case["private"]}
                for path, entry in sorted(inventory.items())
                for name, case in sorted(entry["cases"].items()) if case["private"]
            ],
        }
    if not triggers and source_event == "pull_request":
        return {
            "native": False, "mode": "skip",
            "reason": "No emitter, generated C++, native harness/test or CI driver changes",
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
        "native": True, "mode": "touched",
        "reason": "Actual diff touches native integration inputs",
        "changes": changes, "triggers": triggers,
        "selected": sorted(selected), "selection_reasons": selected,
        "excluded": excluded, "needs_corpus": needs_corpus,
        "history_refs": sorted({
            ref for node in selected
            for ref in inventory.get(node.split("::", 1)[0], {}).get("history_refs", [])
        }),
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
    baseline = {} if source_event == "push_main" else test_inventory(repo, merge_base)
    head_inventory = test_inventory(repo, head)
    inventory = (head_inventory if source_event == "push_main"
                 else merge_inventory(baseline, head_inventory))
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
                "mode": result["mode"],
            }.items():
                stream.write(f"{key}={value}\n")
    summary(
        f"{'RUN' if result['native'] else 'SKIP native'}: {result['reason']}\n\n"
        f"Workflow definition `{definition}`; tested codegen `{head}` "
        f"(tree `{result['codegen_tree']}`); base `{base}`; "
        f"diff baseline `{result['diff_merge_base']}`; source event `{source_event}`.\n\n"
        f"Triggers: `{json.dumps(result['triggers'])}`. "
        f"Mode: `{result['mode']}`; pytest targets: `{json.dumps(result['selected'])}`; "
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


def require_public_feed(feed: Path) -> None:
    if not feed.is_file() or not feed.stat().st_size:
        raise ValueError(f"NEEDS real public replay feed: {feed}")
    with feed.open("rb") as stream:
        if stream.read(128).startswith(b"version https://git-lfs.github.com/spec/"):
            raise ValueError(f"NEEDS smudged public replay feed, got LFS pointer: {feed}")


def provision_history(repo: Path, refs: list[str], out: Path) -> dict:
    if git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("NEEDS full codegen history before collection")
    resolved = {}
    for ref in refs:
        if not re.fullmatch(r"[0-9a-f]{7,40}", ref):
            raise ValueError("invalid historical reference")
        found = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            capture_output=True, text=True,
        )
        if found.returncode:
            if len(ref) != 40:
                raise ValueError(f"NEEDS genuine historical object for abbreviated ref: {ref}")
            logged(["git", "-C", str(repo), "fetch", "--no-tags", "origin", ref],
                   out / "history.log")
        sha = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
        resolved[ref] = {"sha": commit_ref(sha), "tree": git(repo, "rev-parse", f"{sha}^{{tree}}")}
    return resolved


def collected_selectors(nodeids: set[str]) -> set[str]:
    """Index once rather than scan the full suite for each static target."""
    covered = set(nodeids)
    for node in nodeids:
        parts = node.split("[", 1)[0].split("::")
        covered.update("::".join(parts[:index]) for index in range(1, len(parts) + 1))
        path = PurePosixPath(parts[0])
        covered.update(parent.as_posix() for parent in path.parents if parent.as_posix() != ".")
    return covered


def collection_row(report) -> dict:
    return {
        "nodeid": report.nodeid, "outcome": report.outcome,
        "detail": str(report.longrepr) if report.failed or report.skipped else "",
    }


class WorkerCollectionAudit:
    """Capture before/after selection inside each real xdist worker.

    xdist only forwards unsuccessful collection reports to its controller.
    Worker output supplies the successful reports, pre-deselection item list,
    and explicit deselections as well, without a second pytest invocation.
    """

    def __init__(self, config) -> None:
        self.config = config
        self.items: list[str] = []
        self.final: list[str] = []
        self.deselected: list[str] = []
        self.collection: list[dict] = []

    def pytest_itemcollected(self, item) -> None:
        self.items.append(item.nodeid)

    def pytest_deselected(self, items) -> None:
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collectreport(self, report) -> None:
        self.collection.append(collection_row(report))

    def pytest_collection_finish(self, session) -> None:
        self.final = [item.nodeid for item in session.items]

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        self.config.workeroutput["engine_main_collection"] = {
            "plan": os.environ["PF_ENGINE_MAIN_PLAN"],
            "items": self.items, "final": self.final,
            "deselected": self.deselected, "collection": self.collection,
            "exit": int(exitstatus),
        }


def pytest_configure(config) -> None:
    # Loaded by -p in both controller and workers; the controller's instance is
    # supplied explicitly to pytest.main. Keep plan selection stdlib-only.
    if hasattr(config, "workerinput"):
        config.pluginmanager.register(WorkerCollectionAudit(config), "engine-main-worker-audit")


class ExecutionAudit:
    """Actual collection and phase reports, never an expected count."""

    def __init__(self) -> None:
        self.collected: set[str] = set()
        self.reports: list[dict] = []
        self.collections: dict[str, list[str]] = {}
        self.worker_inventory: dict[str, dict] = {}
        self.worker_errors: list[str] = []
        self.collection_reports: list[dict] = []

    def pytest_xdist_node_collection_finished(self, node, ids) -> None:
        self.collected.update(ids)
        self.collections[node.gateway.id if node is not None else "fixture"] = list(ids)

    def pytest_collectreport(self, report) -> None:
        self.collection_reports.append(collection_row(report))

    def pytest_testnodedown(self, node, error) -> None:
        worker = node.gateway.id
        if error is not None:
            self.worker_errors.append(f"{worker}: {error}")
        payload = getattr(node, "workeroutput", {}).get("engine_main_collection")
        if not isinstance(payload, dict):
            self.worker_errors.append(f"{worker}: missing worker collection audit")
        else:
            self.worker_inventory[worker] = payload

    def pytest_runtest_logreport(self, report) -> None:
        self.reports.append({
            "nodeid": report.nodeid, "when": report.when,
            "outcome": report.outcome, "xfail": hasattr(report, "wasxfail"),
            "detail": str(report.longrepr) if report.failed or report.skipped else "",
        })

    def result(self, selected: list[str], exit_code: int, *, mode: str = "touched",
               required: list[str] | None = None, suite_files: list[str] | None = None,
               count: int | None = None, plan_digest: str | None = None) -> dict:
        errors = list(self.worker_errors)
        if mode not in {"full", "touched"}:
            errors.append("invalid execution mode")
        if not self.collected:
            errors.append("zero tests collected")
        missing_targets = []
        covered = collected_selectors(self.collected)
        for selector in (required if mode == "full" and required is not None else selected):
            if selector not in covered:
                errors.append(f"selected target did not collect: {selector}")
                missing_targets.append(selector)
        for legacy in ("False", "True"):
            if f"{CONSTRUCTION}[{legacy}]" not in self.collected:
                errors.append(f"checked-construction smoke parameter missing: {legacy}")
        if count is not None:
            if len(self.collections) != count or set(self.worker_inventory) != set(self.collections):
                errors.append("worker collection inventory is incomplete")
        collection = list(self.collection_reports)
        deselected = set()
        discovered = set()
        ordered = next(iter(self.collections.values()), [])
        for worker, ids in self.collections.items():
            if ids != ordered or len(ids) != len(set(ids)):
                errors.append(f"{worker}: inconsistent or duplicate collected nodeids")
        for worker, payload in self.worker_inventory.items():
            if payload.get("plan") != plan_digest:
                errors.append(f"{worker}: collection plan binding differs")
            items, final = payload.get("items", []), payload.get("final", [])
            if (final != self.collections.get(worker) or set(items) != set(final)
                    or len(items) != len(final)):
                errors.append(f"{worker}: items omitted or changed during collection")
            worker_exit = payload.get("exit")
            if (type(worker_exit) is not int or worker_exit not in {0, 1}
                    or (exit_code == 0 and worker_exit != 0)):
                errors.append(f"{worker}: unsuccessful or inconsistent worker exit: {worker_exit}")
            discovered.update(items)
            deselected.update(payload.get("deselected", []))
            collection.extend({**row, "worker": worker} for row in payload.get("collection", []))
        if deselected:
            errors.append("tests were deselected")
        collection_omissions = [row for row in collection if row["outcome"] != "passed"]
        if collection_omissions:
            errors.append("collection failed or skipped, including module-level omissions")
        if mode == "full":
            if not required or not suite_files:
                errors.append("full mode lacks bound repository inventory")
            if not self.worker_inventory or discovered != self.collected:
                errors.append("full mode lacks complete worker discovery")
            seen_modules = {row["nodeid"] for row in collection if row["outcome"] == "passed"}
            for path in suite_files or []:
                if path not in seen_modules:
                    errors.append(f"repository test module not collected successfully: {path}")
        executed = {
            row["nodeid"] for row in self.reports
            if row["when"] == "call" and row["outcome"] in {"passed", "failed"}
            and not row["xfail"]
        }
        for node in sorted(self.collected - executed):
            errors.append(f"selected case did not execute an ordinary test call: {node}")
        phases = {}
        for row in self.reports:
            phases.setdefault(row["nodeid"], {}).setdefault(row["when"], []).append(row)
            if row["nodeid"] not in self.collected:
                errors.append(f"phase report outside collection: {row['nodeid']}")
        for node in sorted(self.collected):
            for phase in ("setup", "call", "teardown"):
                rows = phases.get(node, {}).get(phase, [])
                if len(rows) != 1:
                    errors.append(f"missing or duplicate {phase} phase: {node}")
                elif phase != "call" and rows[0]["outcome"] != "passed":
                    errors.append(f"{phase} did not pass: {node}")
        skips = [row for row in self.reports if row["outcome"] == "skipped" or row["xfail"]]
        if skips:
            errors.append("tests skipped or xfailed; these rows remain unexecuted")
        if exit_code == 0 and any(row["outcome"] == "failed" for row in self.reports):
            errors.append("pytest returned zero despite a failed phase")
        return {
            "pytest_exit": exit_code, "audit_errors": errors,
            "collected_count": len(self.collected), "executed_count": len(executed),
            "call_outcomes": dict(Counter(
                row["outcome"] for row in self.reports if row["when"] == "call"
            )),
            "skips": skips, "collected_nodeids": sorted(self.collected),
            "reports": self.reports, "mode": mode,
            "collection_reports": collection, "worker_inventories": self.worker_inventory,
            "deselected_nodeids": sorted(deselected), "missing_targets": missing_targets,
            "unexecuted_nodeids": sorted(self.collected - executed),
            "collection_omissions": collection_omissions,
            # An ordinary assertion failure may have complete execution
            # coverage, but can never establish successful full execution.
            "coverage_complete": not errors,
            "full_success": mode == "full" and not errors and exit_code == 0
            and all(row["outcome"] == "passed" for row in self.reports),
        }


def run_native(repo: Path, engine: Path, out: Path, selection: dict) -> int:
    if not selection["native"] or not selection["selected"]:
        raise ValueError("native job requires a nonempty native selection")
    mode = selection["mode"]
    if ((selection["source_event"] == "push_main" and mode != "full")
            or (selection["source_event"] == "pull_request" and mode != "touched")
            or (mode == "full" and (selection["selected"] != ["tests"]
                                    or selection["excluded"]))):
        raise ValueError("source event and execution mode/whole-suite scope differ")
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
        "mode": mode,
        "codegen_sha": selection["codegen_sha"], "codegen_tree": selection["codegen_tree"],
        "engine_ref": "refs/heads/main", "engine_sha": engine_sha,
        "engine_tree": git(engine, "rev-parse", "HEAD^{tree}"),
    }
    write_json(out / "native-identity.json", identity)
    summary("Native checkout identity:\n\n```json\n" + json.dumps(identity, indent=2) + "\n```")
    logged([
        sys.executable, "-m", "pip", "install", "pytest>=7.0", "pytest-xdist>=3.5",
    ], out / "install.log")
    if mode == "full" and shutil.which("clang++") is None:
        # Standard hosted images normally include clang. Install the real
        # public tool when absent; no stub or alternative AST output is used.
        logged(["sudo", "apt-get", "update"], out / "install.log")
        logged(["sudo", "apt-get", "install", "-y", "clang"], out / "install.log")
        if shutil.which("clang++") is None:
            raise ValueError("NEEDS real clang++ for the full host-member inventory")
    identity["historical_codegen"] = provision_history(
        repo, selection.get("history_refs", []), out,
    )
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
        require_public_feed(feed)
        if not (engine / "scripts/run_strategy.py").is_file():
            raise ValueError("public engine replay driver missing")
        env["PINEFORGE_ENGINE_CORPUS"] = str(corpus)
        identity["public_corpus"] = {
            "sha": git(corpus, "rev-parse", "HEAD"),
            "tree": git(corpus, "rev-parse", "HEAD^{tree}"),
            "feed": str(feed), "feed_bytes": feed.stat().st_size,
        }
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
    os.environ.pop("PYTEST_PLUGINS", None)
    os.environ["PF_ENGINE_MAIN_PLAN"] = selection["plan_sha256"]
    driver_dir = str(Path(__file__).resolve().parent)
    sys.path.insert(0, driver_dir)
    os.environ["PYTHONPATH"] = os.pathsep.join((driver_dir, str(repo)))
    args = [
        "-o", "addopts=", "-v", "-ra", "-n", count,
        "-p", "ci_engine_main_touched",
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
    result = audit.result(
        selection["selected"], code, mode=mode, required=selection.get("required_tests"),
        suite_files=selection.get("suite_files"), count=int(count),
        plan_digest=selection["plan_sha256"],
    )
    result["identity"] = identity
    result["input_needs"] = selection.get("input_needs", [])
    result["inventory_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    write_json(out / "execution.json", result)
    summary(
        f"mode={mode}; full success={result['full_success']}; "
        f"pytest exit={code}; collected={result['collected_count']}; "
        f"executed={result['executed_count']}; call outcomes={result['call_outcomes']}; "
        f"skips={len(result['skips'])}; audit errors={json.dumps(result['audit_errors'])}"
    )
    status = code if code else (
        1 if result["audit_errors"] or (mode == "full" and not result["full_success"]) else 0
    )
    if output := os.environ.get("GITHUB_OUTPUT"):
        values = {
            "completed_plan": selection["plan_sha256"],
            "pytest_exit": code, "audit_errors": len(result["audit_errors"]),
            "execution_mode": mode,
            "inventory": result["inventory_sha256"],
            "coverage_complete": str(result["coverage_complete"]).lower(),
            "full_success": str(result["full_success"]).lower(),
            "engine_sha": engine_sha, "engine_tree": identity["engine_tree"],
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
        if result["full_success"]:
            values["full_executed_plan"] = selection["plan_sha256"]
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
