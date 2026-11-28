"""The promotion guard looks up the PR of a fresh merge again.

GitHub's commit-to-PR association can lag a merge by seconds. On a push, the
"Resolve the merged PR" step of promote-baseline.yml therefore repeats the
association lookup, but only while the answer is valid and lists no pull
request at all: 3 lookups, 20 seconds apart. If the third answer is empty too,
the step writes ok=false, reports DEFERRED_NOT_FOUND and fails.

An answer that lists a pull request is final. No match and two matches skip as
before, without another lookup. A lookup that fails ends the step. So does an
answer with no page at all: it is not an empty answer, and the step refuses it
at once.

One push has no pull request by design: the release commit. After the third
empty answer the step reads the pushed commit's parent and file list, but only
when the release App pushed it. It then skips green, with ok=false and one line
naming the release_tag route, only when the push is that one commit (its first
parent is the commit main held before the push) and every changed path is in
this repository's release file set, which is VERSION alone. Another push
actor, a push of more than one commit, one more changed path, or a renamed
record that does not name the path it left, ends in DEFERRED_NOT_FOUND like
every other exhausted lookup. So does a commit read that lists no parents or
whose first parent names no commit, and a push event that names no previous
commit.

The shipped file also holds the release file set of the engine's repository,
which a repository name without `codegen` selects: VERSION,
docker/run_failure_codes_diff.json and a root-level CHANGELOG*. The rows that
count file records run there. A file list of 3,000 records is refused: the
commit API lists no more, so a list that long may be cut. The limit counts
records, never the path names they carry, so 1,500 renames still skip.

Each row runs the shipped step script with bash. The `gh` and `sleep` commands
on PATH are stand-ins that record their calls; the `sleep` one never waits.
"""
import json
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest


WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "promote-baseline.yml"
)
REPO = "owner/repo"
MERGE_SHA = "a" * 40
HEAD_SHA = "b" * 40
# The commit main held before the push, as the push event names it.
BEFORE_SHA = "c" * 40
# A commit pushed together with MERGE_SHA and before it: the tip's parent
# when the push holds two commits.
EARLIER_SHA = "d" * 40
LOOKUP = f"gh api --paginate --slurp repos/{REPO}/commits/{MERGE_SHA}/pulls?per_page=100"
FULL_PR = f"gh api repos/{REPO}/pulls/17"
WAIT = "sleep 20"
# The release rows run as this repository: its name puts the step on the
# code generator's axis, whose release file set is VERSION alone.
CODEGEN_REPO = "pineforge-4pass/pineforge-codegen-oss"
CODEGEN_LOOKUP = (
    f"gh api --paginate --slurp repos/{CODEGEN_REPO}/commits/{MERGE_SHA}/pulls?per_page=100"
)
CHANGED_FILES = (
    f"gh api --paginate --slurp repos/{CODEGEN_REPO}/commits/{MERGE_SHA}?per_page=100"
)
# The same read in REPO. That name holds no `codegen`: it puts the step on the
# engine's axis, whose release file set names a root-level CHANGELOG* too.
ENGINE_CHANGED_FILES = f"gh api --paginate --slurp repos/{REPO}/commits/{MERGE_SHA}?per_page=100"
RELEASE_APP = "pineforge-app[bot]"
PERSON = "writer"
RELEASE_LINE = (
    "release commit: baseline is recorded by the release_tag route, "
    "not post-merge promote\n"
)

# Gives each association lookup the next reply of its row: the pages GitHub
# lists, or an API error. A lookup after the last reply exits 93 and is never
# answered again. The pushed commit's file list is answered only for a row
# that names changed paths or file records; without them that read exits 95.
# The full PR comes from the environment. Any other call exits 97.
GH_STUB = r'''set -eu
printf "gh %s\n" "$*" >> "$CALLS"
if [ "$#" -eq 4 ] && [ "$1" = api ] && [ "$2" = --paginate ] && [ "$3" = --slurp ] &&
    [ "$4" = "repos/$REPO/commits/$SHA/pulls?per_page=100" ]; then
  count=0
  if [ -e "$GH_REPLIES/count" ]; then read -r count < "$GH_REPLIES/count"; fi
  count=$((count + 1))
  printf "%s\n" "$count" > "$GH_REPLIES/count"
  if [ -e "$GH_REPLIES/$count.json" ]; then
    printf "%s\n" "$(<"$GH_REPLIES/$count.json")"
  elif [ -e "$GH_REPLIES/$count.error" ]; then
    printf "%s\n" "$(<"$GH_REPLIES/$count.error")" >&2
    exit 1
  else
    exit 93
  fi
elif [ "$#" -eq 4 ] && [ "$1" = api ] && [ "$2" = --paginate ] && [ "$3" = --slurp ] &&
    [ "$4" = "repos/$REPO/commits/$SHA?per_page=100" ]; then
  if [ -e "$GH_REPLIES/files.json" ]; then
    printf "%s\n" "$(<"$GH_REPLIES/files.json")"
  else
    exit 95
  fi
elif [ "$#" -eq 2 ] && [ "$1" = api ] && [ "$2" = "repos/$REPO/pulls/17" ]; then
  printf "%s\n" "$GH_PR"
else
  exit 97
fi
'''
# Records the requested wait in the same call log and returns at once.
SLEEP_STUB = r'''printf "sleep %s\n" "$*" >> "$CALLS"
'''


