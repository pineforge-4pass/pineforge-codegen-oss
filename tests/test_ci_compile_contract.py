"""Keep the shipped PR workflow dependent on real, paired compile checks."""

from pathlib import Path
import re
import textwrap
import tomllib

import pytest


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/test.yml"
COMPILE_DOC = ROOT / "docs/ci-compile.md"
PYTEST_FORBIDDEN = (
    "PINEFORGE_", "pineforge-engine", "apt-get", "upload-artifact", "compile-shard",
)
SWEEP_COMMAND = (
    "python -m pytest -q tests/test_translate_implies_compile.py "
    "tests/test_require_compile.py tests/test_ci_compile_contract.py"
)
SUITE_COMMAND = 'python -m pytest -q "${suite_paths[@]}"'
SUITE_SCRIPT = (
    "set -euo pipefail\n"
    'suite_files="$RUNNER_TEMP/compile-suite-${SUITE_SLICE}.txt"\n'
    'python tests/_compile_partition.py "$SUITE_SLICE" > "$suite_files"\n'
    'mapfile -t suite_paths < "$suite_files"\n'
    'if [[ "${#suite_paths[@]}" -eq 0 ]]; then\n'
    '  echo "::error::compile suite slice $SUITE_SLICE is empty"\n'
    "  exit 1\n"
    "fi\n"
    f"{SUITE_COMMAND}\n"
)
MATRIX_MESSAGE = (
    "compile must have sweep-0 through sweep-3 and suite-0 through suite-3 exactly once"
)
PIN_STEP = (
    "      - name: Read the engine pin\n"
    "        id: pin\n"
    "        shell: bash\n"
    "        run: |\n"
    "          set -euo pipefail\n"
    "          pin=$(cat ci/engine-pin)\n"
    "          if [[ \"$(wc -l < ci/engine-pin)\" -ne 1 ]]; then\n"
    "            echo \"::error::ci/engine-pin must contain exactly one line of 40 lowercase hex characters\"\n"
    "            exit 1\n"
    "          fi\n"
    "          if [[ ! \"$pin\" =~ ^[0-9a-f]{40}$ ]]; then\n"
    "            echo \"::error::ci/engine-pin must contain exactly one line of 40 lowercase hex characters\"\n"
    "            exit 1\n"
    "          fi\n"
    "          printf 'sha=%s\\n' \"$pin\" >> \"$GITHUB_OUTPUT\"\n"
)
PIN_EQUALITY = '          if [[ "$PINEFORGE_ENGINE_SHA" != "${{ steps.pin.outputs.sha }}" ]]; then\n'


def _job(workflow: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:|\Z)",
                      workflow, re.M | re.S)
    assert match is not None, f"missing {name} job"
    return match[1]


def _step(job: str, name: str) -> str:
    match = re.search(
        rf"^      - name: {re.escape(name)}\n(.*?)(?=^      - |\Z)",
        job, re.M | re.S,
    )
    assert match is not None, f"missing {name} step"
    return match[1]


def _assert_pytest_contract(workflow: str) -> None:
    job = _job(workflow, "pytest")
    for forbidden in PYTEST_FORBIDDEN:
        assert forbidden not in job, f"pytest must not contain {forbidden}"
    assert "    runs-on: ubuntu-24.04\n" in job
    assert '        python-version: ["3.11", "3.12", "3.13", "3.14"]\n' in job
    assert "        run: python -m pytest -q\n" in job


def _assert_aggregate_contract(workflow: str) -> None:
    job = _job(workflow, "test-check")
    needs = re.search(r"^    needs: \[([^\]]+)\]$", job, re.M)
    assert needs is not None
    assert needs[1].split(", ") == [
        "authz", "changes", "workflow-lint", "package-files", "pytest", "compile",
    ], "test-check must need pytest and compile alongside the existing checks"
    assert '          HEAVY_JOBS: "[\\"pytest\\",\\"compile\\"]"\n' in job, (
        "test-check must classify pytest and compile as heavy jobs"
    )


def _assert_no_failure_bypasses(workflow: str) -> None:
    message = "workflow must not set time limits, failure bypasses or cancellation"
    assert re.search(
        r"^\s*(?:timeout-minutes|continue-on-error):", workflow, re.M,
    ) is None, message
    # The workflow-level concurrency group of main keeps its cancel-in-progress
    # line; this change adds no cancellation to the new compile job.
    assert re.search(
        r"^\s*cancel-in-progress:", _job(workflow, "compile"), re.M,
    ) is None, message


