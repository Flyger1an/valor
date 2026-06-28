import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import type { PaperTrade } from "@/lib/domain/types";
import { applyEdgeScoreboardPolicy } from "@/lib/edge/policy";
import { emptyPaperPortfolio } from "@/lib/state/local-store";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("edge policy", () => {
  it("marks underperforming signal families watch-only before paper entry", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const eligibleBasisSignals = signals.filter(
      (signal) => signal.kind === "spot_perp_basis" && signal.eligibleForPaperTrading,
    );
    const result = applyEdgeScoreboardPolicy({
      signals,
      paper: {
        ...emptyPaperPortfolio(),
        trades: [
          trade({
            signalId: eligibleBasisSignals[0].id,
            realizedPnlUsd: -84,
          }),
        ],
      },
      updatedAt: sampleMarketData.generatedAt,
    });
    const basisSignals = result.signals.filter(
      (signal) => signal.kind === "spot_perp_basis",
    );
    const unaffectedEligibleSignals = result.signals.filter(
      (signal) => signal.kind !== "spot_perp_basis" && signal.eligibleForPaperTrading,
    );

    expect(result.decision.blockedKinds).toEqual(["spot_perp_basis"]);
    expect(result.decision.blockedSignalCount).toBe(eligibleBasisSignals.length);
    expect(basisSignals.every((signal) => !signal.eligibleForPaperTrading)).toBe(true);
    expect(basisSignals.every((signal) => signal.direction === "watch_only")).toBe(true);
    expect(basisSignals.every((signal) => signal.edgePolicy?.source === "edge_scoreboard")).toBe(true);
    expect(basisSignals[0].explanation).toContain("Edge policy:");
    expect(result.scoreboard.rows.find((row) => row.kind === "spot_perp_basis")?.paperEligibleCount).toBe(0);
    expect(unaffectedEligibleSignals.length).toBeGreaterThan(0);
  });
});

function trade(input: {
  signalId: string;
  realizedPnlUsd: number;
}): PaperTrade {
  return {
    id: `paper-close-${input.signalId}`,
    signalId: input.signalId,
    timestamp: sampleMarketData.generatedAt,
    assetPair: "BTC/USD",
    venue: "test",
    direction: "long_spot_short_perp",
    notionalUsd: 1000,
    feesUsd: 1,
    status: "closed",
    reason: "edge policy fixture",
    realizedPnlUsd: input.realizedPnlUsd,
  };
}
