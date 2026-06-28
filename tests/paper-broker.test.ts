import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import type {
  MarketDataBundle,
  MarketRiskState,
  RelativeValueSignal,
} from "@/lib/domain/types";
import { applyEdgeScoreboardPolicy } from "@/lib/edge/policy";
import { simulatePaperPortfolio, stepPaperPortfolio } from "@/lib/paper/paper-broker";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { emptyPaperPortfolio } from "@/lib/state/local-store";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("paper broker", () => {
  it("simulates trades from eligible signals under sample risk limits", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const portfolio = simulatePaperPortfolio({ signals, risk });

    expect(portfolio.trades.length).toBeGreaterThan(0);
    expect(portfolio.positions.length).toBe(portfolio.trades.length);
    expect(portfolio.equityUsd).toBeGreaterThan(0);
  });

  it("blocks new paper trades in Black risk state", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk: MarketRiskState = {
      ...evaluateMarketRisk(sampleMarketData),
      state: "Black",
    };
    const portfolio = simulatePaperPortfolio({ signals, risk });

    expect(portfolio.trades).toHaveLength(0);
    expect(portfolio.rejectedSignals.length).toBeGreaterThan(0);
  });

  it("blocks new paper trades when data quality blocks entries", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const dataQuality = evaluateDataQuality(
      {
        ...sampleMarketData,
        markets: [
          {
            ...sampleMarketData.markets[0],
            orderBook: {
              ...sampleMarketData.markets[0].orderBook,
              bid: sampleMarketData.markets[0].orderBook.ask + 1,
              ask: sampleMarketData.markets[0].orderBook.bid - 1,
            },
          },
          ...sampleMarketData.markets.slice(1),
        ],
      },
      {
        connectorId: "sample-fixtures",
        connectorLabel: "Deterministic sample market bundle",
        mode: "sample",
        assessedAt: sampleMarketData.generatedAt,
      },
    );
    const portfolio = simulatePaperPortfolio({ signals, risk, dataQuality });

    expect(portfolio.trades).toHaveLength(0);
    expect(portfolio.rejectedSignals.length).toBeGreaterThan(0);
    expect(portfolio.rejectedSignals[0].reason).toContain("Data quality blocked");
  });

  it("blocks new paper trades when system trust blocks paper mode", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const dataQuality = evaluateDataQuality(sampleMarketData, {
      connectorId: "sample-fixtures",
      connectorLabel: "Deterministic sample market bundle",
      mode: "sample",
      assessedAt: sampleMarketData.generatedAt,
    });
    const systemTrust = evaluateSystemTrust({
      dataQuality,
      risk,
      killSwitch: {
        active: true,
        reason: "manual halt",
        activatedAt: sampleMarketData.generatedAt,
        activatedBy: "test",
        dashboardResetRequired: true,
      },
      paper: emptyPaperPortfolio(),
      now: new Date(sampleMarketData.generatedAt),
    });
    const portfolio = simulatePaperPortfolio({
      signals,
      risk,
      dataQuality,
      systemTrust,
    });

    expect(portfolio.trades).toHaveLength(0);
    expect(portfolio.rejectedSignals.length).toBeGreaterThan(0);
    expect(portfolio.rejectedSignals[0].reason).toContain("System trust blocked");
  });

  it("holds and marks existing positions across ledger steps", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const first = stepPaperPortfolio({ signals, risk });
    const second = stepPaperPortfolio({
      signals,
      risk,
      previousPortfolio: first,
      now: new Date("2026-06-22T18:00:00.000Z"),
    });

    expect(second.positions.length).toBe(first.positions.length);
    expect(second.trades[0].status).toBe("held");
    expect(second.positions[0].holdingHours).toBeGreaterThan(0);
    expect(second.positions[0].lastMarkedAt).toBe("2026-06-22T18:00:00.000Z");
  });

  it("marks existing positions from current market prices when available", () => {
    const signal = fundingSignal();
    const risk = evaluateMarketRisk(sampleMarketData);
    const first = stepPaperPortfolio({
      signals: [signal],
      risk,
      marketData: withOkxBtcPerpMark(100),
    });
    const second = stepPaperPortfolio({
      signals: [signal],
      risk,
      previousPortfolio: first,
      marketData: withOkxBtcPerpMark(90),
      now: new Date("2026-06-22T18:00:00.000Z"),
    });

    expect(first.positions[0].markSource).toBe("market_price");
    expect(first.positions[0].entryReferencePrice).toBe(100);
    expect(second.positions[0].markSource).toBe("market_price");
    expect(second.positions[0].currentReferencePrice).toBe(90);
    expect(second.positions[0].markPnlUsd).toBeGreaterThan(first.positions[0].markPnlUsd);
    expect(second.trades[0].reason).toContain("market reference price");
  });

  it("closes existing positions when edge decays", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const first = stepPaperPortfolio({ signals, risk });
    const decayedSignalId = first.positions[0].signalId;
    const decayedSignals = signals.map((signal) =>
      signal.id === decayedSignalId
        ? {
            ...signal,
            expectedEdgeBps: 1,
            opportunityScore: 1,
          }
        : signal,
    );
    const second = stepPaperPortfolio({
      signals: decayedSignals,
      risk,
      previousPortfolio: first,
      now: new Date("2026-06-22T18:00:00.000Z"),
    });

    expect(second.positions.some((position) => position.signalId === decayedSignalId)).toBe(false);
    expect(second.trades.some((trade) => trade.status === "closed")).toBe(true);
    expect(second.trades[0].reason).toContain("edge decayed");
  });

  it("closes existing positions when edge policy marks their family watch-only", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const first = stepPaperPortfolio({ signals, risk });
    const basisPosition = first.positions.find(
      (position) => position.signalKind === "spot_perp_basis",
    );
    expect(basisPosition).toBeDefined();
    const paperWithBadEvidence = {
      ...first,
      trades: [
        {
          id: `paper-close-${basisPosition!.signalId}-bad-evidence`,
          signalId: basisPosition!.signalId,
          timestamp: sampleMarketData.generatedAt,
          assetPair: basisPosition!.assetPair,
          venue: basisPosition!.venue,
          direction: basisPosition!.direction,
          notionalUsd: basisPosition!.notionalUsd,
          feesUsd: 0,
          status: "closed" as const,
          reason: "scoreboard fixture",
          realizedPnlUsd: -10_000,
        },
        ...first.trades,
      ],
    };
    const policy = applyEdgeScoreboardPolicy({
      signals,
      paper: paperWithBadEvidence,
      updatedAt: sampleMarketData.generatedAt,
    });
    const second = stepPaperPortfolio({
      signals: policy.signals,
      risk,
      previousPortfolio: paperWithBadEvidence,
      now: new Date("2026-06-22T18:00:00.000Z"),
    });
    const closedTrade = second.trades.find(
      (trade) => trade.status === "closed" && trade.signalId === basisPosition!.signalId,
    );

    expect(policy.decision.blockedKinds).toContain("spot_perp_basis");
    expect(second.positions.some((position) => position.id === basisPosition!.id)).toBe(false);
    expect(closedTrade?.reason).toContain("Edge policy:");
  });

  it("closes existing positions and rejects new entries when data quality blocks entries", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const first = stepPaperPortfolio({ signals, risk });
    const dataQuality = evaluateDataQuality(
      {
        ...sampleMarketData,
        markets: [
          {
            ...sampleMarketData.markets[0],
            orderBook: {
              ...sampleMarketData.markets[0].orderBook,
              bid: sampleMarketData.markets[0].orderBook.ask + 1,
              ask: sampleMarketData.markets[0].orderBook.bid - 1,
            },
          },
          ...sampleMarketData.markets.slice(1),
        ],
      },
      {
        connectorId: "sample-fixtures",
        connectorLabel: "Deterministic sample market bundle",
        mode: "sample",
        assessedAt: sampleMarketData.generatedAt,
      },
    );
    const second = stepPaperPortfolio({
      signals,
      risk,
      previousPortfolio: first,
      dataQuality,
      now: new Date("2026-06-22T18:00:00.000Z"),
    });

    expect(second.positions).toHaveLength(0);
    expect(second.trades.some((trade) => trade.status === "closed")).toBe(true);
    expect(second.rejectedSignals.length).toBeGreaterThan(0);
  });
});

function fundingSignal(): RelativeValueSignal {
  return {
    id: "test-funding-short-btc",
    kind: "funding_carry",
    assetPair: "BTC/USD",
    venue: "okx perp",
    direction: "short_perp_receive_funding",
    confidence: 0.8,
    expectedEdgeBps: 100,
    riskScore: 25,
    liquidityScore: 80,
    opportunityScore: 100,
    explanation: "Synthetic funding carry fixture.",
    eligibleForPaperTrading: true,
    eligibleForLiveTrading: false,
    timestamp: sampleMarketData.generatedAt,
  };
}

function withOkxBtcPerpMark(markPrice: number): MarketDataBundle {
  return {
    ...sampleMarketData,
    markets: sampleMarketData.markets.map((market) =>
      market.id === "okx-BTC-USD-perp"
        ? {
            ...market,
            price: markPrice,
            markPrice,
            indexPrice: markPrice,
          }
        : market,
    ),
  };
}
