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

const canonicalManifest = JSON.parse(readFileSync(join(payloadDir, "package.json"), "utf8"));
const compatManifest = JSON.parse(readFileSync(join(compatSourceDir, "package.json"), "utf8"));
assert.deepStrictEqual(
  new Set(Object.keys(compatManifest.exports)),
  new Set(Object.keys(canonicalManifest.exports)),
  "compat package subpaths differ",
);
assert.strictEqual(
  compatManifest.dependencies["@tunaxis/codegen-pyodide"],
  canonicalManifest.version,
  "render the compatibility package with the built payload's version before testing",
);

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

assert.deepStrictEqual(
  new Set(Object.keys(pineforge)),
  new Set(Object.keys(tunaxis)),
  "compat module export names differ",
);
for (const name of Object.keys(tunaxis)) {
  assert.strictEqual(pineforge[name], tunaxis[name], `compat export differs: ${name}`);
}

const requireFromScratch = createRequire(join(scratchDir, "require-probe.cjs"));
const jsonSubpaths = ["./tables.json", "./release.json", "./diagnostics_catalog.json"];
const packageSubpath = (packageName, subpath) => packageName + "/" + subpath.slice(2);
for (const subpath of jsonSubpaths) {
  const canonical = requireFromScratch(packageSubpath("@tunaxis/codegen-pyodide", subpath));
  const forwarded = requireFromScratch(packageSubpath("@pineforge/codegen-pyodide", subpath));
  assert.deepStrictEqual(forwarded, canonical, `compat JSON contents differ: ${subpath}`);
  const canonicalPath = requireFromScratch.resolve(packageSubpath("@tunaxis/codegen-pyodide", subpath));
  assert.deepStrictEqual(
    forwarded,
    JSON.parse(readFileSync(canonicalPath, "utf8")),
    `compat JSON contents differ: ${subpath}`,
  );
}

const jsonProbePath = join(scratchDir, "json-probe.mjs");
const importLines = jsonSubpaths
  .flatMap((subpath, index) => [
    `import canonicalJson${index} from ${JSON.stringify(packageSubpath("@tunaxis/codegen-pyodide", subpath))} with { type: "json" };`,
    `import compatJson${index} from ${JSON.stringify(packageSubpath("@pineforge/codegen-pyodide", subpath))} with { type: "json" };`,
  ])
  .join("\n");
const exportNames = jsonSubpaths.flatMap((_, index) => [`canonicalJson${index}`, `compatJson${index}`]).join(", ");
writeFileSync(jsonProbePath, `${importLines}\nexport { ${exportNames} };\n`);
const jsonModules = await import(pathToFileURL(jsonProbePath).href);
for (const [index, subpath] of jsonSubpaths.entries()) {
  const canonical = requireFromScratch(packageSubpath("@tunaxis/codegen-pyodide", subpath));
  assert.deepStrictEqual(
    jsonModules[`compatJson${index}`],
    jsonModules[`canonicalJson${index}`],
    `compat ESM JSON contents differ: ${subpath}`,
  );
  assert.deepStrictEqual(
    jsonModules[`canonicalJson${index}`],
    canonical,
    `canonical ESM and CommonJS JSON contents differ: ${subpath}`,
  );
}

console.log(`compatibility checks passed in ${scratchDir}`);
