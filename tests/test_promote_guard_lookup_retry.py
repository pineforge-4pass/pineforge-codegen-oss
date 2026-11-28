"""The promotion guard looks up the PR of a fresh merge again.

GitHub's commit-to-PR association can lag a merge by seconds. On a push, the
"Resolve the merged PR" step of promote-baseline.yml therefore repeats the
association lookup, but only while the answer is valid and lists no pull
request at all: 3 lookups, 20 seconds apart. If the third answer is empty too,
the step writes ok=false, reports DEFERRED_NOT_FOUND and fails.

An answer that lists a pull request is final. No match and two matches skip as
before, without another lookup. A lookup that fails ends the step.

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
LOOKUP = f"gh api --paginate --slurp repos/{REPO}/commits/{MERGE_SHA}/pulls?per_page=100"
FULL_PR = f"gh api repos/{REPO}/pulls/17"
WAIT = "sleep 20"

# Gives each association lookup the next reply of its row: the pages GitHub
# lists, or an API error. A lookup after the last reply exits 93 and is never
# answered again. The full PR comes from the environment. Any other call
# exits 97.
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
FOUND = [[listed(merged_pr())]]
NOT_FOUND = (
    f"DEFERRED_NOT_FOUND: no merged PR found for {MERGE_SHA} after 3 attempts; "
    "baseline unchanged"
)
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


def run_resolve_step(replies, tmp_path):
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
    for index, reply in enumerate(replies, start=1):
        if isinstance(reply, ApiError):
            (replies_dir / f"{index}.error").write_text(reply.stderr + "\n", encoding="utf-8")
        else:
            (replies_dir / f"{index}.json").write_text(json.dumps(reply) + "\n", encoding="utf-8")
    script = tmp_path / "resolve.bash"
    script.write_text(resolve_step_script(), encoding="utf-8")
    output_path = tmp_path / "output"
    summary_path = tmp_path / "summary"
    calls_path = tmp_path / "calls"
    env = {
        "PATH": str(bin_dir),
        "EVENT_NAME": "push", "REF": "refs/heads/main", "REPO": REPO, "SHA": MERGE_SHA,
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


@pytest.mark.parametrize("row", ROWS, ids=[row.name for row in ROWS])
def test_promote_guard_lookup_retry(row, tmp_path):
    result, outputs, calls, summary = run_resolve_step(row.replies, tmp_path)
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