@dataclass(frozen=True)
class ApiError:
    """A lookup that fails: `gh` prints this text to stderr and exits 1."""
    stderr: str


def merged_pr(number=17, *, base="main"):
    """The full record of a merged PR, as `gh api repos/<repo>/pulls/<number>` returns it."""
    return {
        "number": number, "state": "closed", "merged": True,
        "merge_commit_sha": MERGE_SHA, "merged_at": "2026-10-01T00:00:00Z",
        "base": {"ref": base},
        "head": {"sha": HEAD_SHA, "repo": {"full_name": REPO, "fork": False}},
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "author_association": "MEMBER", "user": {"login": "member"},
        "merged_by": {"login": "writer"},
    }


def listed(pr):
    """The association answer lists a summary of the PR, without its author or merger."""
    return {
        key: pr[key]
        for key in ("number", "state", "merge_commit_sha", "merged_at", "base", "head")
    }


def retry_notice(attempt):
    return (
        f"::notice::No merged PR found for {MERGE_SHA} (attempt {attempt}/3); "
        "retrying in 20 seconds\n"
    )


def skip_notice(count):
    return (
        f"::notice::found {count} merged PRs with merge commit {MERGE_SHA} "
        "and base main; skipping\n"
    )


EMPTY = [[]]
# An answer of zero pages. EMPTY is one page that lists no pull request; here
# no page was returned at all.
NO_PAGE = []
FOUND = [[listed(merged_pr())]]
NOT_FOUND = (
    f"DEFERRED_NOT_FOUND: no merged PR found for {MERGE_SHA} after 3 attempts; "
    "baseline unchanged"
)
NO_PAGE_REFUSAL = "merged PR response has no page"
RESOLVED_OUTPUTS = {
    "merge_sha": MERGE_SHA, "head_sha": HEAD_SHA, "pr_number": "17",
    "pr_url": f"https://github.com/{REPO}/pull/17",
    "merged_at": "2026-10-01T00:00:00Z", "head_repo": REPO, "head_is_fork": "false",
    "association": "MEMBER", "pr_author": "member", "merged_by": "writer", "ok": "true",
}


@dataclass(frozen=True)
class Row:
    name: str
    # One reply per association lookup, in order: the pages GitHub lists, or
    # an ApiError. Each row ends with a reply that would resolve the merge;
    # except in the first row, the guard must not ask for it.
    replies: tuple
    exit_code: int
    stdout: str
    outputs: dict
    # Every recorded gh and sleep call, in order.
    calls: tuple
    summary: str = ""
    stderr: str = ""
    # The repository the step runs in, and the actor of the push event.
    repository: str = REPO
    push_actor: str = PERSON
    # The paths the pushed commit changes, as its file list reports them:
    # each one a plain modification.
    changed: tuple = ()
    # Further file records of the pushed commit, given exactly as listed.
    records: tuple = ()
    # The pushed commit's parents, first parent first: each the commit it
    # names, or a parent record given exactly as listed. A push of that one
    # commit has the commit main held before the push as its first parent.
    # None is a commit read that lists no parents at all.
    parents: tuple | None = (BEFORE_SHA,)
    # The commit main held before the push, as the push event names it. An
    # empty value is a push event that names no previous commit.
    before: str = BEFORE_SHA


# The release App pushing its one VERSION commit. It is a row of the table
# below and the control of the refused release pushes after it.
RELEASE_SKIPS = Row(
    "the release App pushing only VERSION with no PR skips green",
    replies=(EMPTY, EMPTY, EMPTY, FOUND),
    exit_code=0,
    stdout=retry_notice(1) + retry_notice(2) + RELEASE_LINE,
    outputs={"ok": "false"},
    calls=(
        CODEGEN_LOOKUP, WAIT, CODEGEN_LOOKUP, WAIT, CODEGEN_LOOKUP, CHANGED_FILES,
    ),
    repository=CODEGEN_REPO, push_actor=RELEASE_APP, changed=("VERSION",),
)

