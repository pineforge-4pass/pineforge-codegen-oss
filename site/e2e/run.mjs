#!/usr/bin/env node
// E2E harness (`npm run e2e`). Brings up a throwaway copy of the whole site and runs Playwright
// against it through the browser and HTTP, then tears everything down and exits with Playwright's code.
//
// - Picks free ports at run time; nothing is fixed.
// - Generates a throwaway Ed25519 signing key (kid `pfl-e2e-<timestamp>`) and webhook secret per run.
// - Builds the static site if `out/` is missing (`npm run build`; E2E_FORCE_BUILD=1 rebuilds).
// - Applies the D1 migrations to fresh local persistence dirs and starts two `wrangler pages dev out`
//   instances: the test-mode site (sk_test_ key) and a second one holding a live-looking key
//   (sk_live_x) for the live-payment guard spec.
// - Starts the local Stripe and Resend fakes (e2e/fakes/). They stand in for the two third parties,
//   the only mocking in the suite.
// Extra CLI args go to `playwright test` (e.g. `npm run e2e -- specs/purchase.spec.mjs --project desktop`).
// Env: E2E_FORCE_BUILD=1, E2E_SKIP_BUILD=1, E2E_KEEP_TMP=1, E2E_READY_TIMEOUT_MS (default 180000).
import { spawn } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { startStripeFake } from "./fakes/stripe.mjs";
import { startResendFake } from "./fakes/resend.mjs";

const siteDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runDir = path.join(siteDir, "e2e", "artifacts", "run");
const extraArgs = process.argv.slice(2);
const stamp = Date.now();
const runId = `e2e-${stamp}`;
const npx = process.platform === "win32" ? "npx.cmd" : "npx";
const readyTimeoutMs = Number(process.env.E2E_READY_TIMEOUT_MS ?? 180_000);

const children = new Set();
const closers = [];
let tmpRoot = "";
let teardownPromise = null;
let playwright = null;
let signalled = null;

const say = (msg) => console.log(`[e2e] ${msg}`);

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// Environment for child processes: never leak a real Stripe key from the caller's shell into the run.
function childEnv(extra = {}) {
  const env = { ...process.env, WRANGLER_SEND_METRICS: "false" };
  for (const k of Object.keys(env)) if (k.startsWith("STRIPE_")) delete env[k];
  return { ...env, ...extra };
}

// Plain-text logs for the helper processes whose output goes to files.
function quietEnv() {
  const env = childEnv({ NO_COLOR: "1" });
  delete env.FORCE_COLOR;
  return env;
}

function start(name, args, { env = quietEnv(), logFile } = {}) {
  const out = fs.openSync(logFile ?? path.join(runDir, `${name}.txt`), "a");
  const child = spawn(npx, args, { cwd: siteDir, env, detached: true, stdio: ["ignore", out, out] });
  fs.closeSync(out);
  child.label = name;
  child.exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  child.exited.then(() => children.delete(child));
  children.add(child);
  return child;
}

async function runToEnd(name, args, opts) {
  const child = start(name, args, opts);
  const { code, signal } = await child.exited;
  return code ?? (signal ? 1 : 0);
}

async function tail(file, lines = 60) {
  try {
    const text = await fsp.readFile(file, "utf8");
    return text.split("\n").slice(-lines).join("\n");
  } catch {
    return "(no output)";
  }
}

async function killGroup(child, graceMs = 8000) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    return;
  }
  const done = await Promise.race([child.exited.then(() => true), new Promise((r) => setTimeout(() => r(false), graceMs))]);
  // Whatever of the group is still alive (workerd under wrangler, or the leader itself) goes now.
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch {}
  if (!done) await Promise.race([child.exited, new Promise((r) => setTimeout(r, 3000))]);
}

// Idempotent: every caller waits for the same teardown to finish.
function teardown() {
  teardownPromise ??= doTeardown();
  return teardownPromise;
}

async function doTeardown() {
  if (playwright && playwright.exitCode === null && playwright.signalCode === null) {
    // Playwright shares our process group; a SIGTERM sent to this harness alone must reach it too.
    try {
      playwright.kill("SIGTERM");
    } catch {}
    await Promise.race([new Promise((r) => playwright.once("exit", r)), new Promise((r) => setTimeout(r, 10_000))]);
  }
  await Promise.all([...children].map((c) => killGroup(c)));
  for (const close of closers.reverse()) {
    try {
      await close();
    } catch {}
  }
  if (tmpRoot && !process.env.E2E_KEEP_TMP) await fsp.rm(tmpRoot, { recursive: true, force: true });
  else if (tmpRoot) say(`kept temp dir ${tmpRoot}`);
}

