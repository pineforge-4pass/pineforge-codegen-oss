// Differential parity gate: run the transpiler in real wasm32 Pyodide and assert
// it behaves identically to native CPython on EVERY corpus input — success
// (byte-identical cpp) AND failure (same verdict/error/diagnostics + same
// unexpected-exception type+message). Also writes release.json.
//
//   node gate/run-gate.mjs              # smoke: all err + first N ok fixtures
//   GATE_FULL=1 node gate/run-gate.mjs  # entire corpus
//
// Exit 0 = parity holds; exit 1 = mismatch(es) or setup failure.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { checkExpectedVerdict, compareResults } from "./compare.mjs";

const require = createRequire(import.meta.url);
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const CORPUS = join(ROOT, "tests", "gate-corpus");
const SCRATCH = join(HERE, ".scratch");
const GLUE = readFileSync(join(HERE, "glue.py"), "utf8");
const PYTHON = process.env.GATE_PYTHON ?? "python3";
const OK_CAP = process.env.GATE_FULL ? Infinity : 60;

function branchItems(branch) {
  const dir = join(CORPUS, branch);
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((x) => x.endsWith(".pine"))
    .sort()
    .map((f) => ({ name: `${branch}/${f}`, src: readFileSync(join(dir, f), "utf8") }));
}

async function main() {
  const okItems = branchItems("ok");
  const errItems = branchItems("err");
  if (okItems.length + errItems.length === 0) {
    console.error("gate: empty corpus at tests/gate-corpus/{ok,err}");
    process.exit(1);
  }
  // ALWAYS include the (small) err branch so the smoke run exercises the failure
  // path; cap only the large ok branch.
  const items = [
    ...(Number.isFinite(OK_CAP) ? okItems.slice(0, OK_CAP) : okItems),
    ...errItems,
  ];
  console.log(
    `gate: ${items.length} fixtures (ok=${Math.min(okItems.length, OK_CAP)}/${okItems.length}, err=${errItems.length}, GATE_FULL=${process.env.GATE_FULL ? "1" : "0"})`,
  );

  // 1. Pack the in-repo source into an archive (excluding __pycache__).
  mkdirSync(SCRATCH, { recursive: true });
  const archive = join(SCRATCH, "pineforge_codegen.tar.gz");
  const tar = require("tar");
  await tar.create(
    { gzip: true, file: archive, cwd: ROOT, portable: true, filter: (p) => !p.includes("__pycache__") },
    ["pineforge_codegen"],
  );
  const archiveBytes = readFileSync(archive);

  // 2. Load Pyodide, unpack, run glue.
  const { loadPyodide } = await import("pyodide");
  const indexURL = dirname(require.resolve("pyodide/package.json"));
  const pyodide = await loadPyodide({ indexURL });
  const u8 = new Uint8Array(archiveBytes.buffer, archiveBytes.byteOffset, archiveBytes.byteLength);
  pyodide.unpackArchive(u8, "gztar", { extractDir: "/codegen" });
  pyodide.runPython(GLUE);
  const transpileJson = pyodide.globals.get("transpile_json");

  // 3. Native oracle (one process, whole corpus). PYTHONHASHSEED pinned for
  //    determinism per spec §6.1.
  const oracleOut = execFileSync(PYTHON, [join(HERE, "oracle.py")], {
    input: JSON.stringify(items),
    env: { ...process.env, PYTHONPATH: ROOT, PYTHONHASHSEED: "0" },
    maxBuffer: 256 * 1024 * 1024,
    encoding: "utf8",
  });
  const native = JSON.parse(oracleOut);

  // 4. Pyodide side + compare. The gate enforces TWO independent properties:
  //    (a) native↔wasm parity (compareResults) and
  //    (b) the EXPECTED verdict by corpus dir (checkExpectedVerdict): ok/* must
  //        succeed, err/* must be rejected. A purely differential check would
  //        let two identical crashes — or an ok/ fixture that erroneously errors
  //        — slip through; (b) closes that gap.
  const mismatches = [];
  const verdictFailures = [];
  for (const { name, src } of items) {
    let browser;
    try {
      browser = { json: transpileJson(src) };
    } catch (err) {
      browser = { unexpected: `${err?.constructor?.name ?? "Error"}: ${err?.message ?? String(err)}` };
    }
    const m = compareResults(name, native[name], browser);
    if (m) mismatches.push(m);
    const expectOk = name.startsWith("ok/");
    const v = checkExpectedVerdict(name, expectOk, native[name], browser);
    if (v) verdictFailures.push(v);
  }

  // 5. release.json (versions derived from the loaded Pyodide lock).
  const lock = require("pyodide/pyodide-lock.json");
  const codegen = readFileSync(join(ROOT, "VERSION"), "utf8").trim();
  const pyodideVer = readFileSync(join(ROOT, "PYODIDE_TARGET"), "utf8").trim();
  const release = {
    codegen,
    pyodide: pyodideVer,
    python: lock.info.python,
    emscripten: lock.info.platform,
    sha256: createHash("sha256").update(archiveBytes).digest("hex"),
  };
  writeFileSync(join(ROOT, "release.json"), JSON.stringify(release, null, 2) + "\n");
  console.log("gate: release.json ->", JSON.stringify(release));

  if (mismatches.length || verdictFailures.length) {
    if (mismatches.length) {
      console.error(`gate: ${mismatches.length} PARITY MISMATCH(es):\n` + mismatches.join("\n"));
    }
    if (verdictFailures.length) {
      console.error(`gate: ${verdictFailures.length} VERDICT FAILURE(s) (wrong ok/err result):\n` + verdictFailures.join("\n"));
    }
    process.exit(1);
  }
  console.log(`gate: PARITY OK over ${items.length} fixtures (verdicts asserted: ok/* succeed, err/* rejected)`);
}

main().catch((e) => {
  console.error("gate: fatal", e);
  process.exit(1);
});
