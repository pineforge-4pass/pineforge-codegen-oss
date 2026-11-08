"""Main-owned, stdlib-only PR admission checker and its executable controls.

The hosted workflow invokes this file with -I; pytest imports its controls.
Head workflow blobs are data. No head Python, action or shell is executed.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import textwrap


POD_JOBS = {"workflow-lint", "package-files", "pytest", "differential"}
TEST_POD_JOBS = {"workflow-lint", "package-files", "pytest"}
POD_RUNNER = "ubuntu-24.04"
BOTS = {"dependabot[bot]", "pineforge-app[bot]"}
CHECKER = "python3 -I tests/test_gke_pr_admission.py"
TRIGGER = """on:
  pull_request_target:
    branches: [main]
    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]
"""
SHA = re.compile(r"[0-9a-f]{40}")
NATIVE_WORKFLOW = ".github/workflows/engine-main-touched.yml"
NATIVE_DRIVER = "scripts/ci_engine_main_touched.py"
APPROVED_NATIVE_DRIVER_SHA256 = "a708705d02bd80e6714e20c16310f737756b18a961a4ea435582c5c8577812f8"
NATIVE_CALL = """    needs: [admit]
    if: ${{ needs.admit.outputs.allowed == 'true' }}
    # Permission ceiling for the shared workflow's dispatch-only publisher.
    # Its changes/native execution jobs explicitly reduce to contents: read.
    permissions: {contents: read, actions: read, checks: write}
    uses: ./.github/workflows/engine-main-touched.yml
    with:
      codegen_base_ref: ${{ needs.admit.outputs.base_sha }}
      codegen_head_ref: ${{ needs.admit.outputs.head_sha }}
      definition: ${{ github.sha }}
      workers: "8"
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def gh_json(endpoint):
    result = subprocess.run(
        ["gh", "api", endpoint], check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def authorize(env, api=gh_json):
    repo = env["REPO"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "invalid repository")
    require(env["HEAD_REPO"] == repo, "head repository differs")
    require(env["HEAD_IS_FORK"] == "false", "pull request is a fork")
    require(env["DRAFT"] == "false", "pull request is a draft")
    require(env["BASE_REF"] == "main", "base is not main")
    for key in ("BASE_SHA", "HEAD_SHA"):
        require(SHA.fullmatch(env[key]), f"invalid {key} SHA")
    require(re.fullmatch(r"[1-9][0-9]*", env["PR_NUMBER"]), "invalid PR number")
    for key in ("PR_AUTHOR", "TRIGGERING_ACTOR", "ACTOR"):
        login = env[key]
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?", login), "invalid actor login")
        if login not in BOTS:
            permission = api(f"repos/{repo}/collaborators/{login}/permission")
            require(isinstance(permission, dict) and permission.get("permission") in {
                "admin", "maintain", "write",
            }, f"{login} does not have write permission")
    live = api(f"repos/{repo}/pulls/{env['PR_NUMBER']}")
    require(live["state"] == "open", "live pull request is not open")
    require(live["draft"] is False, "live pull request is a draft")
    require(live["head"]["sha"] == env["HEAD_SHA"], "live head differs from the event")
    require(live["base"]["ref"] == "main", "live base is not main")
    require(live["head"]["repo"]["full_name"] == repo
            and live["head"]["repo"]["fork"] is False, "live repository differs or is a fork")
    return env["BASE_SHA"], env["HEAD_SHA"]


def authorize_proof(env, api=gh_json):
    """Dispatch grants no status-writing authority and accepts public inputs only."""
    repo = env["REPO"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "invalid repository")
    for key in ("BASE_SHA", "HEAD_SHA", "DEFINITION_SHA"):
        require(SHA.fullmatch(env[key]), f"invalid {key} SHA")
    require(env.get("SOURCE_EVENT", "pull_request") in {"pull_request", "push_main"},
            "invalid proof source event")
    for key in ("ACTOR", "TRIGGERING_ACTOR"):
        login = env[key]
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?", login), "invalid actor login")
        if login not in BOTS:
            permission = api(f"repos/{repo}/collaborators/{login}/permission")
            require(isinstance(permission, dict) and permission.get("permission") in {
                "admin", "maintain", "write",
            }, f"{login} does not have write permission")
    require(api(f"repos/{repo}").get("private") is False, "proof repository is not public")
    for key in ("BASE_SHA", "HEAD_SHA"):
        if key == "BASE_SHA" and env[key] == "0" * 40 and env.get("SOURCE_EVENT") == "push_main":
            continue
        require(api(f"repos/{repo}/commits/{env[key]}").get("sha") == env[key],
                f"public {key} commit differs")


def authorize_push(env):
    """Bind the immutable event; a later main tip is deliberately not queried."""
    require(env["EVENT"] == "push" and env["REF"] == "refs/heads/main",
            "native push admission requires main")
    require(env["DELETED"] == "false", "deleted main is not a testable push")
    for key in ("BASE_SHA", "HEAD_SHA", "EVENT_SHA", "DEFINITION_SHA"):
        require(SHA.fullmatch(env[key]), f"invalid {key} SHA")
    require(env["HEAD_SHA"] != "0" * 40, "push has no tested head")
    require(env["HEAD_SHA"] == env["EVENT_SHA"] == env["DEFINITION_SHA"],
            "push event, tested head and definition differ")
    repo = env["REPO"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "invalid repository")
    # GitHub already authorized this protected-main push. Run every event,
    # including app/bot pushes; a later collaborator snapshot cannot suppress
    # native execution. PR and write-capable dispatch retain their actor checks.


def create_native_check(repo, payload):
    """Only the isolated publisher calls the checks write API."""
    result = subprocess.run(
        ["gh", "api", "--method", "POST", f"repos/{repo}/check-runs", "--input", "-"],
        input=json.dumps(payload), check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def report_native_check(env, api=gh_json, create=create_native_check):
    """Publish a source check from this attempt's actual completed native job."""
    require(env["EVENT"] == "workflow_dispatch", "historical publication requires dispatch")
    require(env["REF"].startswith("refs/heads/"), "proof definition must be a published branch")
    authorize_proof(env, api)
    for key in ("RUN_ID", "RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", env[key]), f"invalid {key}")
    require(env["SERVER_URL"] == "https://github.com", "unexpected GitHub server")
    needs = json.loads(env["NEEDS"])
    require(isinstance(needs, dict) and set(needs) == {"changes", "native"},
            "native publication dependencies differ")
    decision, native = needs["changes"], needs["native"]
    require(isinstance(decision, dict) and decision.get("result") == "success"
            and isinstance(native, dict), "native decision did not succeed")
    outputs = decision.get("outputs", {})
    require(isinstance(outputs, dict) and outputs.get("native") == "true"
            and outputs.get("base") == env["BASE_SHA"] and outputs.get("head") == env["HEAD_SHA"]
            and outputs.get("definition") == env["DEFINITION_SHA"]
            and outputs.get("source_event") == env["SOURCE_EVENT"]
            and outputs.get("mode") == (
                "full" if env["SOURCE_EVENT"] == "push_main" else "touched"
            )
            and isinstance(outputs.get("tree"), str) and SHA.fullmatch(outputs["tree"])
            and isinstance(outputs.get("plan"), str)
            and re.fullmatch(r"[0-9a-f]{64}", outputs["plan"]),
            "native publication source/decision binding differs")
    prefix = f"repos/{env['REPO']}/actions/runs/{env['RUN_ID']}/attempts/{env['RUN_ATTEMPT']}"
    run = api(prefix)
    require(type(run.get("id")) is int and type(run.get("run_attempt")) is int
            and run.get("id") == int(env["RUN_ID"])
            and run.get("run_attempt") == int(env["RUN_ATTEMPT"])
            and run.get("event") == "workflow_dispatch"
            and run.get("head_sha") == env["DEFINITION_SHA"]
            and run.get("head_branch") == env["REF"].removeprefix("refs/heads/")
            and run.get("path") == NATIVE_WORKFLOW
            and run.get("repository", {}).get("full_name") == env["REPO"],
            "native workflow run/definition binding differs")
    jobs_seen = []
    total = None
    for page in range(1, 101):
        response = api(f"{prefix}/jobs?per_page=100&page={page}")
        require(isinstance(response.get("jobs"), list)
                and type(response.get("total_count")) is int, "unreadable native job inventory")
        if total is None:
            total = response["total_count"]
        require(response["total_count"] == total and 0 <= total <= 10000,
                "native job inventory changed or exceeds bounds")
        jobs_seen.extend(response["jobs"])
        if len(jobs_seen) >= total:
            break
        require(bool(response["jobs"]), "incomplete native job inventory")
    require(len(jobs_seen) == total, "incomplete native job inventory")
    matches = [job for job in jobs_seen if job.get("name") == "Engine main native"]
    require(len(matches) == 1, "native execution job is missing or ambiguous")
    job = matches[0]
    require(type(job.get("id")) is int and job["id"] > 0, "invalid native job ID")
    details = (f"{env['SERVER_URL']}/{env['REPO']}/actions/runs/{env['RUN_ID']}"
               f"/job/{job['id']}")
    require(type(job.get("run_id")) is int and job.get("run_id") == int(env["RUN_ID"])
            and job.get("head_sha") == env["DEFINITION_SHA"]
            and job.get("status") == "completed" and job.get("html_url") == details,
            "native job attempt/definition/details binding differs")
    state = native.get("result")
    actual = job.get("conclusion")
    require((state == actual and state in {"success", "failure", "cancelled"})
            or (state == "failure" and actual in {"timed_out", "startup_failure", "action_required"}),
            "native job result differs from completed GitHub job")
    execution = native.get("outputs", {})
    require(isinstance(execution, dict), "malformed native execution outputs")
    expected_mode = outputs["mode"]
    ordinary = (
        execution.get("completed_plan") == outputs["plan"]
        and execution.get("audit_errors") == "0"
        and execution.get("execution_mode") == expected_mode
        and isinstance(execution.get("inventory"), str)
        and re.fullmatch(r"[0-9a-f]{64}", execution["inventory"]) is not None
        and all(isinstance(execution.get(key), str) and SHA.fullmatch(execution[key])
                for key in ("engine_sha", "engine_tree"))
        and execution.get("pytest_exit") in {"0", "1", "2", "3", "4", "5"}
        and all(execution.get(f"construction_{legacy}") in {"passed", "failed"}
                for legacy in ("false", "true"))
    )
    if state == "success":
        require(ordinary and execution.get("pytest_exit") == "0"
                and execution.get("audit_errors") == "0"
                and execution.get("coverage_complete") == "true"
                and execution.get("executed_plan") == outputs["plan"]
                and (expected_mode != "full" or (
                    execution.get("full_success") == "true"
                    and execution.get("full_executed_plan") == outputs["plan"]
                ))
                and all(execution[f"construction_{legacy}"] == "passed"
                        for legacy in ("false", "true")),
                "successful native job lacks bound ordinary execution")
        conclusion = "success"
    elif state == "cancelled":
        conclusion = "cancelled"
    else:
        conclusion = "failure" if ordinary else "action_required"
    # Failure summaries report observed outcomes; they never label setup failure
    # as assertion RED or infer the expected result from the selected source SHA.
    observed = {
        "definition_sha": env["DEFINITION_SHA"], "source_event": env["SOURCE_EVENT"],
        "codegen_before": env["BASE_SHA"], "codegen_sha": env["HEAD_SHA"],
        "codegen_tree": outputs["tree"], "plan": outputs["plan"],
        "mode": expected_mode, "inventory": execution.get("inventory"),
        "engine_sha": execution.get("engine_sha"), "engine_tree": execution.get("engine_tree"),
        "coverage_complete": execution.get("coverage_complete"),
        "full_success": execution.get("full_success"),
        "full_executed_plan": execution.get("full_executed_plan"),
        "audit_errors": execution.get("audit_errors"),
        "native_job_result": actual, "ordinary_execution": ordinary,
        "pytest_exit": execution.get("pytest_exit"),
        "construction_false": execution.get("construction_false"),
        "construction_true": execution.get("construction_true"),
        "note": "Read native identity/build/audit logs; setup or incomplete execution is not assertion evidence.",
    }
    payload = {
        "name": "engine-main-native-source", "head_sha": env["HEAD_SHA"],
        "status": "completed", "conclusion": conclusion, "details_url": details,
        "external_id": f"native:{env['RUN_ID']}:{env['RUN_ATTEMPT']}:{job['id']}",
        "output": {"title": "Engine main native source execution",
                   "summary": "```json\n" + json.dumps(observed, indent=2) + "\n```"},
    }
    posted = create(env["REPO"], payload)
    require(type(posted.get("id")) is int, "native check creation returned no ID")
    check = api(f"repos/{env['REPO']}/check-runs/{posted['id']}")
    require(all(check.get(key) == payload[key] for key in (
        "name", "head_sha", "status", "conclusion", "details_url", "external_id",
    )), "created native check does not bind the requested source/result")
    print(json.dumps({key: check.get(key) for key in (
        "id", "head_sha", "conclusion", "details_url", "html_url",
    )}, sort_keys=True), flush=True)
    return check


def safe_path(path):
    """Reject credential names and symlinks before following any local input."""
    path = Path(path)
    parts = path.parts
    name = "/".join(parts)
    require(not any(part in {".ssh", ".aws", ".codex"} or part.endswith(".env")
                    for part in parts)
            and ".config/zsh/keys" not in name
            and ".config/pineforge" not in name, f"protected path refused: {path}")
    require(".." not in parts, f"unsafe path refused: {path}")
    for parent in reversed((path, *path.parents)):
        require(not parent.is_symlink(), f"symlink path refused: {parent}")
    return path


def read_sources(root, paths):
    paths = list(paths)
    print("Files to read: " + json.dumps([str(Path(root) / p) for p in paths]), flush=True)
    checked = [(p, safe_path(Path(root) / p)) for p in paths]
    return {p: workflow_text(path.read_bytes()) for p, path in checked}


def workflow_paths(root):
    root = Path(root)
    directory = safe_path(Path(root) / ".github/workflows")
    print(f"Directory to list: {directory}", flush=True)
    paths = [str(p.relative_to(root)) for p in sorted(directory.iterdir())
             if p.suffix in {".yml", ".yaml"}]
    pending = [safe_path(root / ".github/actions")]
    while pending:
        directory = pending.pop()
        print(f"Directory to inspect: {directory}", flush=True)
        if not directory.exists():
            continue
        for child in sorted(directory.iterdir()):
            child = safe_path(child)
            if child.is_dir():
                pending.append(child)
            else:
                paths.append(str(child.relative_to(root)))
    paths.extend([".github/actionlint.yaml", "package.json", NATIVE_DRIVER])
    print("Files to read: " + json.dumps([str(root / p) for p in paths]), flush=True)
    return paths


def select_blobs(tree):
    require(isinstance(tree, dict) and tree.get("truncated") is False, "head tree is truncated or unreadable")
    require(isinstance(tree.get("tree"), list), "head tree entries are unreadable")
    kept = []
    for entry in tree["tree"]:
        require(isinstance(entry, dict) and isinstance(entry.get("path"), str), "unreadable tree path")
        path = entry["path"]
        if not (path in {".github/actionlint.yaml", "package.json", NATIVE_DRIVER} or path.startswith((
                ".github/workflows/", ".github/actions/"))):
            continue
        if entry.get("type") == "tree":
            continue
        require(entry.get("type") == "blob", f"unsupported entry type: {path!r}")
        require(entry.get("mode") in {"100644", "100755"}, f"unsupported mode: {path!r}")
        parts = path.split("/")
        require(all(re.fullmatch(r"[A-Za-z0-9._-]+", p) and p not in {".", ".."}
                    for p in parts), f"unsafe tree path: {path!r}")
        # This checks names only; no path from the API is opened here.
        require(not any(p in {".ssh", ".aws", ".codex"} or p.endswith(".env") for p in parts)
                and ".config/zsh/keys" not in path
                and ".config/pineforge" not in path, f"protected path refused: {path!r}")
        size = entry.get("size")
        require(type(size) is int and size >= 0, f"unreadable tree size: {path!r}")
        require(size <= 1024 * 1024, f"file exceeds 1 MiB: {path!r}")
        require(isinstance(entry.get("sha"), str) and SHA.fullmatch(entry["sha"]), "invalid blob SHA")
        kept.append(entry)
    require(len(kept) <= 100, "head workflow count exceeds 100")
    require(sum(e["size"] for e in kept) <= 4 * 1024 * 1024, "head workflows exceed 4 MiB")
    return kept


def decode_blob(entry, response):
    require(response.get("encoding") == "base64" and isinstance(response.get("content"), str),
            "unreadable blob encoding")
    data = base64.b64decode("".join(response["content"].split()), validate=True)
    require(len(data) == entry["size"], "blob length differs from tree")
    return data


def workflow_text(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("workflow is not UTF-8") from error
    require(len(data) <= 1024 * 1024, "workflow exceeds 1 MiB")
    return text


def strip_comment(value):
    quote = None
    index = 0
    while index < len(value):
        if value.startswith("${{", index) and quote is None:
            end = value.find("}}", index + 3)
            index = len(value) if end < 0 else end + 2
            continue
        char = value[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
        index += 1
    return value.rstrip()


def yaml_records(text):
    """Conservative YAML structure scanner; actionlint is the syntax parser.

    Refuse unsupported structure instead of interpreting it differently from
    GitHub. Block scalar contents are code/data, not YAML routing keys.
    """
    require(len(text.split("\n")) <= 20001, "workflow line count exceeds 20000")
    require(not any(
        c in "\ufeff\u0085\u2028\u2029" or "\u202a" <= c <= "\u202e"
        or "\u2066" <= c <= "\u2069" or (ord(c) < 32 and c not in "\n\t")
        for c in text.replace("\r\n", "\n")
    ), "unsupported control character")
    records, stack, seen = [], [], set()
    scalar_indent = None
    for number, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        require(len(raw) <= 4096, "workflow line length exceeds 4096")
        leading = re.match(r"[ \t]*", raw)[0]
        require("\t" not in leading, "tab indentation is refused")
        indent, line = len(leading), raw[len(leading):]
        if scalar_indent is not None:
            if line and indent <= scalar_indent:
                scalar_indent = None
            else:
                continue
        if not line or line.startswith("#"):
            continue
        require(indent // 2 <= 40, "workflow structure depth exceeds 40")
        require(not line.startswith(("---", "...")), "YAML document marker refused")
        require(not line.startswith(("?", "- ?")), "explicit YAML key refused")
        match = re.fullmatch(
            r"""(?P<item>-\s+)?(?P<key>"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|[A-Za-z_][A-Za-z0-9_-]*|<<)\s*:(?P<value>.*)""",
            line,
        )
        if not match:
            # Only scalar sequence items are allowed outside key:value lines.
            require(line.startswith("- ") and not any(c in line[2:] for c in "{}"),
                    "unsupported YAML structure")
            value = strip_comment(line[2:]).strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, f"@item{number}"))
            key = ""
        else:
            key = match["key"].strip("'\"")
            require("\\" not in match["key"], "escaped YAML key refused")
            require(key != "<<", "YAML merge key refused")
            if match["item"]:
                while stack and stack[-1][0] >= indent:
                    stack.pop()
                stack.append((indent, f"@item{number}"))
            else:
                while stack and stack[-1][0] >= indent:
                    stack.pop()
            scope = tuple(item[1] for item in stack)
            require((scope, key) not in seen, f"duplicate YAML key: {key}")
            seen.add((scope, key))
            stack.append((indent + (2 if match["item"] else 0), key))
            value = strip_comment(match["value"]).strip()
            records.append((indent, key, value))
        visible = re.sub(r"\$\{\{.*?\}\}", "", value)
        visible = re.sub(r""""(?:\\.|[^"\\])*"|'(?:''|[^'])*'""", "", visible)
        require(not re.search(r"(?:^|[\[{,:])\s*[&*!]", visible),
                "YAML anchor, alias or tag refused")
        flow_depth = 0
        for char in visible:
            if char in "[{":
                flow_depth += 1
            elif char in "]}":
                flow_depth -= 1
            require(indent // 2 + flow_depth <= 40, "workflow flow depth exceeds 40")
        if key == "permissions" and value.startswith(("{", "[")):
            require(re.fullmatch(r"\{\s*(?:[a-z-]+:\s*[a-z]+(?:,\s*[a-z-]+:\s*[a-z]+)*)?\s*\}", value),
                    "permissions must be a plain flow mapping")
            names = re.findall(r"([a-z-]+):", value)
            require(len(names) == len(set(names)), "duplicate permissions")
        elif key == "runs-on":
            require(re.fullmatch(r"(?:ubuntu-(?:latest|24\.04)|gke-[0-9]+cpu-[0-9]+gb-amd64)", value),
                    "every runner must be a bare literal scalar")
        elif key in {"uses", "environment", "secrets", "container"}:
            require(not value.startswith(("[", "{")), f"{key} flow value refused")
        if key == "uses":
            require(re.fullmatch(r"[A-Za-z0-9_./@-]+", value), "uses must be a plain literal reference")
            if value.startswith("./"):
                parts = value[2:].split("/")
                require(value.startswith(("./.github/actions/", "./.github/workflows/"))
                        and all(p not in {"", ".", "..", ".ssh", ".aws", ".codex"}
                                and not p.endswith(".env") for p in parts)
                        and ".config/zsh/keys" not in value
                        and ".config/pineforge" not in value,
                        "protected or unsafe local action reference")
        if value.startswith(("|", ">")):
            scalar_indent = indent + (2 if match and match["item"] else 0)
    return records


def jobs(text):
    require("\njobs:\n" in text, "workflow has no block jobs")
    body = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M))
    require(bool(matches) and len(matches) == len(re.findall(r"^  [^\s#][^\n]*$", body, re.M)),
            "workflow jobs must all use plain block structure")
    return {m[1]: body[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(body)]
            for i, m in enumerate(matches)}


def value_at(text, indent, key):
    values = [v for depth, k, v in yaml_records(text) if depth == indent and k == key]
    require(len(values) == 1, f"expected one {key}")
    return values[0]


def runs(body, key="run"):
    result = []
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^( *)(- )?" + re.escape(key) + r": (.*)$", lines[index])
        if not match:
            index += 1
            continue
        key_indent = len(match[1]) + (2 if match[2] else 0)
        if match[3] == "|":
            end = index + 1
            while end < len(lines) and (
                not lines[end] or len(lines[end]) - len(lines[end].lstrip(" ")) > key_indent
            ):
                end += 1
            result.append(textwrap.dedent("\n".join(lines[index + 1:end])).strip())
            index = end
        else:
            result.append(match[3])
            index += 1
    return result


def check_workflows(sources):
    driver = sources.get(NATIVE_DRIVER)
    require(isinstance(driver, str),
            f"{NATIVE_DRIVER}: native implementation differs from base")
    require(len(sources) <= 100 and sum(len(v.encode()) for v in sources.values()) <= 4 * 1024 * 1024,
            "workflow file set exceeds bounds")
    manifest = json.loads(sources["package.json"])
    require(isinstance(manifest, dict), "package cache manifest must be an object")
    engines = manifest.get("devEngines", {})
    require(isinstance(engines, dict), "package cache devEngines must be an object")
    require("packageManager" not in manifest and "packageManager" not in engines,
            "package-manager cache metadata requires disabling the remaining trusted setup-node default first")
    for path, text in sources.items():
        if not path.endswith((".yml", ".yaml")):
            continue
        records = yaml_records(text)
        if path.startswith(".github/workflows/"):
            header = text.split("\njobs:\n", 1)[0]
            if path == ".github/workflows/test.yml":
                require(not any(
                    (depth == 2 and k == "pull_request")
                    or (depth == 0 and k == "on" and re.search(r"\bpull_request\b", value))
                    for depth, k, value in yaml_records(header)
                ), "test.yml must not have a pull_request trigger")
                for name, body in jobs(text).items():
                    require(value_at(body, 4, "runs-on")
                            == (POD_RUNNER if name in TEST_POD_JOBS else "ubuntu-24.04"),
                            f"test.yml {name}: runner differs")
            if any((depth == 2 and k == "pull_request_target")
                   or (depth == 0 and k == "on" and re.search(r"\bpull_request_target\b", value))
                   for depth, k, value in yaml_records(header)):
                require(path == ".github/workflows/gke-pr.yml",
                        f"{path}: target trigger outside admission")
            for body in jobs(text).values():
                require(body.startswith("\n"), "workflow jobs must use plain block structure")
                for step in step_blocks(body):
                    node = [v for _, k, v in yaml_records(step)
                            if k == "uses" and v.startswith("actions/setup-node@")]
                    if not node:
                        continue
                    # The older release/publish pin has no implicit cache.
                    # The unchanged promotion pin is safe only while the
                    # package manifest above cannot opt it into a cache.
                    old_pin = "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020"
                    promotion_pin = "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1"
                    require(node == [old_pin]
                            or (path == ".github/workflows/promote-baseline.yml"
                                and node == [promotion_pin])
                            or step_mapping(step, "with").get("package-manager-cache") == "false",
                            f"{path}: automatic package-manager cache must be disabled")
        for depth, key, value in records:
            require(key != "cache" and not (key == "uses" and value.startswith("actions/cache")),
                    f"{path}: Actions cache restore/save/input refused")
            test_runner = (path == ".github/workflows/test.yml" and depth == 4
                           and key == "runs-on" and value == POD_RUNNER)
            if key == "runs-on" and value.startswith("gke-"):
                require(path == ".github/workflows/gke-pr.yml" or test_runner,
                        f"{path}: GKE routing outside admission")
            if path != ".github/workflows/gke-pr.yml" and not test_runner:
                require("gke-" not in value and "GKE PR" not in value,
                        f"{path}: GKE routing outside admission")
            require(key not in {"jobs", "steps"} or not value, "jobs and steps must use block structure")
            if key == "uses" and not value.startswith("./"):
                require(re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", value),
                        f"{path}: action must be SHA pinned")
            if key == "uses":
                require("gke-pr.yml" not in value, "admission workflow must not be reusable")
                if "engine-main-touched.yml" in value:
                    require(path == ".github/workflows/gke-pr.yml" and depth == 4
                            and value == "./" + NATIVE_WORKFLOW,
                            "native workflow call outside the admitted route")
        if path != ".github/workflows/gke-pr.yml":
            require(not re.search(r"^\s*(?:group:\s*gke-|workflows:.*GKE PR)", text, re.M),
                    f"{path}: GKE routing outside admission")
    legacy_lint = jobs(sources[".github/workflows/test.yml"])["workflow-lint"]
    require(value_at(legacy_lint, 4, "needs") == "[authz]",
            "authorized workflow lint dependency differs")
    require(not any(depth == 4 and k == "if" for depth, k, _ in yaml_records(legacy_lint)),
            "canonical workflow lint must remain unconditional")
    lint_steps = step_blocks(legacy_lint)
    lint_checkout = next(
        step for step in lint_steps if any(
            k == "uses" and v.startswith("actions/checkout@") for _, k, v in yaml_records(step)
        )
    )
    require(step_mapping(lint_checkout, "with").get("ref")
            == '"${{ github.event.pull_request.head.sha || github.sha }}"',
            "workflow lint must check out the exact head")
    require(any("python3 scripts/check_workflow_app_environments.py" in script
                for script in runs(legacy_lint)), "hosted App environment guard is missing")
    require(any("-config-file .github/actionlint.yaml" in script
                for script in runs(legacy_lint)),
            "workflow lint must use the repository actionlint config")
    # A single, exact base-owned reusable implementation is admitted. Candidate
    # changes to this implementation need a separate base-policy adoption first.
    root = Path(__file__).resolve().parents[1]
    installed = read_sources(root, [NATIVE_WORKFLOW, NATIVE_DRIVER])
    workflow = sources[".github/workflows/gke-pr.yml"]
    bootstrap_jobs = set(jobs(workflow))
    bootstrap_old = POD_JOBS | {"admit", "test-check", "result"}
    bootstrap_new = bootstrap_old | {"engine-main"}
    require(bootstrap_jobs in (bootstrap_old, bootstrap_new),
            "admission job inventory differs")
    has_native = bootstrap_jobs == bootstrap_new
    # TEMPORARY STAGE A ONLY. Two complete caller graphs, and only the
    # exact push-header activation paired with the new graph, are admitted.
    # The shared workflow body stays pinned. Bootstrap keeps the installed
    # driver; activation requires one base-owned digest of its complete bytes.
    push = "  push:\n    branches: [main]\n"
    trusted = installed[NATIVE_WORKFLOW]
    require(trusted.count(push) <= 1, "installed native push header differs")
    inactive = trusted.replace(push, "", 1)
    expected_native = (inactive.replace("\non:\n", "\non:\n" + push, 1)
                       if has_native else inactive)
    require(sources.get(NATIVE_WORKFLOW) == expected_native,
            f"{NATIVE_WORKFLOW}: native implementation differs from base")
    require((hashlib.sha256(driver.encode("utf-8")).hexdigest() == APPROVED_NATIVE_DRIVER_SHA256
             if has_native else driver == installed[NATIVE_DRIVER]),
            f"{NATIVE_DRIVER}: native implementation differs from base")
    require(TRIGGER in workflow, "admission trigger differs")
    header = workflow.split("\njobs:\n", 1)[0]
    require(header.split("\non:\n", 1)[1].split("\nrun-name:", 1)[0].strip()
            == TRIGGER.split("on:\n", 1)[1].strip(), "admission trigger has extra events")
    require({k for depth, k, _ in yaml_records(header) if depth == 0}
            == {"name", "on", "run-name", "permissions", "concurrency"},
            "admission header must not inherit env or defaults")
    require(value_at(header, 0, "permissions") == "{}", "workflow permissions must be empty")
    require('run-name: "gke-pr #${{ github.event.pull_request.number }} ${{ github.event.pull_request.head.sha }}"'
            in header, "admission run binding differs")
    require("  group: gke-pr-${{ github.event.pull_request.number }}\n  cancel-in-progress: true"
            in header, "admission concurrency differs")
    all_records = yaml_records(workflow)
    require(not any(k == "id-token" or "id-token" in v for _, k, v in all_records), "id-token refused")
    require(not any(re.search(r"\bsecrets\b", part.partition("}}")[0])
                    for part in workflow.split("${{")[1:]),
            "secret reference refused")
    require(not any(k == "environment" for _, k, _ in all_records), "environment refused")
    require(not any(k in {"timeout-minutes", "continue-on-error"} for _, k, _ in all_records),
            "admission must fail closed without a time limit")
    require(not any(k in {"defaults", "container", "services", "working-directory"}
                    for _, k, _ in all_records), "admission must not override the execution context")
    require(not any(k in {"include", "exclude"} for _, k, _ in all_records),
            "admission matrix must not add or exclude rows")
    require(not any(k in {"env", "with"} and v for _, k, v in all_records),
            "admission env and with must use block mappings")
    bodies = jobs(workflow)
    require(set(bodies) == (bootstrap_new if has_native else bootstrap_old),
            "admission job inventory differs")
    for name, body in bodies.items():
        if name == "engine-main":
            require(body.strip() == NATIVE_CALL.strip(),
                    "native workflow call must bind the exact admitted route and identities")
            continue
        is_pod = name in POD_JOBS
        require(value_at(body, 4, "runs-on") == (POD_RUNNER if is_pod else "ubuntu-24.04"),
                f"{name}: runner differs")
        expected_permissions = (
            "{contents: read, statuses: write}" if name == "result"
            else "{}" if name == "test-check" else "{contents: read}"
        )
        require(value_at(body, 4, "permissions") == expected_permissions, f"{name}: permissions differ")
        require(not any(depth == 4 and key == "name" for depth, key, _ in yaml_records(body))
                or (name == "result" and value_at(body, 4, "name") == "gate"),
                f"{name}: check name differs")
        if is_pod:
            require(value_at(body, 4, "needs") == "[admit]", f"{name}: admission dependency differs")
            require(value_at(body, 4, "if") == "${{ needs.admit.outputs.allowed == 'true' }}",
                    f"{name}: allowed guard differs")
            require(not re.search(r"github\s*(?:\[\s*['\"]token|[.]\s*token)", body)
                    and "GH_TOKEN:" not in body, f"{name}: credential refused")
            expected_env = ({
                "PF_CI_LABELS": "'[\"ubuntu-24.04\"]'",
                "PYTHONHASHSEED": '"0"',
            } if name in {"pytest", "differential"} else {})
            require(mapping_at(body, "env", 4) == expected_env, f"{name}: pod environment differs")
            for step in step_blocks(body):
                require(not step_env(step), f"{name}: step environment refused")
                for depth, key, value in yaml_records(step):
                    if key == "if":
                        require(name == "differential" and value == "always()"
                                and any(k == "uses" and v.startswith("actions/upload-artifact@")
                                        for _, k, v in yaml_records(step)),
                                f"{name}: proof steps must not be skipped")
        if name in {"admit", "result"}:
            require(not any(k in {"if", "shell"} and depth >= 6
                            for depth, k, _ in yaml_records(body)),
                    f"{name}: hosted checks must not be skipped or change shell")
            require(not any(k == "env" and depth == 4 for depth, k, _ in yaml_records(body)),
                    f"{name}: hosted job environment must not be overridden")
        for script in [*runs(body), *runs(body, "script")]:
            require("${{" not in script, "PR interpolation in code refused")
            require(not re.search(r"(^|[;&|\s])timeout(?:\s|$)", script), "time limit refused")
        require(not any(k == "uses" and v.startswith("./")
                        for _, k, v in yaml_records(body)), "local workflow/action use refused")
        checkouts = [step for step in step_blocks(body)
                     if any(k == "uses" and v.startswith("actions/checkout@")
                            for _, k, v in yaml_records(step))]
        if checkouts:
            require(len(checkouts) == 1, f"{name}: checkout count differs")
            inputs = step_mapping(checkouts[0], "with")
            require(inputs.get("repository") == "${{ github.repository }}"
                    and inputs.get("ref") == ("${{ needs.admit.outputs.head_sha }}" if is_pod else "${{ github.sha }}")
                    and inputs.get("persist-credentials") == "false"
                    and set(inputs) <= {"repository", "ref", "persist-credentials", "fetch-depth"},
                    f"{name}: checkout must bind trusted base or admitted head without credentials")
        else:
            require(name == "test-check", f"{name}: checkout missing")
        if name in {"test-check", "result"}:
            require(value_at(body, 4, "if") == "always()", f"{name}: always() required")
    require(runs(bodies["admit"]) == [
        CHECKER + " --identity", CHECKER + " --workflows",
        'echo "allowed=true" >> "$GITHUB_OUTPUT"',
    ], "identity, workflow checks and last admission output must be ordered and exact")
    require("        id: allowed\n        run: echo \"allowed=true\" >> \"$GITHUB_OUTPUT\""
            in bodies["admit"], "last admission output differs")
    require(value_at(bodies["admit"], 6, "allowed") == "${{ steps.allowed.outputs.allowed }}"
            and value_at(bodies["admit"], 6, "head_sha") == "${{ steps.identity.outputs.head_sha }}"
            and value_at(bodies["admit"], 6, "base_sha") == "${{ steps.identity.outputs.base_sha }}",
            "admission output binding differs")
    admit_steps = step_blocks(bodies["admit"])
    require(len(admit_steps) == 4 and value_at(admit_steps[1], 8, "id") == "identity",
            "identity step order differs")
    require(step_env(admit_steps[1]) == identity_env(), "identity environment binding differs")
    require(step_env(admit_steps[2]) == {
        "REPO": "${{ github.repository }}",
        "HEAD_SHA": "${{ steps.identity.outputs.head_sha }}",
        "GH_TOKEN": "${{ github.token }}",
    }, "workflow check must use the identity head")
    require(not step_env(admit_steps[0]) and not step_env(admit_steps[3]),
            "admission checkout and last output must not override env")
    require(runs(bodies["result"]) == [CHECKER + " --result"], "result must use the base checker")
    result_steps = step_blocks(bodies["result"])
    require(len(result_steps) == 2 and not step_env(result_steps[0])
            and step_env(result_steps[1]) == {
                **identity_env(), "NEEDS": "${{ toJSON(needs) }}",
                **({"DEFINITION_SHA": "${{ github.sha }}"} if has_native else {}),
            },
            "result environment binding differs")
    require("    needs: [admit, workflow-lint, package-files, pytest]" in bodies["test-check"],
            "test-check dependencies differ")
    require(value_at(bodies["result"], 4, "needs")
            == ("[admit, workflow-lint, package-files, pytest, differential, test-check, engine-main]"
                if has_native else
                "[admit, workflow-lint, package-files, pytest, differential, test-check]"),
            "result dependencies differ")
    require(value_at(bodies["result"], 4, "name") == "gate", "gate check name differs")
    test_check_steps = step_blocks(bodies["test-check"])
    require(len(test_check_steps) == 1
            and step_env(test_check_steps[0]) == {"NEEDS": "${{ toJSON(needs) }}"}
            and value_at(test_check_steps[0], 8, "shell") == "python3 {0}",
            "test-check must consume the actual dependency results")
    expected_commands = {
        "workflow-lint": [CHECKER + " --lint"],
        "package-files": [
            "set -euo pipefail\ntest -f LICENSE\ntest -f README.md",
            'python -m pip install -e ".[dev]"',
            "python -m pytest -q tests/test_license_entity.py",
        ],
        "pytest": ['python -m pip install -e ".[dev]"', "python -m pytest -q -n auto"],
        "differential": [
            "set -euo pipefail\npython -m pip install -e .\nnpm ci",
            "npm run gate:selftest", "npm audit --audit-level=high", "npm run gate:full",
        ],
    }
    for name, commands in expected_commands.items():
        require(runs(bodies[name]) == commands, f"{name}: proof commands differ")
    require(value_at(bodies["pytest"], 8, "python-version") == '["3.11", "3.12", "3.13", "3.14"]',
            "pytest matrix versions differ")


def step_blocks(body):
    lines = body.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.startswith("      - ")]
    return ["".join(lines[start:starts[i + 1] if i + 1 < len(starts) else len(lines)])
            for i, start in enumerate(starts)]


