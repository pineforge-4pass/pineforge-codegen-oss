# @tunaxis/codegen-pyodide

formerly @pineforge/codegen-pyodide

Gate-validated Pyodide payload for the PineScript v6 → C++ transpiler. Install it with:

```sh
npm install @tunaxis/codegen-pyodide
```

Built and published from `pineforge-codegen-oss` by `.github/workflows/publish-pyodide.yml`,
with the same version as the matching codegen release on PyPI. Stable releases are on the
`latest` dist-tag. A prerelease (for example `1.0.0-rc.1`) is published on `next` only.

## Contents

- `tunaxis_codegen-<version>.tar.gz` — the gate-validated archive (unpack into Pyodide).
- `tunaxis_codegen/` — unpacked Python source (put on `PYTHONPATH` for Node oracle/grammar tooling).
- `pineforge_codegen/` — deprecated import alias for the unpacked source.
- `tables.json` — introspected codegen tables (Tunaxis's web app renders its tables from this).
- `release.json` — `{ codegen, pyodide, python, emscripten, sha256 }`.
- `glue.py` — the `transpile_json(source)` glue from the repository's `gate/glue.py`; its JSON
  envelopes are described in the repository's `docs/PUBLIC_CONTRACT.md`.
- `transpile.worker.mjs` — an ES module worker that runs the glue in Pyodide.
- `index.mjs` — exports `release`, `tables`, `archivePath`, `sourceRoot`, `codegenSourceDir`,
  `workerPath` and `glue`.
- `tunaxis_codegen/diagnostics_catalog.json` — the diagnostics catalog (since 1.2.0), also
  importable as `@tunaxis/codegen-pyodide/diagnostics_catalog.json`; the repository's
  `docs/PUBLIC_CONTRACT.md` describes it.
- `LICENSE` — the source license, copied from the repository at package build time.

## Publishing (maintainers)

A `v*` tag push, which the release workflow makes, publishes through npm OIDC Trusted
Publishing after the dependency audit and the full parity gate pass; published versions
carry npm provenance. A manual `workflow_dispatch` defaults to a dry run; `dry_run=false`
publishes for real.