ROWS = [
    Row(
        "an empty first association answer is retried and the merged PR is found",
        replies=(EMPTY, FOUND),
        exit_code=0,
        stdout=retry_notice(1),
        outputs=RESOLVED_OUTPUTS,
        calls=(LOOKUP, WAIT, LOOKUP, FULL_PR),
    ),
    Row(
        "three empty association answers end in DEFERRED_NOT_FOUND",
        replies=(EMPTY, EMPTY, EMPTY, FOUND),
        exit_code=1,
        stdout=(
            retry_notice(1) + retry_notice(2)
            + f"::error title=baseline-promotion::{NOT_FOUND}\n"
        ),
        outputs={"ok": "false"},
        calls=(LOOKUP, WAIT, LOOKUP, WAIT, LOOKUP),
        summary=f"baseline promotion: {NOT_FOUND}\n",
    ),
    Row(
        "three answers of zero pages are refused at once, without a retry",
        replies=(NO_PAGE, NO_PAGE, NO_PAGE, FOUND),
        exit_code=1,
        stdout=f"::error title=authz::{NO_PAGE_REFUSAL}\n",
        outputs={},
        calls=(LOOKUP,),
        summary=f"authz: REFUSED: {NO_PAGE_REFUSAL}\n",
    ),
    Row(
        "a non-empty answer without a match skips without a retry",
        replies=([[listed(merged_pr(base="release"))]], FOUND),
        exit_code=0,
        stdout=skip_notice(0),
        outputs={"ok": "false"},
        calls=(LOOKUP,),
    ),
    Row(
        "two matching PRs still defer",
        replies=([[listed(merged_pr())], [listed(merged_pr(18))]], FOUND),
        exit_code=0,
        stdout=skip_notice(2),
        outputs={"ok": "false"},
        calls=(LOOKUP,),
    ),
    Row(
        "an API error after an empty lookup is terminal",
        replies=(EMPTY, ApiError("HTTP 403"), FOUND),
        exit_code=1,
        stdout=retry_notice(1),
        outputs={},
        calls=(LOOKUP, WAIT, LOOKUP),
        stderr="HTTP 403\n",
    ),
    RELEASE_SKIPS,
    Row(
        "the same VERSION commit pushed by another actor ends in DEFERRED_NOT_FOUND",
        replies=(EMPTY, EMPTY, EMPTY, FOUND),
        exit_code=1,
        stdout=(
            retry_notice(1) + retry_notice(2)
            + f"::error title=baseline-promotion::{NOT_FOUND}\n"
        ),
        outputs={"ok": "false"},
        # Another actor's push never reaches the file list.
        calls=(CODEGEN_LOOKUP, WAIT, CODEGEN_LOOKUP, WAIT, CODEGEN_LOOKUP),
        summary=f"baseline promotion: {NOT_FOUND}\n",
        repository=CODEGEN_REPO, push_actor=PERSON, changed=("VERSION",),
    ),
    Row(
        "the release App changing VERSION and a file of the other release set "
        "ends in DEFERRED_NOT_FOUND",
        replies=(EMPTY, EMPTY, EMPTY, FOUND),
        exit_code=1,
        stdout=(
            retry_notice(1) + retry_notice(2)
            + f"::error title=baseline-promotion::{NOT_FOUND}\n"
        ),
        outputs={"ok": "false"},
        calls=(
            CODEGEN_LOOKUP, WAIT, CODEGEN_LOOKUP, WAIT, CODEGEN_LOOKUP, CHANGED_FILES,
        ),
        summary=f"baseline promotion: {NOT_FOUND}\n",
        repository=CODEGEN_REPO, push_actor=RELEASE_APP,
        # The second path belongs to the engine's release commit only.
        changed=("VERSION", "docker/run_failure_codes_diff.json"),
    ),
]


def release_calls(repository):
    """Every call of a release App push: three lookups, two waits, then the commit read."""
    lookup, changed_files = {
        CODEGEN_REPO: (CODEGEN_LOOKUP, CHANGED_FILES),
        REPO: (LOOKUP, ENGINE_CHANGED_FILES),
    }[repository]
    return (lookup, WAIT, lookup, WAIT, lookup, changed_files)


