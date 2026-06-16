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
