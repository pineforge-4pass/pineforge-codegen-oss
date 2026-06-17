// Canary: prove the gate's checks actually catch divergences. Imports the PURE
// comparator + verdict checker (gate/compare.mjs) so it runs in <1s without
// loading Pyodide. Covers BOTH (a) native↔wasm parity (compareResults) and
// (b) expected-verdict-by-corpus-dir (checkExpectedVerdict).
import { checkExpectedVerdict, compareResults } from "./compare.mjs";

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
if (failed) {
  console.error(`gate selftest: ${failed} case(s) failed`);
  process.exit(1);
}
console.log(`gate selftest: ${cmpCases.length} comparator + ${verdictCases.length} verdict cases OK`);