async function fail(message, logFiles = []) {
  console.error(`[e2e] FAILED: ${message}`);
  for (const f of logFiles) console.error(`--- ${path.relative(siteDir, f)} (tail) ---\n${await tail(f)}\n`);
  await teardown();
  process.exit(1);
}

const SIGNAL_EXIT = { SIGINT: 130, SIGTERM: 143 };
for (const sig of Object.keys(SIGNAL_EXIT)) {
  process.on(sig, async () => {
    if (signalled) return;
    signalled = sig;
    say(`${sig}: tearing down`);
    await teardown();
    process.exit(SIGNAL_EXIT[sig]);
  });
}

async function waitReady(url, child, logFile) {
  const deadline = Date.now() + readyTimeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) await fail(`${child.label} exited before it was ready`, [logFile]);
    try {
      const r = await fetch(`${url}/en/`, { signal: AbortSignal.timeout(5000) });
      if (r.status === 200) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  await fail(`${child.label} not ready at ${url}/en/ after ${readyTimeoutMs} ms`, [logFile]);
}

async function makeKeys() {
  const { privateKey } = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const jwk = await crypto.subtle.exportKey("jwk", privateKey);
  const kid = `pfl-e2e-${stamp}`;
  const pub = { kty: "OKP", crv: "Ed25519", x: jwk.x, kid, alg: "EdDSA", use: "sig" };
  return { kid, privateJwk: { ...pub, d: jwk.d }, keyring: { keys: [pub] } };
}

async function migrate(persistDir, name) {
  const logFile = path.join(runDir, `${name}.txt`);
  const code = await runToEnd(name, ["wrangler", "d1", "migrations", "apply", "pineforge-license", "--local", "--persist-to", persistDir], { logFile });
  if (code !== 0) await fail(`D1 migrations failed (${name}, exit ${code})`, [logFile]);
}

async function startSite(name, { port, inspectorPort, persistDir, bindings }) {
  const logFile = path.join(runDir, `${name}.txt`);
  const args = ["wrangler", "pages", "dev", "out", "--ip", "127.0.0.1", "--port", String(port), "--inspector-port", String(inspectorPort), "--persist-to", persistDir];
  for (const [k, v] of Object.entries(bindings)) args.push("--binding", `${k}=${v}`);
  const child = start(name, args, { logFile });
  const url = `http://127.0.0.1:${port}`;
  await waitReady(url, child, logFile);
  return { url, child, logFile };
}

