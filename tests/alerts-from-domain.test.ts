import { describe, expect, it } from "vitest";
import {
  edgeScoreboardToAlertEvents,
  systemTrustToAlertEvents,
} from "@/lib/alerts/from-domain";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import type { PaperPortfolio, PaperTrade } from "@/lib/domain/types";
import { buildEdgeScoreboard } from "@/lib/edge/scoreboard";
import { computeFromData } from "@/lib/ops/recompute";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";
import { emptyPaperPortfolio } from "@/lib/state/local-store";

describe("domain alert adapters", () => {
  it("turns sample-backed system trust caution into a watch alert", () => {
    const verdict = evaluateSystemTrust({
      dataQuality: sampleDataQuality(),
      risk: evaluateMarketRisk(sampleMarketData),
      paper: emptyPaperPortfolio(),
      now: new Date(sampleMarketData.generatedAt),
    });

    const alerts = systemTrustToAlertEvents(verdict);

    expect(alerts).toHaveLength(1);
    expect(alerts[0].source).toBe("system-trust-gate");
    expect(alerts[0].severity).toBe("WATCH");
    expect(alerts[0].metadata.blocksLiveTrading).toBe(true);
    expect(alerts[0].tradingImpact).toContain("live trading remains blocked");
  });

  it("escalates kill-switch trust blocks as black alerts", () => {
    const verdict = evaluateSystemTrust({
      dataQuality: sampleDataQuality(),
      risk: evaluateMarketRisk(sampleMarketData),
      killSwitch: {
        active: true,
        reason: "manual halt",
        activatedBy: "test",
        activatedAt: sampleMarketData.generatedAt,
        dashboardResetRequired: true,
      },
      paper: emptyPaperPortfolio(),
      now: new Date(sampleMarketData.generatedAt),
    });

    const alerts = systemTrustToAlertEvents(verdict);

    expect(alerts).toHaveLength(1);
    expect(alerts[0].severity).toBe("BLACK");
    expect(alerts[0].metadata.blocksPaperTrading).toBe(true);
    expect(alerts[0].fingerprint).toContain("kill-switch-active");
  });

  it("turns underperforming edge families into watch alerts", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const paper = withTrade({
      signalId: "spot_perp_basis:BTC-USD:coinbase-spot---binance-perp",
      status: "closed",
      realizedPnlUsd: -42,
    });
    const scoreboard = buildEdgeScoreboard({
      signals,
      paper,
      updatedAt: sampleMarketData.generatedAt,
    });

    const alerts = edgeScoreboardToAlertEvents(scoreboard);

    expect(alerts).toHaveLength(1);
    expect(alerts[0].source).toBe("edge-scoreboard");
    expect(alerts[0].severity).toBe("WATCH");
    expect(alerts[0].fingerprint).toBe("edge-underperforming:spot_perp_basis");
    expect(alerts[0].message).toContain("-$42.00");
    expect(alerts[0].tradingImpact).toContain("watch-only");
  });

  it("includes system-trust alerts in computed alert events", () => {
    const computed = computeFromData(sampleMarketData, sampleDataQuality(), {
      killSwitch: {
        active: true,
        reason: "manual halt",
        activatedBy: "test",
        activatedAt: sampleMarketData.generatedAt,
        dashboardResetRequired: true,
      },
      now: new Date(sampleMarketData.generatedAt),
    });

    expect(
      computed.alertEvents.some(
        (alert) => alert.source === "system-trust-gate" && alert.severity === "BLACK",
      ),
    ).toBe(true);
  });
});

function sampleDataQuality() {
  return evaluateDataQuality(sampleMarketData, {
    connectorId: "sample-fixtures",
    connectorLabel: "Deterministic sample market bundle",
    mode: "sample",
    assessedAt: sampleMarketData.generatedAt,
  });
}

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
    reason: "alert fixture",
    realizedPnlUsd: input.realizedPnlUsd,
  };
}
