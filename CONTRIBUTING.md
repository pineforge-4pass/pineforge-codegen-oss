# Contributing to pineforge-codegen

This is the source-available PineScript v6 to C++ translator. Read the
[license and contribution terms](LEGAL.md) before sending a material change.
Contributions use the Developer Certificate of Origin: add a `Signed-off-by`
line with `git commit -s`. Material contributions also require a Contributor
License Agreement so PineForge can include them in its commercial license;
contact luis@4pass.com.tw before opening a material pull request.

## Local setup

Use Python 3.11 or newer, Node 22, a C++17 compiler, CMake, and a matching
[`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine)
checkout. Clone codegen and engine as siblings, then install the development
dependencies from this repository:

```bash
python -m pip install -e '.[dev]'
npm ci
```

Build the paired engine outside its source checkout if you need compile and
runtime tests. This example enables the source layer and limits the build to
four jobs:

```bash
cmake -S ../pineforge-engine -B /tmp/pineforge-engine-build \
  -DCMAKE_BUILD_TYPE=Release -DPINEFORGE_BUILD_TESTS=OFF
cmake --build /tmp/pineforge-engine-build -j4
```

If CMake fetches Eigen, its headers are under the build tree's
`_deps/eigen-src`; a system Eigen install is also fine. The engine corpus is a
separate checkout/submodule in some environments. The corpus test skips when
it cannot find that tree, so inspect skip reasons before treating a run as
complete.

## Engine pairing

A released codegen `X.Y.Z` is supported only with engine tag `vX.Y.Z`, using
that release's generated headers and `libpineforge.a`. Prereleases match
exactly too: codegen `1.0.0-rc.1` requires engine `v1.0.0-rc.1`. Do not pair
different patch or prerelease tags, even if `PF_ABI_VERSION` is equal.

On **every** pair change, rerun codegen on the Pine source, then rebuild and
relink each generated strategy translation unit against the new pair's headers
and static library. Replacing only the library, or recompiling old generated
C++, is not a supported migration. Development branches can test against
paired in-progress engine commits, but that does not create a supported
cross-version release pair. See the [public contract](docs/PUBLIC_CONTRACT.md).

## Required checks

Point the test harness at the engine source, its generated header, runtime
archive, Eigen headers, and public corpus. Adjust these paths for your build:

```bash
export PINEFORGE_ENGINE_INCLUDE=../pineforge-engine/include
export PINEFORGE_GENERATED_INCLUDE=/tmp/pineforge-engine-build/include
export PINEFORGE_ENGINE_LIB=/tmp/pineforge-engine-build/lib/libpineforge.a
export PINEFORGE_EIGEN_INCLUDE=/tmp/pineforge-engine-build/_deps/eigen-src
export PINEFORGE_ENGINE_CORPUS=../pineforge-engine/corpus
export CXX=/usr/bin/c++
python -m pytest -ra
python -m pytest -ra tests/test_compile_corpus.py
```

The full suite includes the corpus sweep; run the corpus command separately so
its coverage and result are visible. A green suite with compile or E2E skips
does not verify generated C++ against the engine. If you change engine C++
headers or sources, rebuild the engine before rerunning these checks. The
current item count is determined by `python -m pytest --collect-only -q`, not
by a fixed number in this guide.

Check the Pyodide package and native/Pyodide parity as well:

```bash
npm ci
npm audit --audit-level=high
npm run gate:selftest
GATE_FULL=1 node gate/run-gate.mjs
```

The full gate writes `release.json` and the validated archive consumed by
`node scripts/build-npm-package.mjs`. For release packaging checks, install
the Python `build` frontend, run `python -m build`, assemble the npm payload,
then run `npm pack --dry-run` from `npm/`. These commands build local artifacts;
publishing is a separate release operation.

## Change and review notes

- Add or update tests for behavior changes. Keep the engine corpus compiling;
  an intentional support drop needs the rationale required by [AGENTS.md](AGENTS.md).
- If existing scripts emit different C++, list the affected public-corpus
  translation units and whether their trades move when run with the matching
  engine.
- Keep [CHANGELOG.md](CHANGELOG.md) current for user-visible changes. Its
  release-note policy applies to both stable and prerelease tags.
