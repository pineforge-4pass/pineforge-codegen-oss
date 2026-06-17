// Pure, side-effect-free comparator for the gate. Both run-gate.mjs (the runner)
// and selftest.mjs (the canary) import this — so importing it never triggers a
// gate run. Each side is {json: "<transpile_json string>"} (normal return) or
// {unexpected: "Type: msg"} (non-CompileError exception). Returns a mismatch
// string, or null if the two sides agree.
export function compareResults(name, native, browser) {
  if (!native) return `${name}: oracle produced no result`;
  if (!browser) return `${name}: pyodide produced no result`;
  if (native.unexpected || browser.unexpected) {
    if (native.unexpected !== browser.unexpected) {
      return `${name}: unexpected-exception mismatch\n  native : ${native.unexpected ?? "<none>"}\n  pyodide: ${browser.unexpected ?? "<none>"}`;
    }
    return null;
  }
  if (native.json !== browser.json) {
    let detail = "";
    try {
      const n = JSON.parse(native.json);
      const b = JSON.parse(browser.json);
      if (n.ok !== b.ok) detail = `verdict ${b.ok} (pyodide) != ${n.ok} (native)`;
      else if (n.ok) detail = "C++ output differs";
      else detail = `error/diagnostics differ\n  native : ${native.json}\n  pyodide: ${browser.json}`;
    } catch {
      detail = `raw json differs\n  native : ${native.json}\n  pyodide: ${browser.json}`;
    }
    return `${name}: ${detail}`;
  }
  return null;
}

// Expected verdict for a corpus branch: "ok/*" fixtures must transpile
// successfully (result.ok === true), "err/*" fixtures must be rejected
// (result.ok === false). Anything else — the wrong verdict, an unparseable
// payload, or an unexpected (non-CompileError) exception — is a gate failure
// even when native and wasm agree (two identical crashes must NOT pass).
//
// This is intentionally separate from compareResults so the gate enforces BOTH
// (a) native↔wasm parity and (b) the right answer. `side` is {json} or
// {unexpected}; `expectOk` is true for "ok", false for "err".
function verdictOf(side) {
  if (!side) return { kind: "missing" };
  if (side.unexpected) return { kind: "unexpected", detail: side.unexpected };
  try {
    const v = JSON.parse(side.json);
    if (typeof v.ok !== "boolean") return { kind: "malformed", detail: side.json };
    return { kind: "verdict", ok: v.ok };
  } catch {
    return { kind: "malformed", detail: side.json };
  }
}

// Returns a failure string if either side does not match the expected verdict
// for the fixture's branch, or null if both sides produced the expected verdict.
export function checkExpectedVerdict(name, expectOk, native, browser) {
  for (const [label, side] of [["native", native], ["pyodide", browser]]) {
    const r = verdictOf(side);
    if (r.kind === "missing") return `${name}: ${label} produced no result`;
    if (r.kind === "unexpected") {
      return `${name}: ${label} threw an unexpected exception (expected ok=${expectOk}): ${r.detail}`;
    }
    if (r.kind === "malformed") {
      return `${name}: ${label} returned a malformed result (expected ok=${expectOk}): ${r.detail}`;
    }
    if (r.ok !== expectOk) {
      return `${name}: ${label} verdict ok=${r.ok} but corpus dir expects ok=${expectOk}`;
    }
  }
  return null;
}
