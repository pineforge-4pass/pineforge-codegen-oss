# @pineforge/codegen-pyodide

Gate-validated Pyodide payload for the PineScript v6 → C++ transpiler. Built and
published from `pineforge-codegen-oss` by `.github/workflows/publish-pyodide.yml`.

## Contents
- `pineforge_codegen-<version>.tar.gz` — the gate-validated archive (unpack into Pyodide).
- `pineforge_codegen/` — unpacked Python source (put on `PYTHONPATH` for Node oracle/grammar tooling).
- `tables.json` — introspected codegen tables (the app renders `tables.generated.ts` from this).
- `release.json` — `{ codegen, pyodide, python, emscripten, sha256 }`.
- `index.mjs` — `release`, `tables`, `archivePath`, `codegenSourceDir`.

## One-time bootstrap (maintainer, manual)
This is a NEW package; npm OIDC Trusted Publishing can only be configured after
the package exists:
1. Build locally: `npm run gate:full && node scripts/build-npm-package.mjs`.
2. From `npm/`, do the first publish with a granular npm token:
   `npm publish --access=public` (one time).
3. On npmjs.com, configure the package's Trusted Publisher: GitHub Actions,
   repo `pineforge-4pass/pineforge-codegen-oss`, workflow `publish-pyodide.yml`.
4. Thereafter releases publish via OIDC (no token): run `publish-pyodide.yml`
   via **workflow_dispatch with `dry_run=false`**. NOTE: tag pushes currently run
   a DRY-RUN only (a safe validation) — the sole real-publish path is dispatch +
   `dry_run=false`. To switch to tag-driven releases later, change the publish
   condition in the workflow.