def step_env(step):
    return step_mapping(step, "env")


def step_mapping(step, field):
    return mapping_at(step, field, 8)


def mapping_at(text, field, indent):
    result, in_env = {}, False
    for depth, key, value in yaml_records(text):
        if depth <= indent:
            in_env = depth == indent and key == field and value == ""
        elif in_env and depth == indent + 2:
            result[key] = value
    return result


def identity_env():
    return {
        "REPO": "${{ github.repository }}",
        "HEAD_REPO": "${{ github.event.pull_request.head.repo.full_name || '' }}",
        "HEAD_IS_FORK": "${{ toJSON(github.event.pull_request.head.repo.fork) }}",
        "DRAFT": "${{ toJSON(github.event.pull_request.draft) }}",
        "BASE_REF": "${{ github.event.pull_request.base.ref || '' }}",
        "BASE_SHA": "${{ github.event.pull_request.base.sha || '' }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha || '' }}",
        "PR_NUMBER": "${{ github.event.pull_request.number }}",
        "PR_AUTHOR": "${{ github.event.pull_request.user.login || '' }}",
        "TRIGGERING_ACTOR": "${{ github.triggering_actor || '' }}",
        "ACTOR": "${{ github.actor || '' }}",
        "GH_TOKEN": "${{ github.token }}",
    }