def refused_release(name, *, repository=CODEGEN_REPO, **push):
    """A release App push that reads the commit and still ends in DEFERRED_NOT_FOUND."""
    return Row(
        name,
        replies=(EMPTY, EMPTY, EMPTY, FOUND),
        exit_code=1,
        stdout=(
            retry_notice(1) + retry_notice(2)
            + f"::error title=baseline-promotion::{NOT_FOUND}\n"
        ),
        outputs={"ok": "false"},
        calls=release_calls(repository),
        summary=f"baseline promotion: {NOT_FOUND}\n",
        repository=repository, push_actor=RELEASE_APP, **push,
    )


def skipped_release(name, *, repository, **push):
    """A release App push that reads the commit and skips green with the release line."""
    return Row(
        name,
        replies=(EMPTY, EMPTY, EMPTY, FOUND),
        exit_code=0,
        stdout=retry_notice(1) + retry_notice(2) + RELEASE_LINE,
        outputs={"ok": "false"},
        calls=release_calls(repository),
        repository=repository, push_actor=RELEASE_APP, **push,
    )


# Each of these differs from RELEASE_SKIPS in one fact of the push: of the
# pushed commit, or of the push event.
REFUSED_RELEASES = [
    pytest.param(
        refused_release(
            "the release App pushing two commits whose tip changes only VERSION "
            "ends in DEFERRED_NOT_FOUND",
            # The tip's first parent is the other pushed commit, not the
            # commit main held before the push: that commit is never listed.
            changed=("VERSION",), parents=(EARLIER_SHA,),
        ),
        id="two-commits-with-a-version-only-tip",
    ),
    pytest.param(
        refused_release(
            "the release App renaming a file to VERSION without the path it left "
            "ends in DEFERRED_NOT_FOUND",
            records=({"filename": "VERSION", "status": "renamed"},),
        ),
        id="renamed-version-record-without-previous-filename",
    ),
    pytest.param(
        refused_release(
            "a commit read that lists no parents ends in DEFERRED_NOT_FOUND",
            changed=("VERSION",), parents=None,
        ),
        id="commit-read-that-lists-no-parents",
    ),
    pytest.param(
        refused_release(
            "a commit read whose first parent names no commit ends in DEFERRED_NOT_FOUND",
            # The one parent record carries no commit id.
            changed=("VERSION",), parents=({},),
        ),
        id="commit-read-whose-first-parent-names-no-commit",
    ),
    pytest.param(
        refused_release(
            "a push event that names no previous commit ends in DEFERRED_NOT_FOUND",
            changed=("VERSION",), before="",
        ),
        id="push-event-that-names-no-previous-commit",
    ),
]


def root_changelog_rename(index):
    """One file record that carries two path names, both root-level CHANGELOG names."""
    return {
        "filename": f"CHANGELOG-{index}.md", "status": "renamed",
        "previous_filename": f"CHANGELOG-{index}.txt",
    }


# The commit API's own limit is 3,000 file records, so the limit counts
# records: a rename is one record that carries two path names. These rows run
# in REPO, on the engine's axis, where every root-level CHANGELOG* name is a
# release path. Each entry holds the pushes of one row, in the order they run.
RECORD_COUNT_ROWS = [
    pytest.param(
        (
            skipped_release(
                "the release App pushing 1,500 renames between root-level CHANGELOG names "
                "skips green",
                repository=REPO,
                records=tuple(root_changelog_rename(index) for index in range(1500)),
            ),
        ),
        id="engine-axis-1500-renames-between-root-level-changelog-names-skip-green",
    ),
    pytest.param(
        (
            # Control: one rename among 2,999 records makes 3,000 path names,
            # one record below the limit.
            skipped_release(
                "the release App pushing 2,999 records carrying 3,000 path names skips green",
                repository=REPO,
                changed=tuple(f"CHANGELOG-{index}.md" for index in range(1, 2999)),
                records=(root_changelog_rename(0),),
            ),
            refused_release(
                "the release App pushing a file list of 3,000 records "
                "ends in DEFERRED_NOT_FOUND",
                repository=REPO,
                changed=tuple(f"CHANGELOG-{index}.md" for index in range(3000)),
            ),
        ),
        id="engine-axis-3000-records-refused-and-2999-records-carrying-3000-path-names-still-skip",
    ),
]