async function main() {
  await fsp.rm(runDir, { recursive: true, force: true });
  await fsp.mkdir(runDir, { recursive: true });
  tmpRoot = await fsp.mkdtemp(path.join(os.tmpdir(), "pf-license-e2e-"));
  say(`run ${runId}; logs in ${path.relative(siteDir, runDir)}/`);

  const outReady = fs.existsSync(path.join(siteDir, "out", "en", "index.html"));
  if (!process.env.E2E_SKIP_BUILD && (process.env.E2E_FORCE_BUILD || !outReady)) {
    say("building the site (npm run build)");
    const logFile = path.join(runDir, "build.txt");
    const child = spawn(process.platform === "win32" ? "npm.cmd" : "npm", ["run", "build"], { cwd: siteDir, env: quietEnv(), detached: true, stdio: ["ignore", "pipe", "pipe"] });
    const log = fs.createWriteStream(logFile);
    child.stdout.pipe(log);
    child.stderr.pipe(log);
    children.add(child);
    child.exited = new Promise((resolve) => child.once("exit", (code) => resolve(code)));
    const code = await child.exited;
    children.delete(child);
    if (code !== 0) await fail(`npm run build exited ${code}`, [logFile]);
  }

  const { kid, privateJwk, keyring } = await makeKeys();
  const keyringFile = path.join(tmpRoot, "keyring.json");
  await fsp.writeFile(keyringFile, JSON.stringify(keyring, null, 2));
  const webhookSecret = `whsec_e2e${crypto.randomBytes(24).toString("hex")}`;

  const mainPersist = path.join(tmpRoot, "d1-test");
  const livePersist = path.join(tmpRoot, "d1-live");
  await migrate(mainPersist, "migrate-test");
  await migrate(livePersist, "migrate-live");

  const [sitePort, siteInspector, livePort, liveInspector] = [await freePort(), await freePort(), await freePort(), await freePort()];
  const siteUrl = `http://127.0.0.1:${sitePort}`;
  const liveUrl = `http://127.0.0.1:${livePort}`;
  const webhookUrl = `${siteUrl}/api/stripe/webhook`;

  const fakeLog = (file) => {
    const stream = fs.createWriteStream(path.join(runDir, file), { flags: "a" });
    closers.push(() => new Promise((r) => stream.end(r)));
    return (line) => stream.write(`${new Date().toISOString()} ${line}\n`);
  };
  const stripeFake = await startStripeFake({ webhookUrl, webhookSecret, log: fakeLog("stripe-fake.txt") });
  closers.push(() => stripeFake.close());
  const resendFake = await startResendFake({ log: fakeLog("resend-fake.txt") });
  closers.push(() => resendFake.close());
  say(`stripe fake ${stripeFake.url}, resend sink ${resendFake.url}`);

  const common = {
    LICENSE_SIGNING_KEY: JSON.stringify(privateJwk),
    LICENSE_PUBLIC_KEYS: JSON.stringify(keyring),
    RESEND_API_KEY: "re_test_e2e",
    RESEND_API_BASE: resendFake.url,
    RESEND_FROM: "PineForge Licensing <enterprise@pineforge.dev>",
    LICENSE_NOTIFY_TO: "enterprise@pineforge.dev",
    STRIPE_API_BASE: stripeFake.url,
    STRIPE_WEBHOOK_SECRET: webhookSecret,
    STRIPE_TAX: "off",
  };
  say("starting wrangler pages dev (test mode and live-key instances)");
  const [site, live] = await Promise.all([
    startSite("site-test", { port: sitePort, inspectorPort: siteInspector, persistDir: mainPersist, bindings: { ...common, STRIPE_SECRET_KEY: "sk_test_e2e", SITE_URL: siteUrl } }),
    startSite("site-live", { port: livePort, inspectorPort: liveInspector, persistDir: livePersist, bindings: { ...common, STRIPE_SECRET_KEY: "sk_live_x", SITE_URL: liveUrl } }),
  ]);
  say(`site ${site.url} (test), ${live.url} (live key)`);

  // A cheap probe that the Functions compiled (contract: GET /api/verify without id -> 400 missing_id).
  try {
    const r = await fetch(`${site.url}/api/verify`);
    const body = await r.text();
    say(`probe GET /api/verify -> ${r.status} ${body.slice(0, 120)}`);
  } catch (err) {
    say(`probe GET /api/verify failed: ${err}`);
  }

  const pwEnv = childEnv({
    E2E_BASE_URL: site.url,
    E2E_LIVE_URL: live.url,
    E2E_STRIPE_URL: stripeFake.url,
    E2E_RESEND_URL: resendFake.url,
    E2E_WEBHOOK_URL: webhookUrl,
    E2E_WEBHOOK_SECRET: webhookSecret,
    E2E_KEYRING_FILE: keyringFile,
    E2E_KID: kid,
    E2E_RUN_ID: runId,
    E2E_SITE_DIR: siteDir,
    E2E_TMP_DIR: tmpRoot,
  });
  say(`npx playwright test -c e2e/playwright.config.mjs ${extraArgs.join(" ")}`);
  const pw = spawn(npx, ["playwright", "test", "-c", "e2e/playwright.config.mjs", ...extraArgs], { cwd: siteDir, env: pwEnv, stdio: "inherit" });
  playwright = pw;
  const code = await new Promise((resolve) => pw.once("exit", (c, s) => resolve(c ?? (s ? 1 : 0))));
  say(`playwright exited ${code}`);
  if (code !== 0) {
    for (const f of [site.logFile, live.logFile]) console.error(`--- ${path.relative(siteDir, f)} (tail) ---\n${await tail(f, 40)}\n`);
  }
  await teardown();
  process.exit(signalled ? SIGNAL_EXIT[signalled] : code);
}

main().catch(async (err) => {
  console.error(`[e2e] FAILED: ${err?.stack ?? err}`);
  await teardown();
  process.exit(1);
});