def install_actionlint(directory):
    directory = safe_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    archive = safe_path(directory / "actionlint.tar.gz")
    print(f"Files to read: {json.dumps([str(archive)])}", flush=True)
    subprocess.run([
        "curl", "--fail", "--silent", "--show-error", "--location",
        "--proto", "=https", "--tlsv1.2", "--output", str(archive),
        "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz",
    ], check=True)
    require(hashlib.sha256(archive.read_bytes()).hexdigest()
            == "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
            "actionlint archive checksum differs")
    with tarfile.open(archive) as package:
        print("Archive member to read: actionlint", flush=True)
        member = package.getmember("actionlint")
        require(member.isfile(), "actionlint archive member is not a regular file")
        binary = safe_path(directory / "actionlint")
        binary.write_bytes(package.extractfile(member).read())
    binary.chmod(0o700)
    return binary


def admit_workflows(env, api=gh_json):
    repo, head = env["REPO"], env["HEAD_SHA"]
    require(SHA.fullmatch(head), "invalid head SHA")
    entries = select_blobs(api(f"repos/{repo}/git/trees/{head}?recursive=1"))
    print("Head files fetched as data: " + json.dumps([e["path"] for e in entries]), flush=True)
    sources = {
        e["path"]: workflow_text(decode_blob(e, api(f"repos/{repo}/git/blobs/{e['sha']}")))
        for e in entries
    }
    # Shape checks precede actionlint; semantic rules follow its syntax parse.
    for path, text in sources.items():
        if path.endswith((".yml", ".yaml")):
            yaml_records(text)
    root = safe_path(Path(env["RUNNER_TEMP"]) / "gke-pr-data")
    root.mkdir()
    for path, text in sources.items():
        destination = safe_path(root.joinpath(*path.split("/")))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    config = safe_path(Path.cwd() / ".github/actionlint.yaml")
    print("Files to read: " + json.dumps([str(config), *[
        str(root / p) for p in sources
    ]]), flush=True)
    binary = install_actionlint(Path(env["RUNNER_TEMP"]) / "gke-pr-tools")
    workflows = [str(root / p) for p in sources
                 if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))]
    require(bool(workflows), "no workflow files fetched")
    subprocess.run([str(binary), "-config-file", str(config), "-shellcheck=", "-pyflakes=", *workflows],
                   cwd=root, check=True)
    check_workflows(sources)