def _assert_compile_contract(workflow: str) -> None:
    job = _job(workflow, "compile")
    assert "    name: compile\n" in job
    assert "    needs: [authz, changes]\n" in job
    assert "    if: needs.changes.outputs.code == 'true'\n" in job, (
        "compile must use the code-change guard"
    )
    assert "    runs-on: ubuntu-24.04\n" in job
    assert "    permissions:\n      contents: read\n" in job
    assert "      fail-fast: false\n" in job, "compile must run every matrix part"
    assert '      PINEFORGE_REQUIRE_COMPILE: "1"\n' in job, (
        "compile must require compile inputs"
    )
    assert re.findall(
        r'^      PINEFORGE_ENGINE_PAIRING: ([^\n]+)$', job, re.M
    ) == ['"pinned"'], "compile must use the pinned engine pairing mode"
    for variable, suffix in (
        ("PINEFORGE_ENGINE_INCLUDE", "include"),
        ("PINEFORGE_ENGINE_CORPUS", "corpus"),
    ):
        assert f"      {variable}: ${{{{ github.workspace }}}}/.ci/pineforge-engine/{suffix}\n" in job
    assert "      PINEFORGE_EIGEN_INCLUDE: /usr/include/eigen3\n" in job
    assert "      CXX: /usr/bin/g++\n" in job
    steps = job.split("    steps:\n", 1)[1]
    pin_offset = steps.find(PIN_STEP)
    checkout_step = "      - name: Check out paired engine pin and its pinned corpus\n"
    assert pin_offset >= 0 and steps[pin_offset + len(PIN_STEP):].startswith(checkout_step), (
        "compile must read the engine pin first"
    )
    pin_step = _step(job, "Read the engine pin")
    assert "        id: pin\n" in pin_step
    assert "          pin=$(cat ci/engine-pin)\n" in pin_step
    assert '          if [[ "$(wc -l < ci/engine-pin)" -ne 1 ]]; then\n' in pin_step
    assert "          if [[ ! \"$pin\" =~ ^[0-9a-f]{40}$ ]]; then\n" in pin_step
    assert "          printf 'sha=%s\\n' \"$pin\" >> \"$GITHUB_OUTPUT\"\n" in pin_step
    checkout = _step(job, "Check out paired engine pin and its pinned corpus")
    assert "          repository: pineforge-4pass/pineforge-engine\n" in checkout, (
        "compile must check out the paired engine"
    )
    assert "          ref: ${{ steps.pin.outputs.sha }}\n" in checkout, (
        "compile must check out the committed engine pin"
    )
    assert "          ref: main\n" not in checkout
    assert "          path: .ci/pineforge-engine\n" in checkout
    assert "          submodules: true\n" in checkout, "compile must check out the corpus submodule"
    assert job.count("          persist-credentials: false\n") == 2
    install = _step(job, "Install compile-only prerequisites")
    assert "sudo apt-get install --yes --no-install-recommends g++ libeigen3-dev" in install, (
        "compile must install g++ and Eigen"
    )
    for check in (
        'test -f "$PINEFORGE_ENGINE_INCLUDE/pineforge/engine.hpp"',
        'test -f "$PINEFORGE_EIGEN_INCLUDE/Eigen/Dense"',
        'test -x "$CXX"',
    ):
        assert check in install
    assert PIN_EQUALITY in install, (
        "compile must verify the checked-out engine SHA against the pin"
    )
    matrix = re.search(r"^      matrix:\n(.*?)(?=^    env:)", job, re.M | re.S)
    assert matrix is not None, "compile must declare its eight-part matrix"
    assert matrix[1] == (
        "        include:\n"
        "          - part: sweep-0\n"
        "            compile-shard: 0\n"
        "          - part: sweep-1\n"
        "            compile-shard: 1\n"
        "          - part: sweep-2\n"
        "            compile-shard: 2\n"
        "          - part: sweep-3\n"
        "            compile-shard: 3\n"
        "          - part: suite-0\n"
        "            compile-shard: 0\n"
        "            suite-shard: 0\n"
        "          - part: suite-1\n"
        "            compile-shard: 0\n"
        "            suite-shard: 1\n"
        "          - part: suite-2\n"
        "            compile-shard: 0\n"
        "            suite-shard: 2\n"
        "          - part: suite-3\n"
        "            compile-shard: 0\n"
        "            suite-shard: 3\n"
    ), MATRIX_MESSAGE
    assert '      PINEFORGE_COMPILE_SHARDS: "4"\n' in job
    assert "      PINEFORGE_COMPILE_SHARD: ${{ matrix.compile-shard }}\n" in job
    assert "      PINEFORGE_COMPILE_REPORT_DIR: ${{ github.workspace }}/.ci/compile-report\n" in job
    assert "continue-on-error:" not in job, (
        "workflow must not set time limits, failure bypasses or cancellation"
    )
    assert "runner." not in job.split("    steps:")[0], (
        "compile job env must use actionlint-supported contexts"
    )
    # Job env expressions may use only contexts accepted by actionlint here.
    env = re.search(r"^    env:\n(.*?)(?=^    steps:)", job, re.M | re.S)
    assert env is not None
    for expression in re.findall(r"\$\{\{(.*?)\}\}", env[1]):
        contexts = re.findall(r"(?<![\w.])([A-Za-z_]\w*)\.", expression)
        assert set(contexts) <= {
            "github", "inputs", "matrix", "needs", "secrets", "strategy", "vars",
        }, "compile job env must use actionlint-supported contexts"
    assert job.count(
        "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
    ) == 1
    assert re.findall(r"^          python-version: (.*)$", job, re.M) == ['"3.12"'], (
        "compile must use Python 3.12 only"
    )
    assert '        run: python -m pip install -e ".[dev]"\n' in job
    for name, prefix in (
        ("Run fixture and corpus compile sweep", "sweep"),
        ("Run remaining suite with compile checks", "suite"),
    ):
        step = _step(job, name)
        assert f"        if: startsWith(matrix.part, '{prefix}-')\n" in step, (
            f"{name} must select its own matrix parts"
        )
        if prefix == "sweep":
            assert f"        run: {SWEEP_COMMAND}\n" in step, (
                f"{name} must run its required tests"
            )
        else:
            assert "        shell: bash\n" in step
            assert "        env:\n          SUITE_SLICE: ${{ matrix.suite-shard }}\n" in step, (
                "suite parts must pass their own index to the shared helper"
            )
            assert "        run: |\n" + textwrap.indent(SUITE_SCRIPT, " " * 10) in step, (
                f"{name} must run its required tests"
            )
    assert re.findall(r"^ +(?:run: )?(python -m pytest .*)$", job, re.M) == [
        SWEEP_COMMAND, SUITE_COMMAND,
    ], "compile must run only the sweep or the remaining suite per part"
    for name, prefix, missing in (
        ("Retain sweep compile failures and measured time", "sweep", "error"),
        ("Retain any suite compile report", "suite", "ignore"),
    ):
        step = _step(job, name)
        assert f"        if: ${{{{ always() && startsWith(matrix.part, '{prefix}-') }}}}\n" in step, (
            f"{name} must always retain its own part's reports"
        )
        assert (
            "        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
            in step
        )
        assert "          name: compile-report-${{ matrix.part }}\n" in step
        assert "          path: ${{ github.workspace }}/.ci/compile-report\n" in step
        assert f"          if-no-files-found: {missing}\n" in step, (
            f"{name} must use if-no-files-found: {missing}"
        )
    _assert_pytest_contract(workflow)
    _assert_aggregate_contract(workflow)
    _assert_no_failure_bypasses(workflow)


