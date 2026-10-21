<img alt="Tunaxis" src="https://tunaxis.com/brand/v1/lockup-light.png" height="40">

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
- `tunaxis_codegen/diagnostics_catalog.json` — the diagnostics catalog, also
  importable as `@tunaxis/codegen-pyodide/diagnostics_catalog.json`; the repository's
  `docs/PUBLIC_CONTRACT.md` describes it.
- `LICENSE` — the source license, copied from the repository at package build time.

## Publishing (maintainers)

A `v*` tag push, which the release workflow makes, publishes through npm OIDC Trusted
Publishing after the dependency audit and the full parity gate pass; published versions
carry npm provenance. A manual `workflow_dispatch` defaults to a dry run; `dry_run=false`
publishes for real.

Both publishing jobs test the assembled payload and rendered forwarder before
publishing. Reruns skip each package version that already exists, so they can
finish a partial release.

## Compatibility forwarder

`@pineforge/codegen-pyodide` depends on exactly the matching
`@tunaxis/codegen-pyodide` version and forwards the module's exports. Its `tables.json`,
`release.json` and `diagnostics_catalog.json` subpaths remain real JSON files copied
from that same payload, supporting both `require()` and ESM imports with
`with { type: "json" }`.

After the first forwarder release, the owner runs this once by hand:

```sh
npm deprecate @pineforge/codegen-pyodide "Renamed to @tunaxis/codegen-pyodide; install @tunaxis/codegen-pyodide instead."
```

Omitting a version deprecates all versions of the old name. npm OIDC authenticates
publishing only, so the workflow does not run this command. Deprecate the `0.0.x`
`@tunaxis/codegen-pyodide` placeholders by hand as well; a first real release on
`next` leaves `latest` pointing to its placeholder.

## First-publish bootstrap

Name claims use the token-free `claim-npm` job with `claim_placeholder=true` and an
optional `claim_version` of `0.0.N`. The token bootstrap applies only to
`first_publish=true`.

1. Configure the `@tunaxis/codegen-pyodide` trusted publisher for this repository and
   `publish-pyodide.yml` with **no environment**, since the `publish` job declares
   none. Keep the separate `@pineforge/codegen-pyodide` trusted publisher configured.
2. For the token bootstrap, create `NPM_FIRST_PUBLISH` and the `npm-first` environment
   with a required reviewer and a deployment policy allowing only `v*` tags.
3. Dispatch the workflow on the new `v<VERSION>` tag with `first_publish=true`,
   `dry_run=false` and `publish_old_name=true`. The tag must equal `VERSION`; do not
   dispatch at a `VERSION` already published under `@pineforge/codegen-pyodide`.
4. Delete the bootstrap secret and `npm-first` environment afterwards, then perform
   the manual deprecations above.