def proof_success(needs, expected):
    return (isinstance(needs, dict) and set(needs) == set(expected)
            and all(isinstance(row, dict) and row.get("result") == "success"
                    for row in needs.values())
            and isinstance(needs["admit"].get("outputs"), dict)
            and needs["admit"].get("outputs", {}).get("allowed") == "true")


def report_result(env, api=gh_json):
    try:
        # Decide from the installed base caller, never from supplied NEEDS.
        # The six-leg predicate disappears with this entire bootstrap delta
        # at activation. A new installed caller cannot accept old NEEDS.
        root = Path(__file__).resolve().parents[1]
        installed = read_sources(root, [".github/workflows/gke-pr.yml"])
        expected = set(jobs(installed[".github/workflows/gke-pr.yml"]))
        old = POD_JOBS | {"admit", "test-check", "result"}
        require(expected in (old, old | {"engine-main"}), "installed caller inventory differs")
        has_native = "engine-main" in expected
        expected -= {"result"}
        needs = json.loads(env["NEEDS"])
        passed = (proof_success(needs, expected)
                  and needs["admit"]["outputs"].get("head_sha") == env["HEAD_SHA"]
                  and needs["admit"]["outputs"].get("base_sha") == env["BASE_SHA"]
                  and (not has_native
                       or native_success(needs["engine-main"].get("outputs"), env)))
    except (ValueError, KeyError, TypeError):
        passed = False
    # Refusals still publish failure on the immutable event head. No artifact
    # or head checkout is used. A stale run cannot publish success.
    try:
        authorize(env, api)
    except (ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        passed = False
    require(SHA.fullmatch(env["HEAD_SHA"]), "invalid status head SHA")
    state = "success" if passed else "failure"
    subprocess.run([
        "gh", "api", "--method", "POST", f"repos/{env['REPO']}/statuses/{env['HEAD_SHA']}",
        "-f", "context=gate", "-f", f"state={state}",
        "-f", "description=Full PR checks passed" if passed else "description=PR admission or checks failed",
        "-f", f"target_url={env['GITHUB_SERVER_URL']}/{env['REPO']}/actions/runs/{env['GITHUB_RUN_ID']}",
    ], check=True)
    require(passed, "PR admission or checks failed")


def native_success(outputs, env):
    if not isinstance(outputs, dict):
        return False
    if not all(isinstance(value, str) for value in outputs.values()):
        return False
    if not (
        outputs.get("decision_result") == "success"
        and outputs.get("source_event") == "pull_request"
        and outputs.get("base") == env["BASE_SHA"]
        and outputs.get("head") == env["HEAD_SHA"]
        and outputs.get("definition") == env["DEFINITION_SHA"]
        and SHA.fullmatch(outputs.get("tree", ""))
        and re.fullmatch(r"[0-9a-f]{64}", outputs.get("plan", ""))
    ):
        return False
    return (
        outputs.get("native") == "true" and outputs.get("native_result") == "success"
        and outputs.get("mode") == outputs.get("execution_mode") == "touched"
        and outputs.get("coverage_complete") == "true"
        and re.fullmatch(r"[0-9a-f]{64}", outputs.get("inventory", ""))
        and outputs.get("executed_plan") == outputs["plan"]
    ) or (
        outputs.get("native") == "false" and outputs.get("native_result") == "skipped"
        and outputs.get("mode") == "skip"
        and outputs.get("execution_mode", "") == ""
        and outputs.get("inventory", "") == ""
        and outputs.get("coverage_complete", "") == ""
        and outputs.get("executed_plan", "") == ""
    )


def main():
    require(len(sys.argv) == 2, "expected one admission mode")
    env = dict(os.environ)
    if sys.argv[1] == "--identity":
        base, head = authorize(env)
        output = safe_path(env["GITHUB_OUTPUT"])
        print(f"File to append: {output}", flush=True)
        with output.open("a", encoding="utf-8") as stream:
            stream.write(f"base_sha={base}\nhead_sha={head}\n")
    elif sys.argv[1] == "--workflows":
        admit_workflows(env)
    elif sys.argv[1] == "--result":
        report_result(env)
    elif sys.argv[1] == "--proof-identity":
        authorize_proof(env)
    elif sys.argv[1] == "--push-identity":
        authorize_push(env)
    elif sys.argv[1] == "--native-check":
        report_native_check(env)
    elif sys.argv[1] == "--lint":
        root = Path.cwd()
        paths = workflow_paths(root)
        sources = read_sources(root, paths)
        check_workflows(sources)
        config = safe_path(root / ".github/actionlint.yaml")
        script = safe_path(root / "scripts/check_workflow_app_environments.py")
        print("Files to read: " + json.dumps([str(config), str(script)]), flush=True)
        binary = install_actionlint(Path(env["RUNNER_TEMP"]) / "gke-pr-tools")
        workflow_files = [p for p in paths if p.startswith(".github/workflows/")]
        subprocess.run([str(binary), "-config-file", str(config), "-shellcheck=", "-pyflakes=", *workflow_files],
                       check=True)
        subprocess.run(["python3", str(script)], check=True)
    else:
        raise ValueError("unknown admission mode")
    return 0


def identity_fixture():
    env = {
        "REPO": "owner/project", "HEAD_REPO": "owner/project", "HEAD_IS_FORK": "false",
        "DRAFT": "false", "BASE_REF": "main", "BASE_SHA": "a" * 40,
        "HEAD_SHA": "b" * 40, "PR_NUMBER": "7", "PR_AUTHOR": "author",
        "TRIGGERING_ACTOR": "rerunner", "ACTOR": "actor",
    }
    responses = {f"repos/owner/project/collaborators/{name}/permission": {"permission": "write"}
                 for name in ("author", "rerunner", "actor")}
    responses["repos/owner/project/pulls/7"] = {
        "state": "open", "draft": False, "base": {"ref": "main"},
        "head": {"sha": env["HEAD_SHA"], "repo": {"full_name": env["REPO"], "fork": False}},
    }
    return env, responses


def test_identity_accepts_only_write_actors():
    env, responses = identity_fixture()
    calls = []

    def api(endpoint):
        calls.append(endpoint)
        return responses[endpoint]

    assert authorize(env, api) == (env["BASE_SHA"], env["HEAD_SHA"])
    assert calls == [
        "repos/owner/project/collaborators/author/permission",
        "repos/owner/project/collaborators/rerunner/permission",
        "repos/owner/project/collaborators/actor/permission",
        "repos/owner/project/pulls/7",
    ]


def test_identity_refusals():
    import copy
    import pytest

    for name, change, message in [
        ("fork", {"HEAD_IS_FORK": "true"}, "fork"),
        ("foreign", {"HEAD_REPO": "outside/project"}, "repository"),
        ("draft", {"DRAFT": "true"}, "draft"),
        ("base", {"BASE_REF": "other"}, "base"),
        ("bad-base-sha", {"BASE_SHA": "bad"}, "SHA"),
        ("bad-head-sha", {"HEAD_SHA": "bad"}, "SHA"),
        ("empty-actor", {"ACTOR": ""}, "login"),
    ]:
        env, responses = identity_fixture()
        env.update(change)
        with pytest.raises(ValueError, match=message):
            authorize(env, lambda endpoint: responses[endpoint])
    for login in ("author", "rerunner", "actor"):
        for permission in ("read", "triage", "none", "", "write\nnone"):
            env, responses = identity_fixture()
            responses[f"repos/owner/project/collaborators/{login}/permission"] = {
                "permission": permission,
            }
            with pytest.raises(ValueError, match=f"{login}.*write permission"):
                authorize(env, lambda endpoint: responses[endpoint])
    env, responses = identity_fixture()
    live = responses["repos/owner/project/pulls/7"]
    for field, value, message in [
        ("state", "closed", "open"),
        ("draft", True, "draft"),
        ("head", {**live["head"], "sha": "c" * 40}, "live head"),
        ("head", {**live["head"], "repo": {"full_name": "outside/project", "fork": False}}, "live repository"),
        ("head", {**live["head"], "repo": {"full_name": env["REPO"], "fork": True}}, "live repository"),
        ("base", {"ref": "other"}, "live base"),
    ]:
        changed = copy.deepcopy(responses)
        changed["repos/owner/project/pulls/7"][field] = value
        with pytest.raises(ValueError, match=message):
            authorize(env, lambda endpoint: changed[endpoint])
    with pytest.raises(RuntimeError, match="API unavailable"):
        authorize(env, lambda endpoint: (_ for _ in ()).throw(RuntimeError("API unavailable")))


def test_tree_refusals_and_nested_action():
    import pytest

    entry = {
        "path": ".github/actions/nested/action.yml", "type": "blob",
        "mode": "100644", "size": 1, "sha": "d" * 40,
    }
    tree = {"truncated": False, "tree": [
        {"path": ".github/actions/nested", "type": "tree", "mode": "040000"},
        entry,
    ]}
    assert select_blobs(tree) == [entry]
    for changes, message in [
        ({"type": "commit"}, "entry type"),
        ({"mode": "120000"}, "mode"),
        ({"path": ".github/workflows/evil.yml\n"}, "path"),
        ({"path": ".github/actions/../evil.yml"}, "path"),
        ({"path": ".github/actions/token.env"}, "protected"),
        ({"path": ".github/actions/.ssh/key"}, "protected"),
        ({"size": 1024 * 1024 + 1}, "1 MiB"),
        ({"size": True}, "size"),
    ]:
        with pytest.raises(ValueError, match=message):
            select_blobs({"truncated": False, "tree": [{**entry, **changes}]})
    with pytest.raises(ValueError, match="truncated"):
        select_blobs({**tree, "truncated": True})
    with pytest.raises(ValueError, match="100"):
        select_blobs({"truncated": False, "tree": [
            {**entry, "path": f".github/actions/item{i}.yml"} for i in range(101)
        ]})
    with pytest.raises(ValueError, match="4 MiB"):
        select_blobs({"truncated": False, "tree": [
            {**entry, "path": f".github/actions/item{i}.yml", "size": 1024 * 1024}
            for i in range(5)
        ]})
    with pytest.raises(ValueError, match="blob length"):
        decode_blob(entry, {"encoding": "base64", "content": "YWI="})


def test_yaml_refusals_and_real_workflow_positive_control():
    import pytest

    for text, message in [
        ("a: &route value\n", "anchor"),
        ("a: *route\n", "alias"),
        ("a: !tag value\n", "tag"),
        ("a:\n  <<: *route\n", "merge"),
        ("a: 1\na: 2\n", "duplicate"),
        ('"runs\\x2don": x\n', "escaped"),
        ("a: 1\n---\nb: 2\n", "document"),
        ("? runs-on\n: x\n", "explicit"),
        ("a:\n\tb: 1\n", "tab"),
        ("a: 1\rb: 2\n", "control"),
        ("\ufeffa: 1\n", "control"),
        ("a: \u202ehello\n", "control"),
        ("a: \x00\n", "control"),
        ('"runs-\n on": x\n', "structure"),
        ("runs-on: {group: gke-runners}\n", "literal"),
        ("runs-on: ${{ fromJSON(inputs.runner) }}\n", "literal"),
        ("permissions: {contents: read, contents: write}\n", "permissions"),
        ("uses: [actions/checkout@main]\n", "flow"),
        ("uses: ./.github/actions/../../.ssh/key\n", "protected"),
        ('uses: "./.github/actions/\\x2e\\x2e/key"\n', "plain literal"),
        ("uses: ./.github/actions/token.env\n", "protected"),
        ("a: [*route]\n", "anchor"),
        ("a: " + "x" * 4096 + "\n", "line length"),
        ("a:\n" + "  # row\n" * 20000, "line count"),
        ("  " * 41 + "a: 1\n", "depth"),
    ]:
        with pytest.raises(ValueError, match=message):
            yaml_records(text)
    with pytest.raises(ValueError, match="UTF-8"):
        workflow_text(b"\xff")
    yaml_records(
        "permissions: {contents: read}\n"
        "a: ${{ success() && github.actor != '' }}\n"
        "run: |\n  echo '*.yml ${{ env.VALUE }}'\n  echo '&text'\n"
    )
    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root, workflow_paths(root))
    check_workflows(sources)


