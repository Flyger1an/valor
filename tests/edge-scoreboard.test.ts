import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import type { PaperPortfolio, PaperTrade } from "@/lib/domain/types";
import { buildEdgeScoreboard } from "@/lib/edge/scoreboard";
import { stepPaperPortfolio } from "@/lib/paper/paper-broker";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { emptyPaperPortfolio } from "@/lib/state/local-store";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("edge scoreboard", () => {
  it("aggregates current signals, open positions, and paper ledger events by family", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const first = stepPaperPortfolio({ signals, risk });
    const second = stepPaperPortfolio({
      signals,
      risk,
      previousPortfolio: first,
      now: new Date("2026-06-22T18:00:00.000Z"),
    });

    const scoreboard = buildEdgeScoreboard({
      signals,
      paper: second,
      updatedAt: sampleMarketData.generatedAt,
    });

    const generatedTotal = scoreboard.rows.reduce(
      (sum, row) => sum + row.generatedCount,
      0,
    );
    const basis = scoreboard.rows.find((row) => row.kind === "spot_perp_basis");

    expect(scoreboard.updatedAt).toBe(sampleMarketData.generatedAt);
    expect(generatedTotal).toBe(signals.length);
    expect(scoreboard.totals.openPositionCount).toBe(second.positions.length);
    expect(scoreboard.totals.ledgerEventCount).toBe(
      second.trades.length + second.rejectedSignals.length,
    );
    expect(basis?.filledCount).toBeGreaterThan(0);
    expect(basis?.heldCount).toBeGreaterThan(0);
    expect(basis?.averageExpectedEdgeBps).toBeGreaterThan(0);
  });

  it("flags a signal family as underperforming when closed paper evidence is net negative", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const paper = withTrade({
      signalId: "spot_perp_basis:BTC-USD:coinbase-spot---binance-perp",
      status: "closed",
      realizedPnlUsd: -42,
    });

    const scoreboard = buildEdgeScoreboard({ signals, paper });
    const basis = scoreboard.rows.find((row) => row.kind === "spot_perp_basis");

    expect(basis?.status).toBe("underperforming");
    expect(basis?.totalPnlUsd).toBe(-42);
    expect(scoreboard.totals.underperformingCount).toBe(1);
  });

  it("marks positive closed evidence as proving", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const paper = withTrade({
      signalId: "funding_carry:ETH-USD:okx-perp",
      status: "closed",
      realizedPnlUsd: 38,
    });

    const scoreboard = buildEdgeScoreboard({ signals, paper });
    const funding = scoreboard.rows.find((row) => row.kind === "funding_carry");

    expect(funding?.status).toBe("proving");
    expect(funding?.winRatePct).toBe(100);
  });

  it("counts rejected signals in acceptance-rate evidence", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const paper = {
      ...emptyPaperPortfolio(),
      rejectedSignals: [
        trade({
          signalId: "cross_exchange_premium:BTC-USD:coinbase--kraken",
          status: "rejected",
        }),
      ],
    };

    const scoreboard = buildEdgeScoreboard({ signals, paper });
    const row = scoreboard.rows.find((entry) => entry.kind === "cross_exchange_premium");

    expect(row?.rejectedCount).toBe(1);
    expect(row?.acceptanceRatePct).toBe(0);
  });
});

function withTrade(input: {
  signalId: string;
  status: PaperTrade["status"];
  realizedPnlUsd?: number;
}): PaperPortfolio {
  return {
    ...emptyPaperPortfolio(),
    trades: [trade(input)],
  };
}

function trade(input: {
  signalId: string;
  status: PaperTrade["status"];
  realizedPnlUsd?: number;
}): PaperTrade {
  return {
    id: `paper-${input.status}-${input.signalId}`,
    signalId: input.signalId,
    timestamp: sampleMarketData.generatedAt,
    assetPair: "BTC/USD",
    venue: "test",
    direction: "long_spot_short_perp",
    notionalUsd: 1000,
    feesUsd: 1,
    status: input.status,
    reason: "scoreboard fixture",
    realizedPnlUsd: input.realizedPnlUsd,
  };
}
