import { describe, expect, it } from "vitest";
import type {
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
} from "@/lib/domain/types";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { advancePaperBook } from "@/lib/paper/paper-book";
import { STARTING_CASH_USD } from "@/lib/paper/paper-broker";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("advancePaperBook", () => {
  it("opens eligible positions and records equity history", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const first = advancePaperBook({
      signals,
      risk,
      timestamp: sampleMarketData.generatedAt,
    });

    expect(first.portfolio.positions.length).toBeGreaterThan(0);
    expect(first.equityHistory).toHaveLength(1);
    expect(first.opened).toBeGreaterThan(0);

    const second = advancePaperBook({
      previous: first.portfolio,
      signals,
      risk,
      timestamp: new Date(Date.parse(sampleMarketData.generatedAt) + 60_000).toISOString(),
      equityHistory: first.equityHistory,
    });

    expect(second.equityHistory).toHaveLength(2);
    expect(second.marked).toBeGreaterThan(0);
  });
});

// --- Golden ledger: exact cash/realized/fees across open -> mark -> close ---

const GREEN_RISK: MarketRiskState = {
  state: "Green",
  score: 0,
  explanation: "",
  activeAlerts: [],
  tradingRestrictions: [],
  updatedAt: "2024-01-01T00:00:00.000Z",
};

const T1 = "2024-01-01T00:00:00.000Z";
const T2 = "2024-01-01T00:30:00.000Z";
const T3 = "2024-01-01T01:00:00.000Z";

function signalA(edgeBps: number): RelativeValueSignal {
  return {
    id: "sigA",
    kind: "spot_perp_basis",
    assetPair: "BTC/USD",
    venue: "okx spot / okx perp",
    direction: "long_spot_short_perp",
    confidence: 0.5,
    expectedEdgeBps: edgeBps,
    riskScore: 10,
    liquidityScore: 80,
    opportunityScore: 50,
    explanation: "controlled test signal",
    timestamp: T1,
    eligibleForPaperTrading: true,
    eligibleForLiveTrading: false,
  };
}

// opportunityScore 50 -> notional = min(12500, 100000*0.08, 0.5*12500) = 6250
// open/close fee = 6250 * 8bps = 5.00; cycle-2 mark = 6250 * (50bps) * 0.5 = 15.63
function runSequence() {
  const c1 = advancePaperBook({
    signals: [signalA(100)],
    risk: GREEN_RISK,
    timestamp: T1,
  });
  const c2 = advancePaperBook({
    previous: c1.portfolio,
    signals: [signalA(150)],
    risk: GREEN_RISK,
    timestamp: T2,
    equityHistory: c1.equityHistory,
  });
  const c3 = advancePaperBook({
    previous: c2.portfolio,
    signals: [],
    risk: GREEN_RISK,
    timestamp: T3,
    equityHistory: c2.equityHistory,
  });
  return { c1, c2, c3 };
}

describe("advancePaperBook ledger (golden numbers)", () => {
  const { c1, c2, c3 } = runSequence();

  it("cycle 1: opens one position, charges the open fee to cash, mark starts flat", () => {
    expect(c1.opened).toBe(1);
    expect(c1.portfolio.positions).toHaveLength(1);
    expect(c1.portfolio.positions[0].markPnlUsd).toBeCloseTo(0, 2);
    expect(c1.portfolio.feesPaidUsd).toBeCloseTo(5, 2);
    expect(c1.portfolio.cashUsd).toBeCloseTo(99_995, 2);
    expect(c1.portfolio.realizedPnlUsd).toBeCloseTo(0, 2);
    expect(c1.portfolio.equityUsd).toBeCloseTo(99_995, 2);
    // Deterministic id, derived from (signalId, timestamp) — no Date.now().
    expect(c1.portfolio.trades.map((t) => t.id)).toContain(`paper-open:sigA:${T1}`);
  });

  it("cycle 2: marks the held position as unrealized PnL with no cash movement", () => {
    expect(c2.marked).toBe(1);
    expect(c2.opened).toBe(0);
    expect(c2.portfolio.positions[0].markPnlUsd).toBeCloseTo(15.63, 2);
    expect(c2.portfolio.cashUsd).toBeCloseTo(99_995, 2);
    expect(c2.portfolio.feesPaidUsd).toBeCloseTo(5, 2);
    expect(c2.portfolio.realizedPnlUsd).toBeCloseTo(0, 2);
    expect(c2.portfolio.equityUsd).toBeCloseTo(100_010.63, 2);
  });

  it("cycle 3: closes the position, realizing PnL and a close fee into cash", () => {
    expect(c3.closed).toBe(1);
    expect(c3.portfolio.positions).toHaveLength(0);
    expect(c3.portfolio.realizedPnlUsd).toBeCloseTo(15.63, 2);
    expect(c3.portfolio.feesPaidUsd).toBeCloseTo(10, 2);
    expect(c3.portfolio.cashUsd).toBeCloseTo(100_005.63, 2);
    expect(c3.portfolio.equityUsd).toBeCloseTo(100_005.63, 2);
  });

  it("preserves the ledger identities every cycle", () => {
    for (const cycle of [c1, c2, c3]) {
      const p = cycle.portfolio;
      const openMark = p.positions.reduce((sum, x) => sum + x.markPnlUsd, 0);
      // equity = cash + unrealized marks
      expect(p.equityUsd).toBeCloseTo(p.cashUsd + openMark, 2);
      // cash = starting cash + realized - fees
      expect(p.cashUsd).toBeCloseTo(
        STARTING_CASH_USD + p.realizedPnlUsd - p.feesPaidUsd,
        2,
      );
    }
  });

  it("is deterministic: identical inputs produce byte-identical output", () => {
    const a = runSequence();
    const b = runSequence();
    expect(a.c3.portfolio).toEqual(b.c3.portfolio);
    expect(a.c3.equityHistory).toEqual(b.c3.equityHistory);
  });

  it("upgrades a pre-ledger persisted book without producing NaN", () => {
    // Simulate a portfolio persisted before realizedPnlUsd/feesPaidUsd existed.
    const legacy = JSON.parse(JSON.stringify(c1.portfolio));
    delete legacy.realizedPnlUsd;
    delete legacy.feesPaidUsd;

    const next = advancePaperBook({
      previous: legacy as PaperPortfolio,
      signals: [signalA(150)],
      risk: GREEN_RISK,
      timestamp: T2,
    });

    expect(Number.isFinite(next.portfolio.realizedPnlUsd)).toBe(true);
    expect(Number.isFinite(next.portfolio.feesPaidUsd)).toBe(true);
    expect(Number.isFinite(next.portfolio.equityUsd)).toBe(true);
    // cash (99,995 carried) + held-position mark (15.63) with no fees recoverable.
    expect(next.portfolio.equityUsd).toBeCloseTo(100_010.63, 2);
  });
});
