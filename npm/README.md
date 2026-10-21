# @pineforge/codegen-pyodide

Gate-validated Pyodide payload for the PineScript v6 → C++ transpiler. Built and
published from `pineforge-codegen-oss` by `.github/workflows/publish-pyodide.yml`,
with the same version as the matching `pineforge-codegen` release on PyPI.
Stable releases are on the `latest` dist-tag. A prerelease (for example
`1.0.0-rc.1`) is published on `next` only.

## Contents
- `pineforge_codegen-<version>.tar.gz` — the gate-validated archive (unpack into Pyodide).
- `pineforge_codegen/` — unpacked Python source (put on `PYTHONPATH` for Node oracle/grammar tooling).
- `tables.json` — introspected codegen tables (PineForge's web app renders its tables from this).
- `release.json` — `{ codegen, pyodide, python, emscripten, sha256 }`.
- `glue.py` — the `transpile_json(source)` glue from the repository's `gate/glue.py`; its JSON
  envelopes are described in the repository's `docs/PUBLIC_CONTRACT.md`.
- `transpile.worker.mjs` — an ES module worker that runs the glue in Pyodide.
- `index.mjs` — exports `release`, `tables`, `archivePath`, `sourceRoot`, `codegenSourceDir`,
  `workerPath` and `glue`.
- `pineforge_codegen/diagnostics_catalog.json` — the diagnostics catalog (since 1.2.0), also
  importable as `@pineforge/codegen-pyodide/diagnostics_catalog.json`; the repository's
  `docs/PUBLIC_CONTRACT.md` describes it.
- `LICENSE` — the PineForge Source License (since 1.2.0), which `package.json` names:
  version 1.1 in release 1.2.0, version 1.2 from release 1.3.0 on.

## Publishing (maintainers)

After the release tag exists on main and the release job is green, the operator
doing the cut, using a GitHub account with write access, dispatches publication:

```sh
gh workflow run publish-pyodide.yml --repo pineforge-4pass/pineforge-codegen-oss --ref main -f tag=<tag> -f dry_run=false
```

Use the existing release tag in place of `<tag>`; every run, including a
placeholder claim, requires it. Set `dry_run=true` to validate first. A tag push
alone publishes nothing. A green `release.yml` run does not mean npm published;
watch the `publish-pyodide.yml` run.
The guard and job conditions live in the workflow file on `main`, but GitHub
executes the file from the dispatched ref, so they alone cannot stop a user
with write access who pushes and dispatches a modified workflow on another
branch; a server-side bound requires an admin to configure a GitHub environment
with a `main`-only deployment-branch policy and bind the npm trusted publisher
to require it, outside this change.
Publication uses npm OIDC Trusted Publishing
after the dependency audit and the full parity gate pass; published versions
carry npm provenance. A manual `workflow_dispatch` defaults to a dry run; `dry_run=false`
publishes for real. The one-time bootstrap is done: 0.7.0 was the manual first publish,
and every version since 0.7.1 carries provenance from this workflow.
