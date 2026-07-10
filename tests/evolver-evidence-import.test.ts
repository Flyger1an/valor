import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { loadEvolverEvidenceReport } from "@/lib/evidence/evolver-import";

describe("Evolver evidence import bridge", () => {
  it("reports not configured when no source directory is provided", () => {
    const report = loadEvolverEvidenceReport(undefined, now());

    expect(report.configured).toBe(false);
    expect(report.status).toBe("not_configured");
    expect(report.summary).toContain("VALOR_EVOLVER_EVIDENCE_DIR");
    expect(report.recoveryPlan.status).toBe("not_configured");
    expect(report.recoveryPlan.actions[0]?.code).toBe(
      "configure-evolver-evidence-dir",
    );
  });

  it("summarizes live soak ledgers and flags negative shadow evidence", () => {
    const dir = mkdtempSync(join(tmpdir(), "valor-evolver-evidence-"));
    writeJsonl(
      dir,
      "shadow_analyst_ledger.jsonl",
      Array.from({ length: 12 }, (_, index) => ({
        event: "close",
        entry_ts: Date.UTC(2026, 5, 24 + Math.floor(index / 2), 16, 44),
        closed: `2026-07-${String(Math.min(index + 1, 9)).padStart(2, "0")} 07:20`,
        notional: 10_000,
        shadow_pnl_pct: index < 3 ? 0.002 : -0.004,
        sim_pnl_pct: 0.003,
        converged: index < 4,
      })),
    );
    writeJson(dir, "shadow_analyst_state.json", {
      equity: 95_277.62,
      open: [{ id: "open-1" }, { id: "open-2" }],
    });
    writeJson(dir, "sim_calibration.json", {
      n: 73,
      stated_conf_mean: 0.7906,
      realized_conv_rate: 0.3973,
      mean_divergence_pct: -0.2097,
      conv_scale: 0.6474,
      version: "calib-1783599980-n73",
      updated_epoch: 1783599980,
    });
    writeJsonl(dir, "research_ledger.jsonl", [
      {
        cycle: 1,
        family: "liquidation",
        surfaced: false,
        summary: "below bar",
        ts: "2026-06-24 16:44",
      },
      {
        cycle: 355,
        family: "funding_carry",
        surfaced: false,
        summary: "still below bar",
        ts: "2026-07-09 12:31",
      },
    ]);
    writeJsonl(dir, "fx_research_ledger.jsonl", [
      {
        cycle: 323,
        family: "fx_trend",
        surfaced: false,
        summary: "below bar",
        ts: "2026-07-09 12:43",
      },
    ]);

    const report = loadEvolverEvidenceReport(dir, now());

    expect(report.configured).toBe(true);
    expect(report.status).toBe("blocked");
    expect(report.evidenceDays).toBeGreaterThanOrEqual(15);
    expect(report.totalResearchCycles).toBe(3);
    expect(report.surfacedCandidateCount).toBe(0);
    expect(report.shadow?.closedTradeCount).toBe(12);
    expect(report.shadow?.openPositionCount).toBe(2);
    expect(report.shadow?.reportedPnlUsd).toBeLessThan(0);
    expect(report.calibration?.status).toBe("overconfident");
    expect(report.recoveryPlan.status).toBe("blocked");
    expect(report.recoveryPlan.requiredPnlRecoveryUsd).toBeGreaterThan(4700);
    expect(report.recoveryPlan.additionalEvidenceDays).toBeGreaterThan(0);
    expect(report.recoveryPlan.additionalClosedTrades).toBe(18);
    expect(report.recoveryPlan.winRateGapPct).toBe(25);
    expect(report.recoveryPlan.convergenceRateGapPct).toBeCloseTo(16.7, 1);
    expect(report.recoveryPlan.confidenceHaircutPct).toBeCloseTo(49.7, 1);
    expect(report.recoveryPlan.benchCandidates).toEqual(
      expect.arrayContaining(["funding_carry", "fx_trend", "liquidation"]),
    );
    expect(report.recoveryPlan.actions.map((action) => action.code)).toEqual(
      expect.arrayContaining([
        "extend-evidence-window",
        "collect-shadow-closes",
        "recover-shadow-pnl",
        "repair-shadow-win-rate",
        "repair-shadow-convergence",
        "haircut-calibration-confidence",
        "bench-unsurfaced-families",
      ]),
    );
    expect(report.issues.map((issue) => issue.code)).toEqual(
      expect.arrayContaining([
        "evolver-shadow-negative-pnl",
        "evolver-shadow-win-rate-low",
        "evolver-calibration-overconfident",
        "evolver-no-surfaced-candidates",
      ]),
    );
  });
});

function now() {
  return new Date("2026-07-09T12:50:00.000Z");
}

function writeJsonl(dir: string, filename: string, records: unknown[]) {
  writeFileSync(
    join(dir, filename),
    `${records.map((record) => JSON.stringify(record)).join("\n")}\n`,
  );
}

function writeJson(dir: string, filename: string, record: unknown) {
  writeFileSync(join(dir, filename), JSON.stringify(record, null, 2));
}