def _partition_module():
    # The base's single suite part fails this behavioral contract before the
    # new helper is imported, rather than failing collection on a missing module.
    _assert_compile_contract(WORKFLOW.read_text(encoding="utf-8"))
    from tests import _compile_partition

    return _compile_partition


def _assert_suite_partition(partition) -> None:
    config_path = ROOT / "pyproject.toml"
    print("Suite partition inputs:", config_path, ROOT / "tests", sep="\n", flush=True)
    options = tomllib.loads(config_path.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert options["testpaths"] == ["tests"]
    assert options["python_files"] == ["test_*.py"]
    on_disk = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("test_*.py")
        if path.is_file()
    }
    assert partition.collected_test_files() == sorted(on_disk), (
        "helper must discover every default-collected test file exactly once"
    )
    excluded = set(SWEEP_COMMAND.split()[4:])
    assert len(excluded) == 3 and excluded <= on_disk
    slices = [partition.suite_slice(index) for index in range(4)]
    assert all(slices), "every suite slice must be non-empty"
    flattened = [path for files in slices for path in files]
    assert len(flattened) == len(set(flattened)), "suite slices must not overlap"
    assert set(flattened).isdisjoint(excluded), "suite slices must exclude the sweep files"
    assert set(flattened) | excluded == on_disk, "suite partition must cover every test file"
    positions = {path: index for index, path in enumerate(sorted(on_disk - excluded))}
    for index, files in enumerate(slices):
        assert files == sorted(files), "suite paths must be sorted"
        assert all(positions[path] % 4 == index for path in files), (
            "suite files must belong to their index modulo four"
        )
    print("Suite slice file counts:", [len(files) for files in slices], flush=True)