def resolve_step_script():
    """Take the "Resolve the merged PR" run script out of the shipped workflow."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    scripts = re.findall(
        r"^      - name: Resolve the merged PR\n"
        r"(?:        [^\n]*\n)*?"
        r"        run: \|\n((?:          [^\n]*\n|\n)+)",
        workflow, re.M,
    )
    assert len(scripts) == 1, (
        "promote-baseline.yml must hold exactly one 'Resolve the merged PR' run script"
    )
    return textwrap.dedent(scripts[0])


def run_resolve_step(row, tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.fail("NOT RUN: the promotion guard lookup rows require bash on the test runner")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    for name, body in (("gh", GH_STUB), ("sleep", SLEEP_STUB)):
        stub = bin_dir / name
        stub.write_text(f"#!{bash}\n{body}", encoding="utf-8")
        stub.chmod(0o700)
    replies_dir = tmp_path / "replies"
    replies_dir.mkdir()
    for index, reply in enumerate(row.replies, start=1):
        if isinstance(reply, ApiError):
            (replies_dir / f"{index}.error").write_text(reply.stderr + "\n", encoding="utf-8")
        else:
            (replies_dir / f"{index}.json").write_text(json.dumps(reply) + "\n", encoding="utf-8")
    if row.changed or row.records:
        # The pushed commit with its parents and its file list, as one page
        # of a paged read.
        files = [{"filename": path, "status": "modified"} for path in row.changed]
        files.extend(row.records)
        commit = {"sha": MERGE_SHA}
        if row.parents is not None:
            commit["parents"] = [
                parent if isinstance(parent, dict) else {"sha": parent}
                for parent in row.parents
            ]
        commit["files"] = files
        (replies_dir / "files.json").write_text(json.dumps([commit]) + "\n", encoding="utf-8")
    script = tmp_path / "resolve.bash"
    script.write_text(resolve_step_script(), encoding="utf-8")
    output_path = tmp_path / "output"
    summary_path = tmp_path / "summary"
    calls_path = tmp_path / "calls"
    env = {
        "PATH": str(bin_dir),
        "EVENT_NAME": "push", "REF": "refs/heads/main", "REPO": row.repository,
        "SHA": MERGE_SHA, "PUSH_ACTOR": row.push_actor, "BEFORE": row.before,
        "GH_TOKEN": "",
        "INPUT_MERGE_SHA": "", "INPUT_HEAD_SHA": "", "INPUT_PR_NUMBER": "",
        "INPUT_PR_URL": "", "INPUT_MERGED_AT": "",
        "GITHUB_OUTPUT": str(output_path), "GITHUB_STEP_SUMMARY": str(summary_path),
        "CALLS": str(calls_path), "GH_REPLIES": str(replies_dir),
        "GH_PR": json.dumps(merged_pr()),
    }
    result = subprocess.run(
        [bash, "--noprofile", "--norc", str(script)],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    written = output_path.read_text(encoding="utf-8").splitlines() if output_path.exists() else []
    outputs = dict(line.split("=", 1) for line in written)
    assert len(written) == len(outputs), f"the guard must not write an output twice: {written}"
    calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    return result, outputs, calls, summary


def assert_row(row, tmp_path):
    result, outputs, calls, summary = run_resolve_step(row, tmp_path)
    got = (
        f"{row.name}: exit {result.returncode}\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}\noutputs: {outputs}\ncalls: {calls}\nsummary: {summary}"
    )
    assert result.returncode == row.exit_code, got
    assert outputs == row.outputs, got
    assert calls == list(row.calls), got
    assert result.stdout == row.stdout, got
    assert summary == row.summary, got
    assert result.stderr == row.stderr, got


@pytest.mark.parametrize("row", ROWS, ids=[row.name for row in ROWS])
def test_promote_guard_lookup_retry(row, tmp_path):
    assert_row(row, tmp_path)


@pytest.mark.parametrize("row", REFUSED_RELEASES)
def test_release_app_push_is_refused_unless_it_is_one_commit_with_every_path_named(row, tmp_path):
    # The control runs first, in this same test: the App pushing its one
    # VERSION commit still skips green. Only then is the row's push refused.
    for name, case in (("control", RELEASE_SKIPS), ("refused", row)):
        directory = tmp_path / name
        directory.mkdir()
        assert_row(case, directory)


@pytest.mark.parametrize("pushes", RECORD_COUNT_ROWS)
def test_release_file_list_limit_counts_records_not_path_names(pushes, tmp_path):
    # Every push runs in a directory of its own. Where a row holds two, the
    # first is its control: it must skip green before the second is refused.
    for number, case in enumerate(pushes, start=1):
        directory = tmp_path / f"push-{number}"
        directory.mkdir()
        assert_row(case, directory)