def test_plain_if_boolean_operators_are_not_yaml_anchor_tokens():
    # Raw if conditions are expressions too. Anchor syntax is positional;
    # rejecting every ampersand would refuse a real workflow condition.
    records = yaml_records(
        "if: needs.changes.outputs.code == 'true' && github.ref == 'refs/heads/main'\n"
        "permissions: {}\n"
    )
    assert records[0] == (
        0, "if", "needs.changes.outputs.code == 'true' && github.ref == 'refs/heads/main'",
    )
    inline = yaml_records(
        "steps:\n"
        "  - run: |\n"
        "      echo script\n"
        "    env:\n"
        "      AUTH: value\n"
    )
    assert (4, "env", "") in inline
    assert (6, "AUTH", "value") in inline


def test_real_workflow_mutations():
    import pytest

    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root, workflow_paths(root))
    original = sources[".github/workflows/gke-pr.yml"]
    for before, after, message in [
        ("pull_request_target:", "pull_request:", "trigger"),
        ("permissions: {}", "permissions: {contents: write}", "permissions"),
        ("runs-on: ubuntu-24.04", "runs-on: gke-8cpu-16gb-amd64", "runner"),
        ("persist-credentials: false", "persist-credentials: true", "checkout"),
        ("ref: ${{ github.sha }}", "ref: ${{ github.event.pull_request.head.sha }}", "checkout"),
        ("--identity", "--workflows", "identity"),
        ('echo "allowed=true"', 'echo "allowed=false"', "last admission"),
        ("\n    if: always()\n", "\n    if: success()\n", "always"),
        ("npm ci", "npm ci\n          echo '${{ github.event.pull_request.title }}'", "interpolation"),
        ("permissions: {contents: read}", "permissions: {contents: read, id-token: write}", "id-token"),
        ("    runs-on: ubuntu-24.04", "    environment: release\n    runs-on: ubuntu-24.04", "environment"),
        ("        run: npm run gate:full", "        run: echo '${{ secrets.TOKEN }}'", "secret"),
        ("        run: npm run gate:full", "        run: echo \"${{ secrets['TOKEN'] }}\"", "secret"),
        ("        run: npm run gate:full", "        run: echo '${{ toJSON(secrets) }}'", "secret"),
        ('          node-version: "22"', '          node-version: "22"\n          cache: npm', "cache"),
        ("          package-manager-cache: false", "          package-manager-cache: true", "automatic package-manager cache"),
        ("        id: allowed\n", "        id: allowed\n        if: always()\n", "hosted checks"),
        ("      allowed: ${{ steps.allowed.outputs.allowed }}", "      allowed: 'true'", "output binding"),
        ("          HEAD_SHA: ${{ steps.identity.outputs.head_sha }}", "          HEAD_SHA: ${{ github.sha }}", "identity head"),
        ("    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]",
         "    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]\n  workflow_dispatch:",
         "extra events"),
        ("        run: npm run gate:full", "        run: timeout 30 npm run gate:full", "time limit"),
        ("    runs-on: ubuntu-24.04", "    runs-on: ubuntu-24.04\n    timeout-minutes: 15", "time limit"),
        ("        run: npm run gate:full", "        run: true", "proof commands"),
        ("        run: python -m pytest -q -n auto\n", "        run: echo pytest\n", "proof commands"),
        ('      PYTHONHASHSEED: "0"', '      PYTHONHASHSEED: ${{ toJSON(github) }}', "pod environment"),
        ("      - name: Run the test suite\n", "      - name: Run the test suite\n"
         "        env:\n          AUTH: ${{ toJSON(github) }}\n", "step environment"),
    ]:
        assert before in original, before
        with pytest.raises(ValueError, match=message):
            check_workflows({**sources, ".github/workflows/gke-pr.yml": original.replace(before, after, 1)})
    with pytest.raises(ValueError, match="GKE routing"):
        check_workflows({**sources, ".github/workflows/extra.yml":
                         "on: pull_request\njobs:\n  evil:\n    runs-on: gke-8cpu-16gb-amd64\n"})
    with pytest.raises(ValueError, match="cache"):
        check_workflows({**sources, ".github/workflows/extra.yml":
                         "on: push\njobs:\n  evil:\n    runs-on: ubuntu-24.04\n"
                         "    steps:\n      - uses: actions/cache/restore@" + "e" * 40 + "\n"})
    with pytest.raises(ValueError, match="block structure"):
        check_workflows({**sources, ".github/workflows/extra.yml":
                         "on: push\njobs:\n  hidden: {runs-on: '${{ inputs.runner }}'}\n"
                         "  visible:\n    runs-on: ubuntu-24.04\n"})
    for manifest in (
        '{"packageManager":"npm@11"}',
        '{"devEngines":{"packageManager":{"name":"npm","version":"11"}}}',
    ):
        with pytest.raises(ValueError, match="remaining trusted setup-node default"):
            check_workflows({**sources, "package.json": manifest})
    for path in (".github/workflows/gate.yml", ".github/workflows/extra.yml"):
        text = sources.get(path, "on:\n  workflow_dispatch:\njobs:\n  hosted:\n    runs-on: ubuntu-24.04\n")
        mutant = text.replace("on:\n", "on:\n  pull_request_target:\n", 1)
        with pytest.raises(ValueError, match="target trigger outside admission"):
            check_workflows({**sources, path: mutant})
    for trigger in (
        "on: pull_request_target",
        "on: [push, pull_request_target]",
        "on: {pull_request_target: {types: [opened]}}",
    ):
        with pytest.raises(ValueError, match="target trigger outside admission"):
            check_workflows({**sources, ".github/workflows/extra.yml":
                             trigger + "\njobs:\n  hosted:\n    runs-on: ubuntu-24.04\n"})