def test_compile_suite_partition_covers_every_default_collected_file_once():
    _assert_suite_partition(_partition_module())


@pytest.mark.parametrize("mutation,message", [
    ("modulus", "suite partition must cover every test file"),
    ("exclusions", "suite slices must exclude the sweep files"),
    ("empty", "suite slice 0 is empty"),
])
def test_suite_partition_controls_reject_reverted_rules(monkeypatch, mutation, message):
    partition = _partition_module()
    _assert_suite_partition(partition)
    if mutation == "modulus":
        monkeypatch.setattr(partition, "SUITE_PARTS", 5)
    elif mutation == "exclusions":
        monkeypatch.setattr(partition, "SWEEP_FILES", frozenset())
    else:
        monkeypatch.setattr(partition, "SWEEP_FILES", frozenset(partition.collected_test_files()))
    error = ValueError if mutation == "empty" else AssertionError
    with pytest.raises(error, match=message):
        _assert_suite_partition(partition)


@pytest.mark.parametrize("index", range(4))
def test_suite_partition_cli_prints_the_shared_slice(index, capsys):
    partition = _partition_module()
    assert partition.main([str(index)]) == 0
    output = capsys.readouterr()
    assert output.out.splitlines() == partition.suite_slice(index)
    assert output.err == ""


@pytest.mark.parametrize("index", [-1, 4])
def test_suite_partition_cli_refuses_invalid_indices(index, capsys):
    partition = _partition_module()
    with pytest.raises(SystemExit) as failure:
        partition.main([str(index)])
    output = capsys.readouterr()
    assert failure.value.code == 2
    assert output.out == ""
    assert "suite index must be between 0 and 3" in output.err


def test_suite_partition_cli_refuses_an_empty_slice(monkeypatch, capsys):
    partition = _partition_module()
    monkeypatch.setattr(partition, "SWEEP_FILES", frozenset(partition.collected_test_files()))
    with pytest.raises(SystemExit) as failure:
        partition.main(["0"])
    output = capsys.readouterr()
    assert failure.value.code == 2
    assert output.out == ""
    assert "suite slice 0 is empty" in output.err


def test_suite_partition_reads_the_configured_paths_and_filename_pattern(tmp_path):
    partition = _partition_module()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["cases"]\npython_files = ["probe_*.py"]\n',
        encoding="utf-8",
    )
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "nested").mkdir()
    for name in ("probe_z.py", "nested/probe_a.py", "test_ignored.py", "ignored_test.py"):
        (cases / name).write_text("", encoding="utf-8")
    (tmp_path / "probe_outside.py").write_text("", encoding="utf-8")
    assert partition.collected_test_files(tmp_path) == [
        "cases/nested/probe_a.py", "cases/probe_z.py",
    ]


def _assert_pairing_exports(workflow: str) -> None:
    job = _job(workflow, "compile")
    install = re.search(
        r"^      - name: Install compile-only prerequisites\n(.*?)(?=^      - |\Z)",
        job, re.M | re.S,
    )
    assert install is not None, "missing compile prerequisite step"
    for variable, checkout in (
        ("PINEFORGE_ENGINE_SHA", ".ci/pineforge-engine"),
        ("PINEFORGE_CORPUS_SHA", ".ci/pineforge-engine/corpus"),
    ):
        assert f"          {variable}=$(git -C {checkout} rev-parse HEAD)\n" in install[1], (
            f"{variable} must come from the checked-out commit"
        )
        assert (
            f'          printf \'{variable}=%s\\n\' "${variable}" >> "$GITHUB_ENV"\n'
            in install[1]
        ), f"{variable} must be exported through GITHUB_ENV"


