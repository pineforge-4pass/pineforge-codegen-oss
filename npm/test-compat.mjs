#!/usr/bin/env node

import assert from "node:assert/strict";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";

const payloadArg = process.argv[2];
if (!payloadArg) {
  throw new Error("usage: node npm/test-compat.mjs <built-tunaxis-payload-directory>");
}

const payloadDir = resolve(payloadArg);
const compatSourceDir = resolve(fileURLToPath(new URL("../npm-compat/", import.meta.url)));
if (!existsSync(join(payloadDir, "package.json"))) {
  throw new Error(`built payload is missing package.json: ${payloadDir}`);
}
if (!existsSync(join(compatSourceDir, "package.json"))) {
  throw new Error(`compat package is missing package.json: ${compatSourceDir}`);
}

const scratchDir = mkdtempSync(join(tmpdir(), "tunaxis-codegen-pyodide-compat-"));
const nodeModulesDir = join(scratchDir, "node_modules");
const tunaxisScopeDir = join(nodeModulesDir, "@tunaxis");
const pineforgeScopeDir = join(nodeModulesDir, "@pineforge");
mkdirSync(tunaxisScopeDir, { recursive: true });
mkdirSync(pineforgeScopeDir, { recursive: true });

// The canonical payload is linked; the compat package is copied so its
// dependency lookup starts at this scratch node_modules layout.
symlinkSync(payloadDir, join(tunaxisScopeDir, "codegen-pyodide"), "dir");
cpSync(compatSourceDir, join(pineforgeScopeDir, "codegen-pyodide"), { recursive: true });

const probePath = join(scratchDir, "probe.mjs");
writeFileSync(
  probePath,
  [
    'import * as tunaxis from "@tunaxis/codegen-pyodide";',
    'import * as pineforge from "@pineforge/codegen-pyodide";',
    "export { tunaxis, pineforge };",
  ].join("\n") + "\n",
);
const { tunaxis, pineforge } = await import(pathToFileURL(probePath).href);

for (const name of Object.keys(tunaxis)) {
  assert.ok(Object.prototype.hasOwnProperty.call(pineforge, name), `compat export is missing: ${name}`);
  assert.strictEqual(pineforge[name], tunaxis[name], `compat export differs: ${name}`);
}

const requireFromScratch = createRequire(join(scratchDir, "require-probe.cjs"));
const jsonSubpaths = ["./tables.json", "./release.json", "./diagnostics_catalog.json"];
const packageSubpath = (packageName, subpath) => packageName + "/" + subpath.slice(2);
for (const subpath of jsonSubpaths) {
  const canonical = requireFromScratch(packageSubpath("@tunaxis/codegen-pyodide", subpath));
  const forwarded = requireFromScratch(packageSubpath("@pineforge/codegen-pyodide", subpath));
  assert.strictEqual(forwarded, canonical, `compat JSON object differs: ${subpath}`);
  const canonicalPath = requireFromScratch.resolve(packageSubpath("@tunaxis/codegen-pyodide", subpath));
  assert.deepStrictEqual(
    forwarded,
    JSON.parse(readFileSync(canonicalPath, "utf8")),
    `compat JSON contents differ: ${subpath}`,
  );
}

const jsonProbePath = join(scratchDir, "json-probe.mjs");
const importLines = jsonSubpaths
  .map((subpath, index) => `import * as compatJson${index} from ${JSON.stringify(packageSubpath("@pineforge/codegen-pyodide", subpath))};`)
  .join("\n");
const exportNames = jsonSubpaths.map((_, index) => `compatJson${index}`).join(", ");
writeFileSync(jsonProbePath, `${importLines}\nexport { ${exportNames} };\n`);
const jsonModules = await import(pathToFileURL(jsonProbePath).href);
for (const [index, subpath] of jsonSubpaths.entries()) {
  const canonical = requireFromScratch(packageSubpath("@tunaxis/codegen-pyodide", subpath));
  assert.strictEqual(
    jsonModules[`compatJson${index}`].default,
    canonical,
    `compat ESM JSON wrapper differs: ${subpath}`,
  );
}

console.log(`compatibility checks passed in ${scratchDir}`);
