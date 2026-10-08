import {afterEach, test} from "node:test";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtempSync, writeFileSync, readFileSync, renameSync, rmSync, statSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {request} from "node:http";
import {createDashboard, listenPrivateSocket, readAccessPolicy, statusReport} from "../infra/trading/private-dashboard/server.mjs";

const dirs = [], servers = [];
const now = Date.now(), epoch = now / 1000 - 100;
const policy = {schema_version: 1, operator_login: "owner@example.invalid", hostname: "monitor.example.ts.net",
  source: "alpaca", source_policy_hash: "a".repeat(64), experiment_identity_hash: "b".repeat(64),
  experiment_epoch: epoch, plan_verified_zero_cost: true, device_access_verified: true};
const headers = {Host: policy.hostname, "Tailscale-User-Login": policy.operator_login};
function fixture() {
  const dir = mkdtempSync(join(tmpdir(), "valor-dashboard-")); dirs.push(dir);
  const options = {accessPath: join(dir, "access.json"), runtimePath: join(dir, "runtime.json"), experimentPath: join(dir, "experiment.json")};
  const runtime = {schema_version: 1, mode: "demo", market_source: "alpaca", policy_hash: policy.source_policy_hash,
    timestamp: now / 1000, broker_reconciled_at: now / 1000, marks_fresh: true,
    quotes: {"BTC-USD": {timestamp: now / 1000}}, equity: "500", cash: "500", starting_cash: "500", realized_pnl: "0",
    positions: [], pending_orders: [], supervisor: {expires_at: 0, action: "pause_entries"},
    accidental_secret: "must-never-leak", news: {items: [{headline: "private raw news", url: "https://example.invalid"}]}};
  const experiment = {schema_version: 1, mode: "virtual_only", identity_hash: policy.experiment_identity_hash,
    policy_hash: policy.source_policy_hash, epoch, evaluation_end: epoch + 90 * 86400,
    timestamp: now / 1000, market_source: "alpaca", incremental_model_api_calls: 0,
    marks_fresh: false, usable_evidence_blocks: 0, frames: 2, accidental_secret: "must-never-leak",
    books: ["baseline", "kelly", "henry"].map(name => ({name, starting_cash: "500", equity: "500", cash: "500",
      net_pnl: "0", realized_pnl: "0", unrealized_pnl: "0", fees: "0", exposure: "0", turnover: "0",
      drawdown: "0", fee_escrow: "0", closed_trades: 0, skips: {}, sizing: {}, safety_events: [], policy_version: name + "-v1"}))};
  writeFileSync(options.accessPath, JSON.stringify(policy));
  writeFileSync(options.runtimePath, JSON.stringify(runtime));
  writeFileSync(options.experimentPath, JSON.stringify(experiment));
  return {options, runtime, experiment};
}
async function start(options) {
  const server = createDashboard(options); servers.push(server);
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return server;
}
function get(server, path = "/api/status", extra = {}) {
  return new Promise((resolve, reject) => {
    const req = request({hostname: "127.0.0.1", port: server.address().port, path, headers, ...extra}, res => {
      const chunks = []; res.on("data", c => chunks.push(c));
      res.on("end", () => resolve({status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString()}));
    }); req.on("error", reject); req.end();
  });
}
afterEach(async () => {
  await Promise.all(servers.splice(0).map(s => new Promise(resolve => {s.close(resolve); s.closeAllConnections();})));
  dirs.splice(0).forEach(d => rmSync(d, {recursive: true, force: true}));
});