def test_pr_workflow_exports_the_swept_pairing():
    _assert_pairing_exports(WORKFLOW.read_text(encoding="utf-8"))


def test_engine_pin_is_exactly_one_lowercase_40_hex_line():
    pin = (ROOT / "ci/engine-pin").read_text(encoding="utf-8")
    assert re.fullmatch(r"[0-9a-f]{40}\n", pin), (
        "ci/engine-pin must contain exactly one 40-hex lowercase line"
    )


@pytest.mark.parametrize("variable,checkout", [
    ("PINEFORGE_ENGINE_SHA", ".ci/pineforge-engine"),
    ("PINEFORGE_CORPUS_SHA", ".ci/pineforge-engine/corpus"),
])
def test_pairing_workflow_controls_reject_an_unrecorded_or_literal_sha(variable, checkout):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _assert_pairing_exports(workflow)
    assignment = f"{variable}=$(git -C {checkout} rev-parse HEAD)"
    export = f'printf \'{variable}=%s\\n\' "${variable}" >> "$GITHUB_ENV"'
    assert workflow.count(assignment) == 1
    assert workflow.count(export) == 1
    with pytest.raises(AssertionError, match=f"{variable} must come from"):
        _assert_pairing_exports(workflow.replace(assignment, f"{variable}={'a' * 40}", 1))
    with pytest.raises(AssertionError, match=f"{variable} must be exported"):
        _assert_pairing_exports(workflow.replace(export, f'printf "%s\\n" "${variable}"', 1))


@pytest.mark.parametrize("claim", [
    "The four Python 3.11–3.14 legs stay unchanged from main.",
    "The `compile` job uses Python 3.12 and has eight parts: "
    "four sweep shards (`sweep-0`, `sweep-1`, `sweep-2`, `sweep-3`) and "
    "four suite slices (`suite-0`, `suite-1`, `suite-2`, `suite-3`).",
    "All eight parts set `PINEFORGE_REQUIRE_COMPILE=1`.",
    "The suite slices partition the remaining test files once across four parts.",
    "All eight compile parts must be green.",
    "The required aggregate job name remains `compile`.",
    "A re-run must re-run all of `compile`.",
    "Repository required checks should list `test-check`, which aggregates `pytest` and `compile`.",
    "If settings list matrix legs individually, require all eight compile parts as well.",
    "A measured sweep shard took 239 s of wall time and compiled 196 scripts.",
    "Suite slice times are measured by the first run.",
    "`tests/_compile_partition.py` reads `testpaths` and `python_files` from "
    "`[tool.pytest.ini_options]` in `pyproject.toml`.",
    "For slice K, it excludes the three sweep-run files, sorts the remaining "
    "repository-relative paths, and selects the files at indices K modulo 4.",
    "The excluded files are `tests/test_translate_implies_compile.py`, "
    "`tests/test_require_compile.py`, and `tests/test_ci_compile_contract.py`.",
    "The compile job reads `ci/engine-pin` and initializes the engine's pinned corpus "
    "submodule.",
    "Required compile runs use `pinned`; only scheduled engine drift uses `floating`.",
    "`ci/engine-pin` is one lowercase 40-hex line.",
    "The scheduled Engine drift workflow checks engine `main` separately and tells "
    "the owner when a pin bump is due.",
    "The sweeps cover every `.pine` under `tests/fixtures` and `tests/gate-corpus`, "
    "plus every recursive `strategy.pine` in the paired engine corpus.",
    "Tests that transpile inline strings elsewhere are not swept.",
])
def test_compile_docs_state_the_coverage_limits(claim):
    print(f"Compile coverage documentation: {COMPILE_DOC}", flush=True)
    document = " ".join(COMPILE_DOC.read_text(encoding="utf-8").split())
    assert claim in document, f"missing compile coverage requirement: {claim}"


