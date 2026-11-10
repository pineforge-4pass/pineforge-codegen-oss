// Canary for the gate's pure comparison, verdict, and runtime-identity rules.
// These fixtures check helper behavior; actual runtime identity and whole-gate
// parity require running the gate with initialized Pyodide.
import assert from "node:assert/strict";
import { checkExpectedVerdict, compareResults } from "./compare.mjs";
import { checkRuntimeVersion } from "./runtime-version.mjs";

const OK = '{"ok":true,"cpp":"X"}';
const ERR = '{"ok":false,"error":"e","diagnostics":[]}';

// --- (a) differential comparator cases: [name, native, browser, mustFlag] ---
const cmpCases = [
  ["same-ok", { json: OK }, { json: OK }, false],
  ["cpp-differs", { json: '{"ok":true,"cpp":"X"}' }, { json: '{"ok":true,"cpp":"Y"}' }, true],
  ["verdict-differs", { json: OK }, { json: ERR }, true],
  ["error-differs", { json: '{"ok":false,"error":"a","diagnostics":[]}' }, { json: '{"ok":false,"error":"b","diagnostics":[]}' }, true],
  ["unexpected-one-side", { json: OK }, { unexpected: "TypeError: boom" }, true],
  ["unexpected-both-same", { unexpected: "TypeError: boom" }, { unexpected: "TypeError: boom" }, false],
  ["unexpected-both-diff", { unexpected: "TypeError: a" }, { unexpected: "ValueError: b" }, true],
  ["missing-native", undefined, { json: OK }, true],
];

// --- (b) expected-verdict cases: [name, expectOk, native, browser, mustFlag] ---
// A native↔wasm match with the WRONG verdict (e.g. ok/ that errors, or a shared
// unexpected exception) must FAIL even though compareResults would pass it.
const verdictCases = [
  ["ok/good", true, { json: OK }, { json: OK }, false],
  ["err/bad", false, { json: ERR }, { json: ERR }, false],
  ["ok/that-errors-both-sides", true, { json: ERR }, { json: ERR }, true],
  ["err/that-succeeds-both-sides", false, { json: OK }, { json: OK }, true],
  ["ok/unexpected-both-same", true, { unexpected: "TypeError: boom" }, { unexpected: "TypeError: boom" }, true],
  ["err/unexpected-both-same", false, { unexpected: "TypeError: boom" }, { unexpected: "TypeError: boom" }, true],
  ["ok/native-wrong-only", true, { json: ERR }, { json: OK }, true],
  ["ok/pyodide-wrong-only", true, { json: OK }, { json: ERR }, true],
  ["ok/missing-native", true, undefined, { json: OK }, true],
  ["ok/malformed", true, { json: "not json" }, { json: OK }, true],
];

// --- (c) exact version identity: independent inputs, no runtime initialization ---
const matchingVersions = ["0.0.0", "2.17.4", "41.6.2-rc.3+build.009"];
const mismatchCases = [
  ["observed", ["2.17.5", "2.17.4", "2.17.4"]],
  ["target", ["2.17.4", "2.18.0", "2.17.4"]],
  ["peer", ["2.17.4", "2.17.4", "3.0.0"]],
  ["build-metadata", ["41.6.2+build.1", "41.6.2+build.2", "41.6.2+build.2"]],
];
const invalidVersions = [
  ["missing", undefined, "<undefined>"],
  ["null", null, "<null>"],
  ["number", 2174, "<number>"],
  ["boolean", true, "<boolean>"],
  ["boxed-string", new String("2.17.4"), "<object>"],
  ["coercible-object", { toString: () => "2.17.4" }, "<object>"],
  ...[
    "", " ", " 2.17.4", "2.17.4 ", "2.17.4\n", "2.\t17.4",
    "*", "2.17.x", "^2.17.4", "~2.17.4", ">=2.17.4", "=2.17.4",
    "2.17.4 || 3.0.0", "v2.17.4", "2.17", "2.17.4.1", "02.17.4",
    "2.17.4-01", "2.17.4-", "2.17.4+", "2.17.4-rc..1",
  ].map((value) => [`token ${JSON.stringify(value)}`, value, JSON.stringify(value)]),
];
const rejectedVersions = mismatchCases.map(([name, [observed, target, peer]]) => [
  `mismatch-${name}`,
  [observed, target, peer],
  `Pyodide version identity mismatch: observed=${JSON.stringify(observed)}, target=${JSON.stringify(target)}, peer=${JSON.stringify(peer)}`,
]);
for (const [name, value, shown] of invalidVersions) {
  for (const [index, field] of ["observed", "target", "peer"].entries()) {
    const inputs = ["2.17.4", "2.17.4", "2.17.4"];
    inputs[index] = value;
    rejectedVersions.push([
      `${field}-${name}`, inputs,
      `Pyodide version identity invalid: ${field}=${shown} (expected an exact version string)`,
    ]);
  }
  // Even equal invalid inputs must fail; equality alone cannot validate identity.
  rejectedVersions.push([
    `all-${name}`, [value, value, value],
    "Pyodide version identity invalid: " +
      ["observed", "target", "peer"].map((field) => `${field}=${shown} (expected an exact version string)`).join("; "),
  ]);
}

let failed = 0;
for (const [name, n, b, mustFlag] of cmpCases) {
  const flagged = compareResults(name, n, b) !== null;
  if (flagged !== mustFlag) {
    console.error(`selftest FAIL (compareResults): ${name} expected mustFlag=${mustFlag} got ${flagged}`);
    failed++;
  }
}
for (const [name, expectOk, n, b, mustFlag] of verdictCases) {
  const flagged = checkExpectedVerdict(name, expectOk, n, b) !== null;
  if (flagged !== mustFlag) {
    console.error(`selftest FAIL (checkExpectedVerdict): ${name} expected mustFlag=${mustFlag} got ${flagged}`);
    failed++;
  }
}
for (const version of matchingVersions) {
  try {
    assert.equal(checkRuntimeVersion(version, version, version), version);
  } catch (err) {
    console.error(`selftest FAIL (checkRuntimeVersion): matching ${version}`, err);
    failed++;
  }
}
for (const [name, inputs, message] of rejectedVersions) {
  try {
    assert.throws(() => checkRuntimeVersion(...inputs), { name: "Error", message });
  } catch (err) {
    console.error(`selftest FAIL (checkRuntimeVersion): ${name}`, err);
    failed++;
  }
}
if (failed) {
  console.error(`gate selftest: ${failed} case(s) failed`);
  process.exit(1);
}
console.log(`gate selftest: ${cmpCases.length} comparator + ${verdictCases.length} verdict + ${matchingVersions.length + rejectedVersions.length} runtime-identity helper cases OK`);