test("fails closed until both plan and device access are verified", async () => {
  const {options} = fixture(); const server = await start(options);
  for (const changed of [{}, {...policy, plan_verified_zero_cost: false}, {...policy, device_access_verified: false}, {...policy, operator_login: ""}]) {
    writeFileSync(options.accessPath, JSON.stringify(changed));
    assert.equal(readAccessPolicy(options.accessPath), null);
    assert.equal((await get(server)).status, 403);
  }
});
test("rejects missing, other, duplicate and cross-origin identities on every route", async () => {
  const {options} = fixture(); const server = await start(options);
  for (const supplied of [{Host: policy.hostname}, {...headers, "Tailscale-User-Login": "other@example.invalid"},
    {...headers, "Tailscale-User-Login": [policy.operator_login, policy.operator_login]},
    {...headers, Host: "unapproved.example.invalid"}, {...headers, Origin: "https://attacker.invalid"},
    {...headers, "Sec-Fetch-Site": "cross-site"}]) {
    for (const path of ["/study", "/api/status", "/assets/dashboard.js", "/unknown"]) {
      const response = await get(server, path, {headers: supplied});
      assert.equal(response.status, 403); assert.ok(!response.body.includes("500"));
    }
  }
});
test("serves only the sanitized projection with no-store and restrictive browser headers", async () => {
  const {options} = fixture(); const response = await get(await start(options));
  assert.equal(response.status, 200);
  const d = JSON.parse(response.body);
  assert.equal(d.experiment.books.length, 3); assert.equal(d.runtime.mode, "demo");
  assert.equal(d.experiment.captureFresh, true); assert.equal(d.experiment.marksFresh, false);
  for (const privateValue of ["must-never-leak", "private raw news", policy.operator_login, policy.source_policy_hash, options.runtimePath]) assert.ok(!response.body.includes(privateValue));
  assert.match(response.headers["cache-control"], /no-store/);
  assert.match(response.headers["content-security-policy"], /frame-ancestors 'none'/);
  assert.equal(response.headers["access-control-allow-origin"], undefined);
});
test("rejects every write method and arbitrary path without changing the snapshots", async () => {
  const {options} = fixture(); const server = await start(options);
  const digest = () => createHash("sha256").update(readFileSync(options.runtimePath)).update(readFileSync(options.experimentPath)).digest("hex");
  const before = digest();
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"]) assert.equal((await get(server, "/api/status", {method})).status, 405);
  for (const path of ["/config/access.json", "/api/ops/resume", "/../runtime/snapshot.json", "/%2e%2e/runtime/snapshot.json", "/api/status?path=/etc/passwd"]) assert.equal((await get(server, path)).status, 404);
  const head = await get(server, "/study", {method: "HEAD"}); assert.equal(head.status, 200); assert.equal(head.body, "");
  assert.equal(digest(), before);
});
test("rejects live, fixture and foreign-identity data and malformed balances", () => {
  const {options, runtime, experiment} = fixture();
  for (const changed of [{...runtime, mode: "live"}, {...runtime, policy_hash: "f".repeat(64)}, {...runtime, market_source: "test_fixture"}]) {
    writeFileSync(options.runtimePath, JSON.stringify(changed)); assert.equal(statusReport(options, policy, now).runtime.status, "unavailable");
  }
  for (const changed of [{...experiment, epoch: epoch + 1}, {...experiment, identity_hash: "f".repeat(64)},
    {...experiment, market_source: "test_fixture"}, {...experiment, books: [{...experiment.books[0], equity: "900"}, ...experiment.books.slice(1)]}]) {
    writeFileSync(options.experimentPath, JSON.stringify(changed)); assert.equal(statusReport(options, policy, now).experiment.status, "unavailable");
  }
});
test("retains original freshness and sees atomic snapshot updates across dashboard restart", async () => {
  const {options, experiment} = fixture(); let server = await start(options);
  assert.equal(statusReport(options, policy, now + 31_000).experiment.captureFresh, false);
  const updated = {...experiment, frames: 3};
  writeFileSync(options.experimentPath + ".tmp", JSON.stringify(updated)); renameSync(options.experimentPath + ".tmp", options.experimentPath);
  assert.equal(JSON.parse((await get(server)).body).experiment.frames, 3);
  const before = readFileSync(options.experimentPath, "utf8");
  await new Promise(resolve => server.close(resolve)); servers.splice(servers.indexOf(server), 1);
  server = await start(options);
  const d = JSON.parse((await get(server)).body).experiment;
  assert.equal(d.frames, 3); assert.equal(d.epoch, new Date(epoch * 1000).toISOString()); assert.equal(d.books[0].equity, 500);
  assert.equal(readFileSync(options.experimentPath, "utf8"), before);
});
test("serves over an owner-only Unix socket and validates Serve’s rewritten origin", async () => {
  const {options} = fixture(); const path = join(dirs.at(-1), "dashboard.sock");
  const server = createDashboard(options); servers.push(server);
  const ready = new Promise(resolve => server.once("listening", resolve)); listenPrivateSocket(server, path); await ready;
  assert.equal(statSync(path).mode & 0o777, 0o600);
  const fromServe = {Host: "localhost", "Tailscale-User-Login": policy.operator_login,
    "X-Forwarded-Host": policy.hostname, "X-Forwarded-Proto": "https", Origin: `https://${policy.hostname}`};
  assert.equal((await get(server, "/api/status", {socketPath: path, headers: fromServe})).status, 200);
  for (const supplied of [{...fromServe, "X-Forwarded-Host": "attacker.invalid"}, {...fromServe, "X-Forwarded-Proto": "http"}]) {
    assert.equal((await get(server, "/api/status", {socketPath: path, headers: supplied})).status, 403);
  }
});