def test_native_admission_pins_the_only_call_callee_driver_and_result_dependency():
    import pytest

    root = Path(__file__).resolve().parents[1]
    sources = bootstrap_fixture(read_sources(root, workflow_paths(root)), native=True)
    path = ".github/workflows/gke-pr.yml"
    original = sources[path]
    call = jobs(original)["engine-main"]
    for before, after in [
        ("needs: [admit]", "needs: []"),
        ("outputs.allowed == 'true'", "outputs.allowed != 'false'"),
        ("outputs.base_sha", "outputs.head_sha"),
        ("outputs.head_sha", "outputs.base_sha"),
        ("definition: ${{ github.sha }}", "definition: ${{ needs.admit.outputs.head_sha }}"),
        ('workers: "8"', 'workers: "auto"'),
        ("./.github/workflows/engine-main-touched.yml", "./.github/workflows/other.yml"),
    ]:
        assert call.count(before) == 1
        mutant = original.replace(call, call.replace(before, after, 1), 1)
        with pytest.raises(ValueError, match="native workflow call"):
            check_workflows({**sources, path: mutant})
    with pytest.raises(ValueError, match="native implementation differs"):
        check_workflows({**sources, path: original.replace("  engine-main:" + call, "", 1)})
    dependency = "[admit, workflow-lint, package-files, pytest, differential, test-check, engine-main]"
    assert original.count(dependency) == 1
    with pytest.raises(ValueError, match="result dependencies"):
        check_workflows({
            **sources, path: original.replace(dependency, dependency.replace(", engine-main]", "]"), 1),
        })
    for source_path, before, after in [
        (NATIVE_WORKFLOW, "  push:\n    branches: [main]\n", ""),
        (NATIVE_WORKFLOW, "    branches: [main]", "    branches: [main]\n    paths: ['docs/**']"),
        (NATIVE_WORKFLOW, "permissions: {}",
         "permissions: {}\n\nconcurrency:\n  group: native-main\n  cancel-in-progress: true"),
        (NATIVE_WORKFLOW, "ref: main", "ref: stale-engine"),
        (NATIVE_WORKFLOW, "needs.changes.outputs.native == 'true'", "true"),
        (NATIVE_WORKFLOW, "--expected-plan", "--ignored-plan"),
        (NATIVE_WORKFLOW, "jobs.outcomes.outputs.decision_result", "jobs.changes.result"),
        (NATIVE_WORKFLOW, "jobs.outcomes.outputs.native_result", "jobs.native.result"),
        (NATIVE_WORKFLOW, "steps.forward.outputs.native_result", "'success'"),
        (NATIVE_WORKFLOW, "    if: always()\n    runs-on: ubuntu-24.04\n    permissions: {}",
         "    if: success()\n    runs-on: ubuntu-24.04\n    permissions: {}"),
        (NATIVE_WORKFLOW, 'needs = json.loads(os.environ["NEEDS"])',
         'needs = {"changes": {"result": "success"}, "native": {"result": "success"}}'),
        (NATIVE_WORKFLOW, 'results[job] = row["result"]', 'results[job] = "success"'),
        (NATIVE_DRIVER, "selection differs from the changes job", "ignored identity"),
    ]:
        assert before in sources[source_path]
        with pytest.raises(ValueError, match="native implementation differs"):
            check_workflows({
                **sources, source_path: sources[source_path].replace(before, after, 1),
            })
    for missing in (NATIVE_WORKFLOW, NATIVE_DRIVER):
        with pytest.raises(ValueError, match="native implementation differs"):
            check_workflows({key: value for key, value in sources.items() if key != missing})
    with pytest.raises(ValueError, match="outside the admitted route"):
        check_workflows({
            **sources, ".github/workflows/other.yml":
            "on:\n  workflow_dispatch:\njobs:\n  other:\n"
            "    uses: ./.github/workflows/engine-main-touched.yml\n",
        })


def test_proof_dispatch_requires_both_writers_and_real_public_commits():
    import copy
    import pytest

    env, responses = identity_fixture()
    env["DEFINITION_SHA"] = "d" * 40
    responses[f"repos/{env['REPO']}"] = {"private": False}
    for key in ("BASE_SHA", "HEAD_SHA"):
        responses[f"repos/{env['REPO']}/commits/{env[key]}"] = {"sha": env[key]}
    authorize_proof(env, lambda endpoint: responses[endpoint])
    for identity in ("ACTOR", "TRIGGERING_ACTOR"):
        changed = copy.deepcopy(responses)
        changed[f"repos/{env['REPO']}/collaborators/{env[identity]}/permission"] = {
            "permission": "read",
        }
        with pytest.raises(ValueError, match="write permission"):
            authorize_proof(env, lambda endpoint: changed[endpoint])
    for endpoint, response, message in [
        (f"repos/{env['REPO']}", {"private": True}, "not public"),
        (f"repos/{env['REPO']}/commits/{env['HEAD_SHA']}", {"sha": "c" * 40}, "differs"),
    ]:
        changed = {**responses, endpoint: response}
        with pytest.raises(ValueError, match=message):
            authorize_proof(env, lambda endpoint: changed[endpoint])
    with pytest.raises(ValueError, match="SHA"):
        authorize_proof({**env, "BASE_SHA": "main"}, lambda endpoint: responses[endpoint])


def test_admission_refuses_the_closed_promotion_target_trigger():
    import pytest

    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root, workflow_paths(root))
    path = ".github/workflows/promote-baseline.yml"
    original = sources[path]
    # Replace the event block so this is valid YAML even on the old base,
    # which already contains the closed target trigger being retired.
    mutant, count = re.subn(
        r"^on:\n.*?(?=^[^\s#])",
        "on:\n  pull_request_target:\n    types: [closed]\n\n",
        original, count=1, flags=re.M | re.S,
    )
    assert count == 1, "mutation must replace the actual promotion event block"
    with pytest.raises(ValueError, match="target trigger outside admission"):
        check_workflows({**sources, path: mutant})


def test_test_workflow_pull_request_trigger_mutation_is_refused():
    import pytest

    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root, workflow_paths(root))
    path = ".github/workflows/test.yml"
    original = sources[path]
    marker = "  workflow_dispatch:\n"
    assert original.count(marker) == 1
    mutant = original.replace(marker, "  pull_request:\n" + marker, 1)
    assert mutant != original
    with pytest.raises(ValueError, match="test.yml must not have a pull_request trigger"):
        check_workflows({**sources, path: mutant})


def test_test_workflow_hosted_runner_admission_and_mutations():
    import pytest

    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root, workflow_paths(root))
    path = ".github/workflows/test.yml"

    def with_runner(workflow, name, runner):
        body = jobs(workflow)[name]
        original = f"    runs-on: {value_at(body, 4, 'runs-on')}\n"
        assert body.count(original) == 1
        assert workflow.count(body) == 1
        changed = body.replace(original, f"    runs-on: {runner}\n", 1)
        return workflow.replace(body, changed, 1)

    candidate = sources[path]
    assert {name: value_at(body, 4, "runs-on") for name, body in jobs(candidate).items()} == {
        "authz": "ubuntu-24.04",
        "changes": "ubuntu-24.04",
        "workflow-lint": "ubuntu-24.04",
        "package-files": "ubuntu-24.04",
        "pytest": "ubuntu-24.04",
        "test-check": "ubuntu-24.04",
    }
    check_workflows({**sources, path: candidate})

    for name, runner in (
        ("authz", "gke-8cpu-16gb-amd64"),
        ("changes", "gke-8cpu-16gb-amd64"),
        ("test-check", "gke-8cpu-16gb-amd64"),
        ("workflow-lint", "gke-8cpu-16gb-amd64"),
        ("package-files", "gke-8cpu-16gb-amd64"),
        ("pytest", "gke-8cpu-16gb-amd64"),
        ("pytest", "gke-4cpu-8gb-amd64"),
    ):
        mutant = with_runner(candidate, name, runner)
        assert mutant != candidate
        with pytest.raises(ValueError) as error:
            check_workflows({**sources, path: mutant})
        assert str(error.value) == f"test.yml {name}: runner differs"

    marker = "  workflow_dispatch:\n"
    assert candidate.count(marker) == 1
    pr_workflow = candidate.replace(marker, "  pull_request:\n" + marker, 1)
    for name in ("authz", "changes", "workflow-lint", "package-files", "pytest", "test-check"):
        mutant = with_runner(pr_workflow, name, "gke-8cpu-16gb-amd64")
        assert mutant != pr_workflow
        with pytest.raises(ValueError) as error:
            check_workflows({**sources, path: mutant})
        assert str(error.value) == "test.yml must not have a pull_request trigger"


def test_workflow_lint_guard_cannot_be_removed_or_pointed_at_candidate():
    import pytest

    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root, workflow_paths(root))
    original = sources[".github/workflows/test.yml"]
    body = jobs(original)["workflow-lint"]
    for before, after, message in (
        ('ref: "${{ github.event.pull_request.head.sha || github.sha }}"',
         'ref: "${{ github.event.pull_request.base.sha || github.sha }}"', "exact head"),
        ("    needs: [authz]\n", "    needs: []\n", "authorized workflow lint"),
        ("  workflow-lint:\n", "  workflow-lint:\n    if: false\n", "unconditional"),
        ("python3 scripts/check_workflow_app_environments.py", "echo skipped", "App environment"),
        ("-config-file .github/actionlint.yaml", '-config-file "$lint_config"',
         "repository actionlint config"),
    ):
        target = original if before.startswith("  workflow-lint:") else body
        assert target.count(before) == 1
        changed = target.replace(before, after, 1)
        mutant = changed if target is original else original.replace(body, changed, 1)
        with pytest.raises(ValueError, match=message):
            check_workflows({**sources, ".github/workflows/test.yml": mutant})


def test_path_reader_refuses_links_and_credentials_before_open(tmp_path, capsys):
    import pytest

    for relative in (
        ".ssh/key", ".aws/config", ".codex/auth.json",
        ".config/zsh/keys/key", ".config/pineforge/config", "foo.env",
    ):
        with pytest.raises(ValueError, match="protected"):
            read_sources(tmp_path, [relative])
    target = tmp_path / "unopened"
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        read_sources(tmp_path, ["link"])
    assert not target.exists()
    assert "Files to read:" in capsys.readouterr().out


