import { describe, expect, it } from "vitest";
import type { EvolverEvidenceReport } from "@/lib/domain/types";
import {
  appendEvolverRecoverySnapshot,
  benchedSignalKindsFromEvidence,
  evaluateEvolverRecoveryWatchdog,
} from "@/lib/evidence/evolver-watchdog";

describe("Evolver recovery watchdog", () => {
  it("dedupes identical snapshots and reports recovery trend", () => {
    const older = report({
      generatedAt: "2026-07-09T00:00:00.000Z",
      requiredPnlRecoveryUsd: 5_000,
      additionalEvidenceDays: 7,
      winRateGapPct: 8,
      convergenceRateGapPct: 12,
    });
    const current = report({
      generatedAt: "2026-07-10T00:00:00.000Z",
      requiredPnlRecoveryUsd: 4_000,
      additionalEvidenceDays: 6,
      winRateGapPct: 5,
      convergenceRateGapPct: 10,
    });

    const first = appendEvolverRecoverySnapshot(undefined, older);
    const second = appendEvolverRecoverySnapshot(first.snapshots, current);
    const duplicate = appendEvolverRecoverySnapshot(second.snapshots, current);
    const watchdog = evaluateEvolverRecoveryWatchdog(current, duplicate.snapshots);

    expect(first.appended).toBe(true);
    expect(second.appended).toBe(true);
    expect(duplicate.appended).toBe(false);
    expect(duplicate.snapshots).toHaveLength(2);
    expect(watchdog.posture).toBe("improving");
    expect(watchdog.previous?.generatedAt).toBe(older.generatedAt);
    expect(
      watchdog.metrics.find((metric) => metric.key === "required_pnl_recovery_usd")
        ?.direction,
    ).toBe("improved");
  });

  it("surfaces exact v0.2 signal-kind matches for bench guard", () => {
    const evidence = report({
      benchCandidates: ["funding_carry", "fx_trend", "vol_premium"],
    });
    const watchdog = evaluateEvolverRecoveryWatchdog(evidence, [
      appendEvolverRecoverySnapshot(undefined, evidence).current,
    ]);

    expect(benchedSignalKindsFromEvidence(evidence)).toEqual(["funding_carry"]);
    expect(watchdog.benchGuard.active).toBe(true);
    expect(watchdog.benchGuard.matchingSignalKinds).toEqual(["funding_carry"]);
  });
});

function report(
  overrides: Partial<{
    generatedAt: string;
    requiredPnlRecoveryUsd: number;
    additionalEvidenceDays: number;
    winRateGapPct: number;
    convergenceRateGapPct: number;
    benchCandidates: string[];
  }> = {},
): EvolverEvidenceReport {
  const generatedAt = overrides.generatedAt ?? "2026-07-10T00:00:00.000Z";
  return {
    id: `evolver-evidence:${generatedAt}`,
    generatedAt,
    status: "blocked",
    configured: true,
    sourceLabel: "test-evolver",
    summary: "blocked imported evidence",
    evidenceDays: 16,
    firstTimestamp: "2026-06-24T00:00:00.000Z",
    lastTimestamp: "2026-07-10T00:00:00.000Z",
    totalResearchCycles: 100,
    surfacedCandidateCount: 0,
    researchLoops: [],
    issues: [],
    recoveryPlan: {
      status: "blocked",
      summary: "blocked recovery fixture",
      minimumEvidenceDays: 21,
      additionalEvidenceDays: overrides.additionalEvidenceDays ?? 5,
      minimumClosedTrades: 30,
      additionalClosedTrades: 0,
      minimumWinRatePct: 50,
      winRateGapPct: overrides.winRateGapPct ?? 4,
      minimumConvergenceRatePct: 50,
      convergenceRateGapPct: overrides.convergenceRateGapPct ?? 9,
      requiredPnlRecoveryUsd: overrides.requiredPnlRecoveryUsd ?? 4_500,
      confidenceHaircutPct: 40,
      benchCandidates: overrides.benchCandidates ?? ["shadow_analyst"],
      actions: [
        {
          code: "recover-shadow-pnl",
          severity: "critical",
          title: "Recover shadow PnL",
          current: "-$4499.00",
          target: "$1.00 or better",
          gap: "$4500.00 net recovery",
          rationale: "Negative imported shadow PnL blocks promotion.",
        },
      ],
    },
  };
}
