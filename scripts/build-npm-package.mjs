// Assemble the publishable @pineforge/codegen-pyodide payload into npm/.
// Inputs (all produced by `npm run gate:full`, which MUST run first):
//   gate/.scratch/pineforge_codegen.tar.gz  — the gate-VALIDATED archive
//   release.json                            — derived versions + archive sha256
// Plus: the unpacked pineforge_codegen/ source (for Node PYTHONPATH consumers),
// and tables.json (scripts/dump-tables.py). Version synced from VERSION.
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as tar from "tar";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const NPM = join(ROOT, "npm");
const SCRATCH_ARCHIVE = join(ROOT, "gate", ".scratch", "pineforge_codegen.tar.gz");
const RELEASE = join(ROOT, "release.json");
const VERSION = readFileSync(join(ROOT, "VERSION"), "utf8").trim();

function fail(msg) {
  console.error(`build-npm-package: ${msg}`);
  process.exit(1);
}

if (!existsSync(SCRATCH_ARCHIVE) || !existsSync(RELEASE)) {
  fail("missing gate outputs — run `npm run gate:full` first (need gate/.scratch/*.tar.gz + release.json)");
}

// release.json must agree with VERSION + PYODIDE_TARGET (sanity).
const release = JSON.parse(readFileSync(RELEASE, "utf8"));
if (release.codegen !== VERSION) fail(`release.json codegen ${release.codegen} != VERSION ${VERSION}`);

// 1. Clean payload (keep tracked manifest + index).
for (const p of ["pineforge_codegen", "tables.json", "release.json"]) {
  rmSync(join(NPM, p), { recursive: true, force: true });
}
const versionedArchive = `pineforge_codegen-${VERSION}.tar.gz`;
rmSync(join(NPM, versionedArchive), { force: true });

// 2. Validated archive (versioned filename) + release.json.
cpSync(SCRATCH_ARCHIVE, join(NPM, versionedArchive));
cpSync(RELEASE, join(NPM, "release.json"));

// 3. Unpacked source tree — extracted FROM the gate-validated archive (single
//    source of truth). Never re-copy the working tree: it could have drifted
//    from the validated bytes between the gate run and this build, which would
//    ship an unpacked tree the gate never saw.
tar.x({ file: SCRATCH_ARCHIVE, cwd: NPM, sync: true });

// 4. tables.json (introspection).
const tablesJson = execFileSync("python3", [join(ROOT, "scripts", "dump-tables.py")], {
  env: { ...process.env, PYTHONPATH: ROOT },
  maxBuffer: 64 * 1024 * 1024,
  encoding: "utf8",
});
writeFileSync(join(NPM, "tables.json"), tablesJson);

// 5. Sync npm/package.json version from VERSION.
const pkgPath = join(NPM, "package.json");
const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
if (pkg.version !== VERSION) {
  pkg.version = VERSION;
  writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n");
}

console.log(
  `build-npm-package: npm/ ready — ${versionedArchive}, pineforge_codegen/, tables.json, release.json @ v${VERSION}`,
);