function henrySnapshot(overrides = {}) {
  return {book: "henry_v2", mode: "virtual_only", rules_version: "henry-raging-bull-v1", identity_hash: "c".repeat(64),
    epoch: now / 1000 - 60, timestamp: now / 1000, frames: 12, halt: "", floor_breached: false, equity_floor_usd: "250",
    cash_usd: "250.00", equity_usd: "512.40", net_pnl_usd: "12.40", return_pct: "2.48", peak_equity_usd: "515.00",
    max_drawdown_pct: "0.51", fees_paid_usd: "1.25", closed_trades: 2, win_rate_pct: "50.0", avg_win_usd: "9.10",
    avg_loss_usd: "-2.30", payoff_ratio: "3.96", best_trade_usd: "9.10", worst_trade_usd: "-2.30", pressed_trades: 1,
    position: {symbol: "SKY-USD", strategy: "breakout@be6c54d2010f", quantity: "3100", avg_price: "0.0802", stop: "0.0790",
      high_water: "0.0815", pressed: true, accidental_secret: "must-never-leak"},
    pending: null, last_decision: {action: "press", symbol: "SKY-USD", reason: "up one stop, trend intact", raw: "must-never-leak"},
    recent_trades: [{symbol: "ADA-USD", strategy: "ema_trend@6ad91594a2f1", pnl_usd: "-2.30", pressed: false, fills: "must-never-leak"}],
    skips: {"must-never-leak": 1}, accidental_secret: "must-never-leak", ...overrides};
}

test("Henry v2 is projected explicitly and never leaks raw fields", () => {
  const {options} = fixture();
  options.henryPath = join(dirs.at(-1), "henry.json");
  writeFileSync(options.henryPath, JSON.stringify(henrySnapshot()));
  const report = statusReport(options, policy, now);
  const h = report.henryV2;
  assert.equal(h.status, "running");
  assert.equal(h.equity, 512.4);
  assert.equal(h.returnPct, 2.48);
  assert.equal(h.payoffRatio, 3.96);
  assert.equal(h.position.symbol, "SKY-USD");
  assert.equal(h.position.pressed, true);
  assert.equal(h.captureFresh, true);
  assert.match(h.lastDecision, /press · SKY-USD/);
  assert.equal(h.recentTrades[0].pnl, -2.3);
  assert.ok(!JSON.stringify(report).includes("must-never-leak"));
  assert.equal(report.experiment.books?.length ?? 3, 3);  // the existing studies are unaffected
});

test("Henry v2 missing reads not_deployed; malformed fails closed", () => {
  const {options} = fixture();
  options.henryPath = join(dirs.at(-1), "absent.json");
  assert.equal(statusReport(options, policy, now).henryV2.status, "not_deployed");
  writeFileSync(options.henryPath, JSON.stringify(henrySnapshot({mode: "live"})));
  assert.deepEqual(statusReport(options, policy, now).henryV2, {status: "unavailable"});
  writeFileSync(options.henryPath, "{not json");
  assert.deepEqual(statusReport(options, policy, now).henryV2, {status: "unavailable"});
  delete options.henryPath;
  assert.equal(statusReport(options, policy, now).henryV2.status, "not_deployed");
});

test("halted or floor-breached Henry v2 reports its stop", () => {
  const {options} = fixture();
  options.henryPath = join(dirs.at(-1), "henry.json");
  writeFileSync(options.henryPath, JSON.stringify(henrySnapshot({halt: "equity_floor", floor_breached: true, position: null})));
  const h = statusReport(options, policy, now).henryV2;
  assert.equal(h.status, "halted");
  assert.equal(h.floorBreached, true);
  assert.equal(h.position, null);
});
