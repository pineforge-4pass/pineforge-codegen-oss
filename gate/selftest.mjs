// Canary: prove the gate's comparator actually catches a divergence. Imports the
// PURE comparator (gate/compare.mjs) so it runs in <1s without loading Pyodide.
import { compareResults } from "./compare.mjs";

const cases = [
  // [name, native, browser, mustFlag]
  ["same-ok", { json: '{"ok":true,"cpp":"X"}' }, { json: '{"ok":true,"cpp":"X"}' }, false],
  ["cpp-differs", { json: '{"ok":true,"cpp":"X"}' }, { json: '{"ok":true,"cpp":"Y"}' }, true],
  ["verdict-differs", { json: '{"ok":true,"cpp":"X"}' }, { json: '{"ok":false,"error":"e","diagnostics":[]}' }, true],
  ["error-differs", { json: '{"ok":false,"error":"a","diagnostics":[]}' }, { json: '{"ok":false,"error":"b","diagnostics":[]}' }, true],
  ["unexpected-one-side", { json: '{"ok":true,"cpp":"X"}' }, { unexpected: "TypeError: boom" }, true],
  ["unexpected-both-same", { unexpected: "TypeError: boom" }, { unexpected: "TypeError: boom" }, false],
  ["unexpected-both-diff", { unexpected: "TypeError: a" }, { unexpected: "ValueError: b" }, true],
  ["missing-native", undefined, { json: '{"ok":true,"cpp":"X"}' }, true],
];

let failed = 0;
for (const [name, n, b, mustFlag] of cases) {
  const flagged = compareResults(name, n, b) !== null;
  if (flagged !== mustFlag) {
    console.error(`selftest FAIL: ${name} expected mustFlag=${mustFlag} got ${flagged}`);
    failed++;
  }
}
if (failed) {
  console.error(`gate selftest: ${failed} case(s) failed`);
  process.exit(1);
}
console.log(`gate selftest: ${cases.length} comparator cases OK`);
