import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadExperimentStatus } from "@/lib/trading/experiment-status";
import { GET } from "@/app/api/ops/trading-experiment/route";

const dirs: string[] = [];
const now = Date.UTC(2026, 9, 5, 13);
function snapshot(changes: Record<string, unknown> = {}) {
  const dir = mkdtempSync(join(tmpdir(), "valor-three-books-"));
  dirs.push(dir);
  const value = { schema_version: 1, mode: "virtual_only", identity_hash: "a".repeat(64),
    epoch: now / 1000 - 10, evaluation_end: now / 1000 - 10 + 90 * 86400,
    timestamp: now / 1000, marks_fresh: true, incremental_model_api_calls: 0, market_source: "test_fixture",
    usable_evidence_blocks: 0, shared_operating_estimate: "0.30", actual_shared_operating_cost: null,
    books: ["henry", "kelly", "baseline"].map(name => ({name, policy_version: `${name}-v1`, starting_cash: "500",
      equity: "499", cash: "474", fee_escrow: "0", net_pnl: "-1", realized_pnl: "-.5", unrealized_pnl: "-.5",
      fees: ".15", exposure: "25", turnover: "50", drawdown: ".002", closed_trades: 1,
      after_allocated_operating_estimate: "-1.1", skips: {below_minimum_no_round_up: 2},
      safety_events: [], sizing: {}, provisional_fees: true})), ...changes };
  const file = join(dir, "snapshot.json");
  writeFileSync(file, JSON.stringify(value));
  return file;
}
afterEach(() => { vi.unstubAllEnvs(); for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });

describe("fictional book reporting", () => {
  it("reports a common epoch, stable names, modeled costs, and unknown actual costs", () => {
    const report = loadExperimentStatus(snapshot(), now);
    expect(report.status).toBe("current");
    expect(report.books?.map(b => b.name)).toEqual(["baseline", "kelly", "henry"]);
    expect(report.sharedOperatingEstimate).toBe(.3);
    expect(report.actualSharedCost).toBeUndefined();
    expect(report.books?.[0].netPnl).toBe(-1);
    expect(report.books?.[0].afterOperatingEstimate).toBe(-1.1);
    expect(report.source).toBe("test_fixture");
  });
  it("never substitutes the broker or legacy account for missing experiment data", () => {
    expect(loadExperimentStatus("").status).toBe("unconfigured");
    expect(loadExperimentStatus("/nonexistent-experiment.json").status).toBe("unavailable");
    expect(loadExperimentStatus(snapshot({ mode: "demo" }), now).status).toBe("unavailable");
  });
  it("marks stale, missing, and future marks as stale", () => {
    expect(loadExperimentStatus(snapshot(), now + 31_000).status).toBe("stale");
    expect(loadExperimentStatus(snapshot({ marks_fresh: false }), now).status).toBe("stale");
    expect(loadExperimentStatus(snapshot({ timestamp: now / 1000 + 1 }), now).status).toBe("stale");
  });
  it("rejects duplicate, nonfinite or unbalanced books and a changed epoch", () => {
    expect(loadExperimentStatus(snapshot({ books: [] }), now).status).toBe("unavailable");
    expect(loadExperimentStatus(snapshot({ evaluation_end: now / 1000 }), now).status).toBe("unavailable");
    expect(loadExperimentStatus(snapshot({ incremental_model_api_calls: 1 }), now).status).toBe("unavailable");
    expect(loadExperimentStatus(snapshot({ usable_evidence_blocks: "NaN" }), now).status).toBe("unavailable");
    for (const change of [{name: "kelly"}, {equity: "900"}, {cash: "-1"}, {cash: "NaN"}]) {
      const path = snapshot();
      const value = JSON.parse(readFileSync(path, "utf8"));
      Object.assign(value.books[0], change);
      writeFileSync(path, JSON.stringify(value));
      expect(loadExperimentStatus(path, now).status).toBe("unavailable");
    }
  });
  it("keeps the API private even when other read APIs are configured public", async () => {
    vi.stubEnv("VALOR_PUBLIC_READ_APIS", "true");
    vi.stubEnv("VALOR_REQUIRE_OPS_AUTH", "false");
    vi.stubEnv("VALOR_OPS_SECRET", "test-only-ops-secret");
    vi.stubEnv("VALOR_SESSION_SECRET", "");
    const response = await GET(new Request("http://localhost/api/ops/trading-experiment"));
    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
});
