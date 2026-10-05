import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadTradingRuntimeStatus } from "@/lib/trading/runtime-status";

const dirs: string[] = [];
const now = Date.UTC(2026, 9, 4, 18);
function snapshot(changes = {}) {
  const dir = mkdtempSync(join(tmpdir(), "valor-study-status-"));
  dirs.push(dir);
  const file = join(dir, "snapshot.json");
  writeFileSync(file, JSON.stringify({ schema_version: 1, mode: "paper", policy_hash: "a".repeat(64),
    timestamp: now / 1000, broker_reconciled_at: now / 1000, marks_fresh: true, starting_cash: "500",
    equity: "502", cash: "477", realized_pnl: "1", positions: [{}], pending_orders: [],
    quotes: { "BTC-USD": { timestamp: now / 1000 } },
    supervisor: { action: "resume_entries", expires_at: now / 1000 + 60 },
    model_usage: { estimated_cost_usd: "0.25" }, ...changes }));
  return file;
}
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });

describe("isolated execution ledger monitor", () => {
  it("reports actual runtime mode and subtracts model costs without using legacy demo capital", () => {
    const report = loadTradingRuntimeStatus(snapshot(), now);
    expect(report.status).toBe("current");
    expect(report.mode).toBe("paper");
    expect(report.afterModelCosts).toBe(1.75);
    expect(report.realizedPnl).toBe(1);
    expect(report.unrealizedPnl).toBe(1);
    expect(report.studyStarted).toBeUndefined();
    expect(report.studyEnd).toBeUndefined();
  });
  it("marks stale and future snapshots as stale, and missing accounts unavailable", () => {
    expect(loadTradingRuntimeStatus(snapshot({ timestamp: now / 1000 - 31 }), now).status).toBe("stale");
    expect(loadTradingRuntimeStatus(snapshot({ timestamp: now / 1000 + 1 }), now).status).toBe("stale");
    expect(loadTradingRuntimeStatus("/nonexistent-study-file", now).status).toBe("unavailable");
  });
  it("rejects invalid modes and nonfinite balances", () => {
    expect(loadTradingRuntimeStatus(snapshot({ mode: "profitable-live" }), now).status).toBe("unavailable");
    expect(loadTradingRuntimeStatus(snapshot({ equity: "NaN" }), now).status).toBe("unavailable");
  });
  it("shows a blocked model without pretending the paper phase started the live clock", () => {
    const report = loadTradingRuntimeStatus(snapshot({study_started_at: null, study_deadline: null,
      model_usage: {estimated_cost_usd: "0.001", model_connection: {status: "blocked", http_status: 401}}}), now);
    expect(report.modelConnection).toBe("blocked");
    expect(report.studyStarted).toBeUndefined();
  });
  it("separates provisional fees, operating expenses and deposits from trading results", () => {
    const report = loadTradingRuntimeStatus(snapshot({mode: "demo", equity: "602", realized_pnl: "1",
      accounting: {net_cash_flows: "100", fees_provisional: true},
      costs: {total_operating_estimated_usd: "0.5", experiment_pnl_estimated_usd: "1.5"},
      readiness: {strategy_round_trips: 0, active_strategy_sessions: 0, fee_posting_days: [], engineering_drill: {status: "passed"}},
      protection: {complete: true}}), now);
    expect(report.unrealizedPnl).toBe(1);
    expect(report.afterModelCosts).toBe(1.75);
    expect(report.afterOperatingCosts).toBe(1.5);
    expect(report.feesProvisional).toBe(true);
    expect(report.demoDrill).toBe("passed");
    expect(report.strategyTrades).toBe(0);
  });
  it("shows news provenance and entry hours while rejecting unsafe headline links", () => {
    const report = loadTradingRuntimeStatus(snapshot({entry_session_open: false,
      news: {status: "current", fetched_at: now / 1000, items: [
        {headline: "Central bank update", source: "federal_reserve", url: "https://www.federalreserve.gov/newsevents/"},
        {headline: "Unsafe link", source: "invalid", url: "javascript:alert(1)"}]} }), now);
    expect(report.entrySessionOpen).toBe(false);
    expect(report.newsStatus).toBe("current");
    expect(report.newsHeadlines).toHaveLength(1);
    expect(report.newsHeadlines?.[0].source).toBe("federal_reserve");
  });
});
