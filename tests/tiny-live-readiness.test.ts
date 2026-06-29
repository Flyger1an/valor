import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import type {
  DataQualityReport,
  EdgeScoreboard,
  EdgeScoreboardRow,
  MarketRiskState,
  PaperPortfolio,
  PaperTrade,
} from "@/lib/domain/types";
import { reconcileDryRunAttempts } from "@/lib/execution/dry-run-executor";
import { evaluateTinyLiveReadiness } from "@/lib/readiness/tiny-live-readiness";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { evaluateOperationalRunbook } from "@/lib/runbook/operational-runbook";
import { emptyPaperPortfolio } from "@/lib/state/local-store";

const now = new Date("2026-06-25T00:00:00.000Z");

describe("tiny-live readiness", () => {
  it("keeps sample fixture data in no-go status", () => {
    const paper = emptyPaperPortfolio();
    const dataQuality = evaluateDataQuality(sampleMarketData, {
      connectorId: "sample-fixtures",
      connectorLabel: "Deterministic sample market bundle",
      mode: "sample",
      assessedAt: sampleMarketData.generatedAt,
    });
    const report = evaluateTinyLiveReadiness({
      dataQuality,
      systemTrust: evaluateSystemTrust({
        dataQuality,
        risk: healthyRisk(),
        paper,
        now,
      }),
      edgeScoreboard: emptyScoreboard(),
      paper,
      executionReconciliation: reconcileDryRunAttempts([], now),
      operationalRunbook: readyRunbook(dataQuality, paper),
      now,
    });

    expect(report.status).toBe("no_go");
    expect(report.blockers.some((blocker) => blocker.code === "untrusted-data-source")).toBe(true);
  });

  it("keeps a strong-looking family in no-go when paper history is too short", () => {
    const paper = paperWithClosedTrades(3);
    const dataQuality = healthyDataQuality();
    const report = evaluateTinyLiveReadiness({
      dataQuality,
      systemTrust: evaluateSystemTrust({
        dataQuality,
        risk: healthyRisk(),
        paper,
        now,
      }),
      edgeScoreboard: scoreboardWithCandidate(),
      paper,
      executionReconciliation: reconcileDryRunAttempts([], now),
      operationalRunbook: readyRunbook(dataQuality, paper),
      now,
    });

    expect(report.status).toBe("no_go");
    expect(report.blockers.some((blocker) => blocker.code === "insufficient-evidence-window")).toBe(true);
  });

  it("marks a clean multi-week proving family for human candidate review", () => {
    const paper = paperWithClosedTrades(24);
    const dataQuality = healthyDataQuality();
    const report = evaluateTinyLiveReadiness({
      dataQuality,
      systemTrust: evaluateSystemTrust({
        dataQuality,
        risk: healthyRisk(),
        paper,
        now,
      }),
      edgeScoreboard: scoreboardWithCandidate(),
      paper,
      executionReconciliation: reconcileDryRunAttempts([], now),
      operationalRunbook: readyRunbook(dataQuality, paper),
      now,
    });

    expect(report.status).toBe("candidate_review");
    expect(report.candidate?.kind).toBe("funding_carry");
    expect(report.memo.conclusion).toContain("not execution approval");
  });
});

function readyRunbook(dataQuality: DataQualityReport, paper: PaperPortfolio) {
  const systemTrust = evaluateSystemTrust({
    dataQuality,
    risk: healthyRisk(),
    paper,
    now,
  });

  return evaluateOperationalRunbook({
    dataQuality,
    systemTrust,
    schedulerStatus: {
      running: false,
      cycleCount: 20,
      consecutiveErrors: 0,
      lastMessage: "Scheduler ok.",
    },
    alertDeliveries: [],
    paper,
    executionReconciliation: reconcileDryRunAttempts([], now),
    now,
  });
}

function healthyDataQuality(): DataQualityReport {
  return {
    connectorId: "trusted-test",
    connectorLabel: "Trusted test connector",
    mode: "coingecko",
    status: "healthy",
    generatedAt: now.toISOString(),
    assessedAt: now.toISOString(),
    dataAgeMinutes: 0,
    marketCount: sampleMarketData.markets.length,
    issueCount: 0,
    criticalIssueCount: 0,
    fallbackUsed: false,
    fixtureBacked: false,
    blocksPaperTrading: false,
    summary: "Trusted data is healthy.",
    issues: [],
  };
}

function healthyRisk(): MarketRiskState {
  return {
    state: "Green",
    score: 5,
    explanation: "No active risk alerts.",
    activeAlerts: [],
    tradingRestrictions: [],
    updatedAt: now.toISOString(),
  };
}

function emptyScoreboard(): EdgeScoreboard {
  return {
    updatedAt: now.toISOString(),
    rows: [],
    totals: {
      generatedCount: 0,
      paperEligibleCount: 0,
      openPositionCount: 0,
      ledgerEventCount: 0,
      totalPnlUsd: 0,
      realizedPnlUsd: 0,
      markPnlUsd: 0,
      underperformingCount: 0,
    },
  };
}

function scoreboardWithCandidate(): EdgeScoreboard {
  const row: EdgeScoreboardRow = {
    kind: "funding_carry",
    generatedCount: 20,
    paperEligibleCount: 20,
    openPositionCount: 0,
    activeNotionalUsd: 0,
    ledgerEventCount: 20,
    filledCount: 10,
    heldCount: 0,
    closedCount: 10,
    rejectedCount: 0,
    averageExpectedEdgeBps: 22,
    averageSignalRiskScore: 30,
    markPnlUsd: 0,
    realizedPnlUsd: 240,
    totalPnlUsd: 240,
    fundingUsd: 35,
    winRatePct: 70,
    acceptanceRatePct: 100,
    averageHoldingHours: 0,
    averageEdgeDecayBps: 0,
    status: "proving",
    recommendation: "Keep in paper rotation and prepare human review.",
  };

  return {
    updatedAt: now.toISOString(),
    rows: [row],
    totals: {
      generatedCount: row.generatedCount,
      paperEligibleCount: row.paperEligibleCount,
      openPositionCount: row.openPositionCount,
      ledgerEventCount: row.ledgerEventCount,
      totalPnlUsd: row.totalPnlUsd,
      realizedPnlUsd: row.realizedPnlUsd,
      markPnlUsd: row.markPnlUsd,
      underperformingCount: 0,
    },
  };
}

function paperWithClosedTrades(days: number): PaperPortfolio {
  return {
    ...emptyPaperPortfolio(),
    trades: Array.from({ length: 10 }, (_, index) =>
      tradeAtDay(Math.floor((index * Math.max(days - 1, 0)) / 9), index),
    ),
  };
}

function tradeAtDay(day: number, index: number): PaperTrade {
  const timestamp = new Date(Date.UTC(2026, 5, 1 + day)).toISOString();
  return {
    id: `closed-${index}`,
    signalId: `funding_carry:ETH-USD:okx-perp:${index}`,
    timestamp,
    assetPair: "ETH/USD",
    venue: "okx-perp",
    direction: "short_perp_receive_funding",
    notionalUsd: 1000,
    feesUsd: 1,
    status: "closed",
    reason: "readiness fixture",
    realizedPnlUsd: 24,
    fundingUsd: 4,
  };
}