def test_lint_lists_local_action_inputs_and_refuses_linked_actions(tmp_path, capsys):
    import pytest

    (tmp_path / ".github/workflows").mkdir(parents=True)
    directory = tmp_path / ".github/actions/nested"
    directory.mkdir(parents=True)
    action = directory / "action.yml"
    action.write_text("name: data\n", encoding="utf-8")
    assert ".github/actions/nested/action.yml" in workflow_paths(tmp_path)
    # Listing must name the action before any later actionlint invocation.
    assert ".github/actions/nested/action.yml" in capsys.readouterr().out
    (directory / "linked").symlink_to(tmp_path / "unopened")
    with pytest.raises(ValueError, match="symlink"):
        workflow_paths(tmp_path)


def test_admission_cli_crash_is_nonzero(tmp_path):
    # Deliberately missing REPO crashes the real entry point before any API or
    # file read. Inspect the child's exit and traceback, not a wrapper marker.
    script = safe_path(Path(__file__).absolute())
    print("Files to read: " + json.dumps([str(script)]), flush=True)
    result = subprocess.run(
        [sys.executable, "-I", str(script), "--identity"],
        cwd=tmp_path, env={}, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "KeyError: 'REPO'" in result.stderr


def test_result_refuses_every_incomplete_leg_and_stale_head(monkeypatch):
    import copy
    import pytest

    root = Path(__file__).resolve().parents[1]
    original_read = read_sources
    installed = bootstrap_fixture(original_read(root, workflow_paths(root)), native=True)
    monkeypatch.setattr(sys.modules[__name__], "read_sources",
                        lambda root, paths: ({".github/workflows/gke-pr.yml":
                                             installed[".github/workflows/gke-pr.yml"]}
                                            if paths == [".github/workflows/gke-pr.yml"]
                                            else original_read(root, paths)))
    env, responses = identity_fixture()
    env.update(GITHUB_SERVER_URL="https://github.com", GITHUB_RUN_ID="1", DEFINITION_SHA="d" * 40)
    expected = {"admit", "workflow-lint", "package-files", "pytest", "differential", "test-check",
                "engine-main"}
    needs = {name: {"result": "success"} for name in expected}
    needs["admit"]["outputs"] = {
        "allowed": "true", "head_sha": env["HEAD_SHA"], "base_sha": env["BASE_SHA"],
    }
    needs["engine-main"]["outputs"] = {
        "source_event": "pull_request",
        "decision_result": "success", "native_result": "success", "native": "true",
        "mode": "touched", "execution_mode": "touched", "inventory": "a" * 64,
        "coverage_complete": "true",
        "base": env["BASE_SHA"], "head": env["HEAD_SHA"], "definition": env["DEFINITION_SHA"],
        "tree": "e" * 40, "plan": "f" * 64, "executed_plan": "f" * 64,
    }
    calls = []
    # Only the external status transport is replaced. The actual result and
    # live identity decisions execute, and their full outgoing payload is read.
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    env["NEEDS"] = json.dumps(needs)
    report_result(env, lambda endpoint: responses[endpoint])
    assert "state=success" in calls.pop()
    for name in expected:
        for result in ("failure", "cancelled", "skipped", None):
            changed = copy.deepcopy(needs)
            changed[name]["result"] = result
            env["NEEDS"] = json.dumps(changed)
            with pytest.raises(ValueError, match="PR admission or checks failed"):
                report_result(env, lambda endpoint: responses[endpoint])
            posted = calls.pop()
            assert "state=failure" in posted
            assert f"repos/owner/project/statuses/{env['HEAD_SHA']}" in posted
        env["NEEDS"] = json.dumps({key: row for key, row in needs.items() if key != name})
        with pytest.raises(ValueError, match="PR admission or checks failed"):
            report_result(env, lambda endpoint: responses[endpoint])
        assert "state=failure" in calls.pop()
    for output in ("allowed", "head_sha", "base_sha"):
        changed = copy.deepcopy(needs)
        changed["admit"]["outputs"][output] = "wrong"
        env["NEEDS"] = json.dumps(changed)
        with pytest.raises(ValueError, match="PR admission or checks failed"):
            report_result(env, lambda endpoint: responses[endpoint])
        assert "state=failure" in calls.pop()
    for output, value in [
        ("source_event", "push_main"),
        ("decision_result", "failure"), ("decision_result", "cancelled"),
        ("decision_result", "skipped"), ("native_result", "failure"),
        ("native_result", "cancelled"), ("native_result", "skipped"),
        ("native_result", ""), ("native", "false"), ("native", "maybe"),
        ("base", "c" * 40), ("head", "c" * 40), ("definition", "c" * 40),
        ("tree", ""), ("plan", ""), ("executed_plan", "a" * 64),
        ("executed_plan", None),
        ("mode", "full"), ("execution_mode", "full"), ("inventory", ""),
        ("coverage_complete", "false"),
    ]:
        changed = copy.deepcopy(needs)
        changed["engine-main"]["outputs"][output] = value
        env["NEEDS"] = json.dumps(changed)
        with pytest.raises(ValueError, match="PR admission or checks failed"):
            report_result(env, lambda endpoint: responses[endpoint])
        assert "state=failure" in calls.pop()
    docs = copy.deepcopy(needs)
    docs["engine-main"]["outputs"].update(
        native="false", native_result="skipped", executed_plan="",
        mode="skip", execution_mode="", inventory="", coverage_complete="",
    )
    env["NEEDS"] = json.dumps(docs)
    report_result(env, lambda endpoint: responses[endpoint])
    assert "state=success" in calls.pop()
    # An intended native skip still needs a successful, identity-bound decision.
    for output, value in [
        ("source_event", "push_main"),
        ("decision_result", "skipped"), ("head", "c" * 40),
        ("base", "c" * 40), ("definition", "c" * 40),
        ("native_result", "success"), ("executed_plan", "f" * 64),
        ("mode", "full"), ("execution_mode", "touched"), ("inventory", "a" * 64),
    ]:
        changed = copy.deepcopy(docs)
        changed["engine-main"]["outputs"][output] = value
        env["NEEDS"] = json.dumps(changed)
        with pytest.raises(ValueError, match="PR admission or checks failed"):
            report_result(env, lambda endpoint: responses[endpoint])
        assert "state=failure" in calls.pop()
    for malformed in ("{", "null", "[]", '{"engine-main": null}'):
        env["NEEDS"] = malformed
        with pytest.raises(ValueError, match="PR admission or checks failed"):
            report_result(env, lambda endpoint: responses[endpoint])
        assert "state=failure" in calls.pop()
    env["NEEDS"] = json.dumps(needs)
    responses["repos/owner/project/pulls/7"]["head"]["sha"] = "c" * 40
    with pytest.raises(ValueError, match="PR admission or checks failed"):
        report_result(env, lambda endpoint: responses[endpoint])
    assert "state=failure" in calls.pop()


def test_push_admission_binds_before_after_and_never_queries_a_later_main(monkeypatch):
    import pytest

    env, responses = identity_fixture()
    env.update(
        EVENT="push", REF="refs/heads/main", DELETED="false",
        EVENT_SHA=env["HEAD_SHA"], DEFINITION_SHA=env["HEAD_SHA"],
    )
    calls = []

    def api(endpoint):
        calls.append(endpoint)
        return responses[endpoint]

    monkeypatch.setitem(globals(), "gh_json", api)
    authorize_push(env)
    assert calls == []
    authorize_push({**env, "BASE_SHA": "0" * 40})
    authorize_push({**env, "ACTOR": "github-actions[bot]"})
    assert calls == []
    for changes in [
        {"EVENT": "workflow_dispatch"}, {"REF": "refs/heads/topic"},
        {"DELETED": "true"}, {"BASE_SHA": "main"},
        {"HEAD_SHA": "c" * 40}, {"EVENT_SHA": "c" * 40},
        {"DEFINITION_SHA": "c" * 40}, {"HEAD_SHA": "0" * 40},
    ]:
        with pytest.raises(ValueError):
            authorize_push({**env, **changes})


def native_publication_fixture():
    env, responses = identity_fixture()
    env.update(
        EVENT="workflow_dispatch", REF="refs/heads/proof", SOURCE_EVENT="push_main",
        DEFINITION_SHA="d" * 40, RUN_ID="1", RUN_ATTEMPT="2", SERVER_URL="https://github.com",
    )
    responses[f"repos/{env['REPO']}"] = {"private": False}
    for key in ("BASE_SHA", "HEAD_SHA"):
        responses[f"repos/{env['REPO']}/commits/{env[key]}"] = {"sha": env[key]}
    prefix = f"repos/{env['REPO']}/actions/runs/1/attempts/2"
    responses[prefix] = {
        "id": 1, "run_attempt": 2, "event": "workflow_dispatch",
        "head_sha": env["DEFINITION_SHA"], "head_branch": "proof", "path": NATIVE_WORKFLOW,
        "repository": {"full_name": env["REPO"]},
    }
    job = {
        "id": 3, "run_id": 1, "head_sha": env["DEFINITION_SHA"], "status": "completed",
        "name": "Engine main native", "conclusion": "success",
        "html_url": f"https://github.com/{env['REPO']}/actions/runs/1/job/3",
    }
    responses[f"{prefix}/jobs?per_page=100&page=1"] = {"total_count": 1, "jobs": [job]}
    needs = {
        "changes": {"result": "success", "outputs": {
            "native": "true", "source_event": "push_main", "base": env["BASE_SHA"],
            "mode": "full",
            "head": env["HEAD_SHA"], "definition": env["DEFINITION_SHA"],
            "tree": "e" * 40, "plan": "f" * 64,
        }},
        "native": {"result": "success", "outputs": {
            "completed_plan": "f" * 64, "executed_plan": "f" * 64,
            "pytest_exit": "0", "audit_errors": "0",
            "execution_mode": "full", "inventory": "a" * 64, "coverage_complete": "true",
            "full_success": "true", "full_executed_plan": "f" * 64,
            "engine_sha": "b" * 40, "engine_tree": "c" * 40,
            "construction_false": "passed", "construction_true": "passed",
        }},
    }
    return env, responses, needs, job


def test_historical_check_uses_source_sha_actual_job_and_observed_outcomes():
    env, responses, needs, job = native_publication_fixture()
    posted = []

    def create(repo, payload):
        assert repo == env["REPO"]
        posted.append(payload)
        responses[f"repos/{repo}/check-runs/9"] = {**payload, "id": 9}
        return {"id": 9}

    for state, outputs, conclusion in [
        ("success", needs["native"]["outputs"].copy(), "success"),
        ("failure", {
            "completed_plan": "f" * 64, "pytest_exit": "1", "audit_errors": "0",
            "execution_mode": "full", "inventory": "a" * 64, "coverage_complete": "true",
            "full_success": "false", "engine_sha": "b" * 40, "engine_tree": "c" * 40,
            "construction_false": "failed", "construction_true": "failed",
        }, "failure"),
        ("failure", {}, "action_required"),
        ("cancelled", {}, "cancelled"),
    ]:
        needs["native"].update(result=state, outputs=outputs)
        job["conclusion"] = state
        env["NEEDS"] = json.dumps(needs)
        check = report_native_check(env, lambda endpoint: responses[endpoint], create)
        assert check["head_sha"] == env["HEAD_SHA"] != env["DEFINITION_SHA"]
        assert check["conclusion"] == conclusion and check["details_url"] == job["html_url"]
        assert posted[-1]["external_id"] == "native:1:2:3"
        assert "context" not in posted[-1] and posted[-1]["name"] != "gate"


def test_historical_check_refuses_missing_or_wrong_run_source_execution_binding():
    import copy
    import pytest

    for mutation in (
        "missing-native", "wrong-source", "wrong-scope", "wrong-plan", "no-execution",
        "skip", "wrong-definition", "wrong-attempt", "not-completed", "wrong-details",
        "wrong-job-head", "wrong-job-result", "missing-job",
    ):
        env, responses, needs, job = native_publication_fixture()
        prefix = f"repos/{env['REPO']}/actions/runs/1/attempts/2"
        if mutation == "missing-native":
            del needs["native"]
        elif mutation == "wrong-source":
            needs["changes"]["outputs"]["head"] = env["DEFINITION_SHA"]
        elif mutation == "wrong-scope":
            needs["changes"]["outputs"]["source_event"] = "pull_request"
        elif mutation == "wrong-plan":
            needs["native"]["outputs"]["completed_plan"] = "a" * 64
        elif mutation == "no-execution":
            needs["native"]["outputs"] = {}
        elif mutation == "skip":
            needs["native"]["result"] = job["conclusion"] = "skipped"
        elif mutation == "wrong-definition":
            responses[prefix]["head_sha"] = env["HEAD_SHA"]
        elif mutation == "wrong-attempt":
            responses[prefix]["run_attempt"] = 3
        elif mutation == "not-completed":
            job["status"] = "in_progress"
        elif mutation == "wrong-details":
            job["html_url"] = "https://github.com/other/run"
        elif mutation == "wrong-job-head":
            job["head_sha"] = env["HEAD_SHA"]
        elif mutation == "wrong-job-result":
            job["conclusion"] = "failure"
        else:
            responses[f"{prefix}/jobs?per_page=100&page=1"] = {"total_count": 0, "jobs": []}
        env["NEEDS"] = json.dumps(needs)
        creates = []
        with pytest.raises(ValueError):
            report_native_check(env, lambda endpoint: responses[endpoint],
                                lambda repo, payload: creates.append(payload))
        assert creates == [], mutation
    env, responses, needs, job = native_publication_fixture()
    env["NEEDS"] = json.dumps(needs)

    def wrong_association(repo, payload):
        responses[f"repos/{repo}/check-runs/9"] = {
            **copy.deepcopy(payload), "id": 9, "head_sha": env["DEFINITION_SHA"],
        }
        return {"id": 9}

    with pytest.raises(ValueError, match="does not bind"):
        report_native_check(env, lambda endpoint: responses[endpoint], wrong_association)


def test_main_publication_requires_full_completion_beyond_passing_construction():
    import pytest

    for key, value in [
        ("execution_mode", "touched"), ("inventory", ""),
        ("coverage_complete", "false"), ("audit_errors", "1"),
        ("full_success", "false"), ("full_executed_plan", ""),
        ("full_executed_plan", "b" * 64), ("engine_sha", ""),
    ]:
        env, responses, needs, job = native_publication_fixture()
        needs["native"]["outputs"][key] = value
        env["NEEDS"] = json.dumps(needs)
        creates = []
        with pytest.raises(ValueError, match="lacks bound ordinary execution"):
            report_native_check(env, lambda endpoint: responses[endpoint],
                                lambda repo, payload: creates.append(payload))
        assert creates == [], key


def test_test_check_executes_real_workflow_script(tmp_path):
    import pytest

    root = Path(__file__).resolve().parents[1]
    workflow = read_sources(root, [".github/workflows/gke-pr.yml"])[".github/workflows/gke-pr.yml"]
    scripts = runs(jobs(workflow)["test-check"])
    assert len(scripts) == 1
    expected = {"admit", "workflow-lint", "package-files", "pytest"}
    needs = {name: {"result": "success"} for name in expected}
    needs["admit"]["outputs"] = {"allowed": "true"}

    def run_case(script, rows, expected_exit):
        result = subprocess.run(
            [sys.executable, "-I", "-c", script], cwd=tmp_path,
            env={"NEEDS": json.dumps(rows)}, capture_output=True, text=True,
        )
        assert result.returncode == expected_exit, result.stderr
        assert result.stderr == ""
        assert result.stdout == ("" if expected_exit == 0 else
                                 "::error::PR admission or a test leg did not succeed\n")

    run_case(scripts[0], needs, 0)
    for name in expected:
        for verdict in ("failure", "skipped", "cancelled"):
            changed = {key: {**row} for key, row in needs.items()}
            changed[name]["result"] = verdict
            run_case(scripts[0], changed, 1)
    changed = {key: row for key, row in needs.items() if key != "package-files"}
    run_case(scripts[0], changed, 1)
    with pytest.raises(AssertionError):
        run_case("pass", changed, 1)


def bootstrap_fixture(sources, *, native):
    """Temporary controls use both real canonical shapes, with exact deltas."""
    result = dict(sources)
    root = Path(__file__).resolve().parents[1]
    driver_path = ("tests/fixtures/ci_engine_main_touched_approved.txt" if native else NATIVE_DRIVER)
    driver_file = safe_path(root / driver_path)
    print("Files to read: " + json.dumps([str(driver_file)]), flush=True)
    driver = workflow_text(driver_file.read_bytes())
    if native:
        assert hashlib.sha256(driver.encode("utf-8")).hexdigest() == APPROVED_NATIVE_DRIVER_SHA256
    result[NATIVE_DRIVER] = driver
    path = ".github/workflows/gke-pr.yml"
    text = result[path]
    body = jobs(text).get("engine-main")
    if body is not None:
        text = text.replace("  engine-main:" + body, "", 1)
    new_needs = "[admit, workflow-lint, package-files, pytest, differential, test-check, engine-main]"
    old_needs = "[admit, workflow-lint, package-files, pytest, differential, test-check]"
    text = text.replace(new_needs, old_needs, 1)
    definition = "          DEFINITION_SHA: ${{ github.sha }}\n"
    text = text.replace(definition, "", 1)
    if native:
        text = text.replace("  test-check:\n", "  engine-main:\n" + NATIVE_CALL + "\n  test-check:\n", 1)
        text = text.replace(old_needs, new_needs, 1)
        # Only the result step has this exact NEEDS line in block env.
        marker = "          NEEDS: ${{ toJSON(needs) }}\n          REPO:"
        require(text.count(marker) == 1, "bootstrap result fixture binding differs")
        text = text.replace(marker, "          NEEDS: ${{ toJSON(needs) }}\n"
                            + definition + "          REPO:", 1)
    result[path] = text
    push = "  push:\n    branches: [main]\n"
    callee = result[NATIVE_WORKFLOW].replace(push, "", 1)
    result[NATIVE_WORKFLOW] = callee.replace("\non:\n", "\non:\n" + push, 1) if native else callee
    return result


def test_bootstrap_accepts_only_complete_graph_and_push_activation_pairs():
    import pytest

    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root, workflow_paths(root))
    old = bootstrap_fixture(sources, native=False)
    new = bootstrap_fixture(sources, native=True)
    check_workflows(old)
    check_workflows(new)
    for caller, callee in ((old, new), (new, old)):
        with pytest.raises(ValueError, match="native implementation differs"):
            check_workflows({**caller, NATIVE_WORKFLOW: callee[NATIVE_WORKFLOW]})
        with pytest.raises(ValueError, match="native implementation differs"):
            check_workflows({**caller, NATIVE_DRIVER: callee[NATIVE_DRIVER]})
    for native_sources in (old, new):
        for path, before, after in (
            (NATIVE_WORKFLOW, "ref: main", "ref: stale-engine"),
            (NATIVE_DRIVER, "selection differs from the changes job", "ignored identity"),
        ):
            with pytest.raises(ValueError, match="native implementation differs"):
                check_workflows({**native_sources,
                                 path: native_sources[path].replace(before, after, 1)})


