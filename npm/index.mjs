// Entry for @pineforge/codegen-pyodide. Resolves the packaged payload paths and
// metadata so consumers (the app's build + Node oracle/grammar tooling) read
// everything from this one dependency — no git submodule.
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

export const release = require("./release.json");
export const tables = require("./tables.json");

// Absolute path to the gate-validated Pyodide archive (gztar) to unpackArchive.
export const archivePath = join(HERE, `pineforge_codegen-${release.codegen}.tar.gz`);

// Absolute path to the unpacked Python source dir's PARENT — put on PYTHONPATH
// so `import pineforge_codegen` resolves (oracle tests, grammar gen).
export const sourceRoot = HERE;
export const codegenSourceDir = join(HERE, "pineforge_codegen");
