import { readFileSync, statSync, lstatSync, unlinkSync, chmodSync } from "node:fs";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { parseExperimentStatus } from "../../../src/lib/trading/experiment-status.ts";
import { parseTradingRuntimeStatus } from "../../../src/lib/trading/runtime-status.ts";

const assets = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/study", ["index.html", "text/html; charset=utf-8"]],
  ["/assets/dashboard.js", ["dashboard.js", "text/javascript; charset=utf-8"]],
  ["/assets/dashboard.css", ["dashboard.css", "text/css; charset=utf-8"]],
].map(([path, [file, type]]) => [path, {type, body: readFileSync(new URL(file, import.meta.url))}]));

function readJson(path, limit) {
  if (statSync(path).size > limit) throw new Error("oversized file");
  const bytes = readFileSync(path);
  if (bytes.length > limit) throw new Error("oversized file");
  return JSON.parse(bytes.toString("utf8"));
}

export function readAccessPolicy(path) {
  try {
    const p = readJson(path, 8192);
    if (p.schema_version !== 1 || typeof p.operator_login !== "string" ||
        !/^[^\s,\x00-\x1f]{1,254}$/.test(p.operator_login) ||
        typeof p.hostname !== "string" || !/^[a-z0-9][a-z0-9.-]+\.ts\.net$/.test(p.hostname) ||
        p.source !== "alpaca" || !/^[a-f0-9]{64}$/.test(p.source_policy_hash) ||
        !/^[a-f0-9]{64}$/.test(p.experiment_identity_hash) ||
        !Number.isFinite(p.experiment_epoch) || p.experiment_epoch <= 0 ||
        p.plan_verified_zero_cost !== true || p.device_access_verified !== true) return null;
    return p;
  } catch { return null; }
}

function oneHeader(req, name) {
  let count = 0;
  for (let i = 0; i < req.rawHeaders.length; i += 2) {
    if (req.rawHeaders[i].toLowerCase() === name) count += 1;
  }
  return count === 1 ? req.headers[name] : undefined;
}

export function authorized(req, policy) {
  const host = oneHeader(req, "host");
  // Serve 1.102.4 preserves Host for TCP proxies. For a Unix socket it sets
  // Host=localhost and overwrites X-Forwarded-Host/Proto with the actual origin.
  const originMatches = !!policy && (host === policy.hostname || (host === "localhost" &&
    oneHeader(req, "x-forwarded-host") === policy.hostname && oneHeader(req, "x-forwarded-proto") === "https"));
  return !!policy && oneHeader(req, "tailscale-user-login") === policy.operator_login &&
    originMatches &&
    (!req.headers.origin || req.headers.origin === `https://${policy.hostname}`) &&
    !["cross-site", "same-site"].includes(req.headers["sec-fetch-site"]);
}

// Explicit projection: never serve a raw snapshot, broker order IDs, news bodies,
// connection metadata, identity configuration, credentials or filesystem paths.
export function statusReport({runtimePath, experimentPath}, policy, now = Date.now()) {
  let runtime = {configured: true, status: "unavailable"};
  let experiment = {status: "unavailable"};
  try {
    const raw = readJson(runtimePath, 1_000_000);
    if (raw.policy_hash !== policy.source_policy_hash || raw.mode !== "demo" ||
        raw.market_source !== policy.source) throw new Error("unexpected source");
    const parsed = parseTradingRuntimeStatus(raw, now);
    const fields = ["status", "mode", "timestamp", "equity", "cash", "startingCash",
      "realizedPnl", "unrealizedPnl", "operatingCosts", "afterOperatingCosts", "feesProvisional",
      "protectionComplete", "entryPause", "entrySessionOpen", "halt", "supervisor",
      "positionCount", "pendingOrderCount", "strategyTrades", "studyStarted", "studyEnd"];
    runtime = Object.fromEntries(fields.filter(k => parsed[k] !== undefined).map(k => [k, parsed[k]]));
    runtime.captureFresh = typeof parsed.timestamp === "string" && now >= Date.parse(parsed.timestamp) &&
      now - Date.parse(parsed.timestamp) <= 30_000;
  } catch { /* Fixed unavailable result; never expose the parser error or path. */ }
  try {
    const raw = readJson(experimentPath, 1_000_000);
    if (raw.policy_hash !== policy.source_policy_hash || raw.identity_hash !== policy.experiment_identity_hash ||
        raw.epoch !== policy.experiment_epoch || raw.market_source !== policy.source) throw new Error("unexpected study");
    experiment = parseExperimentStatus(raw, now);
  } catch { /* Same fail-closed data behavior. */ }
  return {schemaVersion: 1, servedAt: new Date(now).toISOString(), runtime, experiment};
}

export function createDashboard(options) {
  const server = createServer({maxHeaderSize: 8192}, (req, res) => {
    const headers = {
      "Cache-Control": "no-store, max-age=0",
      "Pragma": "no-cache",
      "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "no-referrer",
      "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
      "Strict-Transport-Security": "max-age=31536000",
      "Vary": "Tailscale-User-Login",
    };
    const send = (status, type, body, extra = {}) => {
      res.writeHead(status, {...headers, "Content-Type": type, ...extra});
      res.end(req.method === "HEAD" ? undefined : body);
    };
    const policy = readAccessPolicy(options.accessPath);
    if (!authorized(req, policy)) return send(403, "text/plain; charset=utf-8", "Private dashboard access required.\n");
    if (!["GET", "HEAD"].includes(req.method)) return send(405, "text/plain; charset=utf-8", "Read-only.\n", {Allow: "GET, HEAD"});
    // Exact routes deliberately reject query strings, absolute URLs and traversal.
    if (req.url === "/api/status") {
      return send(200, "application/json; charset=utf-8", JSON.stringify(statusReport(options, policy)));
    }
    const asset = assets.get(req.url);
    if (!asset) return send(404, "text/plain; charset=utf-8", "Not found.\n");
    send(200, asset.type, asset.body);
  });
  server.requestTimeout = 10_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 5_000;
  server.maxRequestsPerSocket = 100;
  server.maxConnections = 32;
  return server;
}

export function listenPrivateSocket(server, socketPath) {
  try {
    if (!lstatSync(socketPath).isSocket()) throw new Error("Socket path is occupied by a non-socket");
    unlinkSync(socketPath);
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  process.umask(0o077);
  server.listen(socketPath, () => chmodSync(socketPath, 0o600));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const server = createDashboard({
    accessPath: process.env.VALOR_DASHBOARD_ACCESS_PATH || "/config/access.json",
    runtimePath: process.env.VALOR_TRADING_SNAPSHOT_PATH || "/runtime/snapshot.json",
    experimentPath: process.env.VALOR_EXPERIMENT_SNAPSHOT_PATH || "/experiment/snapshot.json",
  });
  // There is no TCP listener or container network. Only local Tailscale Serve
  // connects through the owner-only socket mounted into this one container.
  listenPrivateSocket(server, process.env.VALOR_DASHBOARD_SOCKET || "/socket/dashboard.sock");
  const stop = () => { server.close(); server.closeAllConnections(); };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
}