def test_native_driver_pin_uses_complete_bytes_against_the_real_base_policy():
    import pytest

    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root, workflow_paths(root))
    old = bootstrap_fixture(sources, native=False)
    new = bootstrap_fixture(sources, native=True)
    installed_driver = old[NATIVE_DRIVER]
    approved_driver = new[NATIVE_DRIVER]
    assert installed_driver != approved_driver
    baseline = subprocess.run(
        ["git", "show",
         "e6dc2c01cc719bcd88b4416a085f5a07873da2fd:tests/test_gke_pr_admission.py"],
        cwd=root, check=True, capture_output=True,
    )
    # Execute the genuine pinned policy, never the approved driver fixture.
    # Both policy and candidate contexts resolve the real installed base files.
    policy = {"__name__": "baseline_gke_pr_admission", "__file__": __file__}
    exec(compile(workflow_text(baseline.stdout), __file__, "exec"), policy)
    for checker, admitted_driver, refused_driver in (
        (policy["check_workflows"], installed_driver, approved_driver),
        (check_workflows, approved_driver, installed_driver),
    ):
        checker(old)
        checker({**new, NATIVE_DRIVER: admitted_driver})
        with pytest.raises(ValueError, match="native implementation differs"):
            checker({**new, NATIVE_DRIVER: refused_driver})
        with pytest.raises(ValueError, match="native implementation differs"):
            checker({**old, NATIVE_DRIVER: approved_driver})
        for graph in (old, new):
            with pytest.raises(ValueError, match="native implementation differs"):
                checker({path: text for path, text in graph.items() if path != NATIVE_DRIVER})

        semantic_guard = 'if args.expected_plan != selection["plan_sha256"]:'
        assert approved_driver.count(semantic_guard) == 1
        assert approved_driver.endswith("\n")
        for mutant in (
            approved_driver[:-1],
            approved_driver.replace(semantic_guard, semantic_guard.replace("!=", "=="), 1),
            approved_driver + "# changed driver comment\n",
            approved_driver + " ",
            approved_driver.replace("\n", "\r\n"),
            "raise SystemExit(0)\n",
        ):
            assert mutant != approved_driver
            with pytest.raises(ValueError, match="native implementation differs"):
                checker({**new, NATIVE_DRIVER: mutant})

    # Invalid direct-call types must retain the native-implementation refusal,
    # rather than failing in size accounting or treating metadata as authority.
    for graph in (old, new):
        for invalid in (
            None, approved_driver.encode("utf-8"), 0,
            {"sha256": APPROVED_NATIVE_DRIVER_SHA256},
        ):
            with pytest.raises(ValueError, match="native implementation differs"):
                check_workflows({**graph, NATIVE_DRIVER: invalid})


def test_bootstrap_reporter_keeps_old_results_only_for_installed_old_caller(monkeypatch):
    import pytest

    root = Path(__file__).resolve().parents[1]
    original_read = read_sources
    sources = original_read(root, workflow_paths(root))
    env, responses = identity_fixture()
    env.update(GITHUB_SERVER_URL="https://github.com", GITHUB_RUN_ID="1")
    expected = {"admit", "workflow-lint", "package-files", "pytest", "differential", "test-check"}
    needs = {name: {"result": "success"} for name in expected}
    needs["admit"]["outputs"] = {
        "allowed": "true", "head_sha": env["HEAD_SHA"], "base_sha": env["BASE_SHA"],
    }
    env["NEEDS"] = json.dumps(needs)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    for native in (False, True):
        fixture = bootstrap_fixture(sources, native=native)
        monkeypatch.setattr(sys.modules[__name__], "read_sources",
                            lambda root, paths: {".github/workflows/gke-pr.yml":
                                                 fixture[".github/workflows/gke-pr.yml"]})
        if native:
            with pytest.raises(ValueError, match="PR admission or checks failed"):
                report_result(env, lambda endpoint: responses[endpoint])
            assert "state=failure" in calls.pop()
        else:
            report_result(env, lambda endpoint: responses[endpoint])
            assert "state=success" in calls.pop()


if __name__ == "__main__":
    sys.exit(main())
