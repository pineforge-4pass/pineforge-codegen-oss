#!/usr/bin/env node
// Local email sink standing in for Resend in the E2E suite: `POST /emails` is accepted like Resend's
// API (Bearer key required), stored in memory and answered with `{ id }`. Nothing is sent anywhere.
//
// Standalone:  node e2e/fakes/resend.mjs --port 0        (or env FAKE_RESEND_PORT)
// Module:      const sink = await startResendFake({ port }); sink.url; await sink.close();
//
// Control:     GET  /__control/health
//              GET  /__control/emails[?to=<address>]  -> { emails: [{ id, created_at, from, to, subject, text, html, reply_to, attachments }] }
//              POST /__control/fail-next { count, status?, to? } -> the next `count` POST /emails (only those
//                   addressed to `to`, when given) answer `status` (default 503) and are not stored
//              GET  /__control/failed  -> { failed: [{ at, status, to, subject }] }
import http from "node:http";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";

function sendJson(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

const asList = (v) => (v === undefined || v === null ? [] : Array.isArray(v) ? v : [v]);

export async function startResendFake(options = {}) {
  const host = options.host ?? "127.0.0.1";
  const log = options.log ?? (() => {});
  const emails = [];
  const failRules = [];
  const failed = [];
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url ?? "/", "http://fake.invalid");
      if (req.method === "POST" && url.pathname === "/emails") {
        const auth = req.headers.authorization ?? "";
        if (!/^Bearer \S+/.test(auth)) {
          return sendJson(res, 401, { statusCode: 401, name: "missing_api_key", message: "Missing API key in the authorization header." });
        }
        let body;
        try {
          body = JSON.parse(await readBody(req));
        } catch {
          return sendJson(res, 422, { statusCode: 422, name: "validation_error", message: "Invalid JSON body." });
        }
        const missing = ["from", "to", "subject"].filter((k) => !body?.[k] || (Array.isArray(body[k]) && body[k].length === 0));
        if (missing.length) {
          return sendJson(res, 422, { statusCode: 422, name: "validation_error", message: `Missing \`${missing[0]}\` field.` });
        }
        if (!body.text && !body.html) {
          return sendJson(res, 422, { statusCode: 422, name: "validation_error", message: "Missing `text` or `html` field." });
        }
        const to = asList(body.to).map((t) => String(t).toLowerCase());
        const rule = failRules.find((r) => r.remaining > 0 && (!r.to || to.some((t) => t.includes(r.to))));
        if (rule) {
          rule.remaining -= 1;
          failed.push({ at: new Date().toISOString(), status: rule.status, to: body.to, subject: body.subject });
          log(`failing (fail-next) to=${to.join(",")} subject=${JSON.stringify(body.subject)} -> ${rule.status}`);
          return sendJson(res, rule.status, { statusCode: rule.status, name: "application_error", message: "Simulated failure (test control fail-next)." });
        }
        const id = crypto.randomUUID();
        emails.push({ id, created_at: new Date().toISOString(), idempotencyKey: req.headers["idempotency-key"] ?? null, ...body });
        log(`stored ${id} to=${asList(body.to).join(",")} subject=${JSON.stringify(body.subject)}`);
        return sendJson(res, 200, { id });
      }
      if (req.method === "GET" && url.pathname === "/__control/health") return sendJson(res, 200, { ok: true });
      if (req.method === "POST" && url.pathname === "/__control/fail-next") {
        let body;
        try {
          body = JSON.parse((await readBody(req)) || "{}");
        } catch {
          return sendJson(res, 400, { error: "invalid_json" });
        }
        const count = Number(body.count ?? 1);
        const status = Number(body.status ?? 503);
        if (!Number.isInteger(count) || count < 1 || !Number.isInteger(status) || status < 400 || status > 599) {
          return sendJson(res, 400, { error: "count >= 1 and a 4xx/5xx status required" });
        }
        failRules.push({ remaining: count, status, to: body.to ? String(body.to).toLowerCase() : null });
        return sendJson(res, 200, { ok: true, pending: failRules.filter((r) => r.remaining > 0).length });
      }
      if (req.method === "GET" && url.pathname === "/__control/failed") return sendJson(res, 200, { failed });
      if (req.method === "GET" && url.pathname === "/__control/emails") {
        const to = url.searchParams.get("to");
        const list = to ? emails.filter((e) => asList(e.to).some((t) => String(t).toLowerCase().includes(to.toLowerCase()))) : emails;
        return sendJson(res, 200, { emails: list });
      }
      return sendJson(res, 404, { statusCode: 404, name: "not_found", message: "The requested endpoint does not exist." });
    } catch (err) {
      log(`error: ${err?.stack ?? err}`);
      if (!res.headersSent) sendJson(res, 500, { statusCode: 500, name: "application_error", message: String(err?.message ?? err) });
      else res.end();
    }
  });
  server.keepAliveTimeout = 1000;
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(Number(options.port ?? 0), host, resolve);
  });
  const { port } = server.address();
  return {
    url: `http://${host}:${port}`,
    port,
    emails,
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const i = process.argv.indexOf("--port");
  const sink = await startResendFake({
    port: i >= 0 ? process.argv[i + 1] : (process.env.FAKE_RESEND_PORT ?? 0),
    log: (line) => console.log(`[resend-fake] ${line}`),
  });
  console.log(`[resend-fake] listening on ${sink.url}`);
  const stop = () => sink.close().then(() => process.exit(0));
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}