@pytest.mark.parametrize("forbidden", PYTEST_FORBIDDEN)
def test_pytest_job_keeps_compile_work_out_of_the_python_matrix(forbidden):
    job = _job(WORKFLOW.read_text(encoding="utf-8"), "pytest")
    assert forbidden not in job, f"pytest must not contain {forbidden}"


def test_required_check_includes_compile_as_heavy_work():
    _assert_aggregate_contract(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_has_no_time_limits_failure_bypasses_or_cancellation():
    _assert_no_failure_bypasses(WORKFLOW.read_text(encoding="utf-8"))


def test_pr_workflow_requires_the_paired_compile_sweep():
    _assert_compile_contract(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.parametrize("mutation,message", [
    (
        ("          ref: ${{ steps.pin.outputs.sha }}\n", "          ref: main\n"),
        "compile must check out the committed engine pin",
    ),
    (
        (PIN_EQUALITY, '          if [[ "$PINEFORGE_ENGINE_SHA" == "${{ steps.pin.outputs.sha }}" ]]; then\n'),
        "compile must verify the checked-out engine SHA against the pin",
    ),
    (
        (PIN_STEP, ""),
        "compile must read the engine pin first",
    ),
])
def test_compile_pin_controls_reject_reverted_rules(mutation, message):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _assert_compile_contract(workflow)
    old, new = mutation
    assert workflow.count(old) == 1, "pin mutation must target the shipped workflow once"
    mutated = workflow.replace(old, new, 1)
    with pytest.raises(AssertionError, match=re.escape(message)):
        _assert_compile_contract(mutated)


@pytest.mark.parametrize("job_name,old,new,message", [
    (
        "compile", 'PINEFORGE_REQUIRE_COMPILE: "1"', 'PINEFORGE_REQUIRE_COMPILE: "0"',
        "compile must require compile inputs",
    ),
    (
        "compile", '      PINEFORGE_ENGINE_PAIRING: "pinned"\n',
        '      PINEFORGE_ENGINE_PAIRING: "floating"\n',
        "compile must use the pinned engine pairing mode",
    ),
    (
        "compile", '      PINEFORGE_ENGINE_PAIRING: "pinned"\n', "",
        "compile must use the pinned engine pairing mode",
    ),
    (
        "compile", "repository: pineforge-4pass/pineforge-engine",
        "repository: example/other-engine", "compile must check out the paired engine",
    ),
    (
        "compile", "submodules: true", "submodules: false",
        "compile must check out the corpus submodule",
    ),
    ("compile", "g++ libeigen3-dev", "g++", "compile must install g++ and Eigen"),
    (
        "compile", f"run: {SWEEP_COMMAND}\n", "run: echo pytest\n",
        "Run fixture and corpus compile sweep must run its required tests",
    ),
    (
        "compile", SUITE_COMMAND, "python -m pytest -q",
        "Run remaining suite with compile checks must run its required tests",
    ),
    (
        "compile", "compile-shard: 3\n", "compile-shard: 2\n",
        MATRIX_MESSAGE,
    ),
    (
        "compile", "- part: suite-0\n", "- part: sweep-4\n", MATRIX_MESSAGE,
    ),
    (
        "compile",
        "          - part: suite-3\n            compile-shard: 0\n            suite-shard: 3\n",
        "", MATRIX_MESSAGE,
    ),
    (
        "compile", "suite-shard: 3\n", "suite-shard: 2\n", MATRIX_MESSAGE,
    ),
    (
        "compile",
        'python tests/_compile_partition.py "$SUITE_SLICE" > "$suite_files"',
        'python tests/_compile_partition.py 0 > "$suite_files"',
        "Run remaining suite with compile checks must run its required tests",
    ),
    (
        "compile",
        "          SUITE_SLICE: ${{ matrix.suite-shard }}\n",
        "          SUITE_SLICE: 0\n",
        "suite parts must pass their own index to the shared helper",
    ),
    (
        "compile", 'if [[ "${#suite_paths[@]}" -eq 0 ]]; then', "if false; then",
        "Run remaining suite with compile checks must run its required tests",
    ),
    (
        "compile", "if-no-files-found: error\n", "if-no-files-found: ignore\n",
        "Retain sweep compile failures and measured time must use if-no-files-found: error",
    ),
    (
        "compile", "if-no-files-found: ignore\n", "if-no-files-found: error\n",
        "Retain any suite compile report must use if-no-files-found: ignore",
    ),
    (
        "compile", "if: needs.changes.outputs.code == 'true'",
        "if: needs.changes.outputs.code == 'false'", "compile must use the code-change guard",
    ),
    (
        "compile", "fail-fast: false", "fail-fast: true",
        "compile must run every matrix part",
    ),
    (
        "compile", 'python-version: "3.12"', 'python-version: "3.11"',
        "compile must use Python 3.12 only",
    ),
    (
        "compile", "if: startsWith(matrix.part, 'sweep-')",
        "if: startsWith(matrix.part, 'suite-')",
        "Run fixture and corpus compile sweep must select its own matrix parts",
    ),
    (
        "compile", "if: startsWith(matrix.part, 'suite-')",
        "if: startsWith(matrix.part, 'sweep-')",
        "Run remaining suite with compile checks must select its own matrix parts",
    ),
    (
        "compile", "if: ${{ always() && startsWith(matrix.part, 'sweep-') }}",
        "if: ${{ success() && startsWith(matrix.part, 'sweep-') }}",
        "Retain sweep compile failures and measured time must always retain its own part's reports",
    ),
    (
        "compile", "if: ${{ always() && startsWith(matrix.part, 'suite-') }}",
        "if: ${{ success() && startsWith(matrix.part, 'suite-') }}",
        "Retain any suite compile report must always retain its own part's reports",
    ),
    (
        "pytest", '      PYTHONHASHSEED: "0"\n',
        '      PYTHONHASHSEED: "0"\n      PINEFORGE_REQUIRE_COMPILE: "1"\n',
        "pytest must not contain PINEFORGE_",
    ),
    (
        "test-check", "needs: [authz, changes, workflow-lint, package-files, pytest, compile]",
        "needs: [authz, changes, workflow-lint, package-files, pytest]",
        "test-check must need pytest and compile alongside the existing checks",
    ),
    (
        "test-check", "needs: [authz, changes, workflow-lint, package-files, pytest, compile]",
        "needs: [authz, changes, workflow-lint, package-files, compile]",
        "test-check must need pytest and compile alongside the existing checks",
    ),
    (
        "test-check", 'HEAVY_JOBS: "[\\"pytest\\",\\"compile\\"]"',
        'HEAVY_JOBS: "[\\"pytest\\"]"',
        "test-check must classify pytest and compile as heavy jobs",
    ),
])
def test_compile_workflow_controls_reject_a_reverted_rule(job_name, old, new, message):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _assert_compile_contract(workflow)
    job = _job(workflow, job_name)
    assert job.count(old) == 1, "mutation must target the shipped job once"
    header = f"  {job_name}:\n"
    assert workflow.count(header + job) == 1
    mutated = workflow.replace(header + job, header + job.replace(old, new, 1), 1)
    with pytest.raises(AssertionError, match=re.escape(message)):
        _assert_compile_contract(mutated)


@pytest.mark.parametrize("context", ["runner", "steps", "job"])
def test_compile_job_env_rejects_unavailable_contexts(context):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _assert_compile_contract(workflow)
    old = "PINEFORGE_COMPILE_REPORT_DIR: ${{ github.workspace }}/.ci/compile-report"
    assert workflow.count(old) == 1
    new = old + f"\n      INVALID_CONTEXT: ${{{{ {context}.workspace }}}}"
    with pytest.raises(AssertionError, match="compile job env must use actionlint-supported contexts"):
        _assert_compile_contract(workflow.replace(old, new, 1))


@pytest.mark.parametrize("old,new", [
    ("  compile:\n", "  compile:\n    timeout-minutes: 30\n"),
    ("  compile:\n", "  compile:\n    continue-on-error: true\n"),
    ("  compile:\n", "  compile:\n    concurrency:\n      cancel-in-progress: true\n"),
])
def test_workflow_controls_reject_time_limits_failure_bypasses_and_cancellation(old, new):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _assert_no_failure_bypasses(workflow)
    assert workflow.count(old) == 1
    with pytest.raises(
        AssertionError, match="workflow must not set time limits, failure bypasses or cancellation",
    ):
        _assert_no_failure_bypasses(workflow.replace(old, new, 1))
