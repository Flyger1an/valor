import type { AlertEvent } from "@/lib/alerts/types";
import type {
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
  RiskAlert,
} from "@/lib/domain/types";
import { severityFromRiskState } from "@/lib/alerts/severity-policy";

export function riskAlertToAlertEvent(alert: RiskAlert): AlertEvent {
  const severity = mapRiskAlertSeverity(alert.severity);
  return {
    id: `alert:${alert.id}`,
    severity,
    title: alert.title,
    message: alert.explanation,
    source: alert.source,
    scope: {},
    createdAt: alert.timestamp,
    fingerprint: `${alert.category}:${alert.title}`,
    tradingImpact:
      severity === "CRITICAL" || severity === "BLACK"
        ? "Block new live trades for affected venue/asset."
        : "Mark for review.",
    metadata: {
      category: alert.category,
      riskSeverity: alert.severity,
    },
  };
}

export function signalToTradeableAlert(signal: RelativeValueSignal): AlertEvent {
  return {
    id: `alert:signal:${signal.id}`,
    severity: signal.eligibleForPaperTrading ? "TRADEABLE" : "WATCH",
    title: `${signal.assetPair} ${signal.kind.replaceAll("_", " ")}`,
    message: `${signal.explanation} Expected edge ${signal.expectedEdgeBps} bps; risk ${signal.riskScore}; liquidity ${signal.liquidityScore}; assumed fees/slippage are strategy configured and must be verified before manual review.`,
    source: "relative-value-signal-engine",
    scope: { pair: signal.assetPair, venue: signal.venue },
    createdAt: signal.timestamp,
    fingerprint: `signal:${signal.kind}:${signal.assetPair}:${signal.venue}`,
    tradingImpact: signal.eligibleForPaperTrading
      ? "Paper trading allowed under local limits; live trading requires all guards."
      : "No trading action.",
    metadata: {
      expectedEdgeBps: signal.expectedEdgeBps,
      riskScore: signal.riskScore,
      liquidityScore: signal.liquidityScore,
      opportunityScore: signal.opportunityScore,
    },
  };
}

export function riskTransitionAlert(input: {
  previousState: MarketRiskState["state"];
  next: MarketRiskState;
}): AlertEvent | null {
  if (input.previousState === input.next.state) return null;

  const worsened =
    stateRank(input.next.state) > stateRank(input.previousState);
  const severity: AlertEvent["severity"] =
    input.next.state === "Black"
      ? "BLACK"
      : input.next.state === "Red"
        ? "CRITICAL"
        : worsened
          ? "WATCH"
          : "INFO";

  return {
    id: `alert:risk-transition:${input.next.updatedAt}`,
    severity,
    title: `Risk state moved ${input.previousState} → ${input.next.state}`,
    message: input.next.explanation,
    source: "risk-state-engine",
    scope: {},
    createdAt: input.next.updatedAt,
    fingerprint: `risk-transition:${input.previousState}:${input.next.state}`,
    tradingImpact:
      severity === "BLACK" || severity === "CRITICAL"
        ? "Tighten exposure and review restrictions immediately."
        : "Review restrictions and paper book posture.",
    metadata: {
      previousState: input.previousState,
      nextState: input.next.state,
      riskScore: input.next.score,
    },
  };
}

export function dailyDigestAlert(input: {
  risk: MarketRiskState;
  paper: PaperPortfolio;
  signalCount: number;
}): AlertEvent {
  return {
    id: `alert:digest:${input.risk.updatedAt}`,
    severity: severityFromRiskState(input.risk.state),
    title: "Daily system digest",
    message: `${input.signalCount} signals generated. Paper PnL ${input.paper.dailyPnlUsd.toFixed(
      2,
    )}. Market risk state ${input.risk.state}: ${input.risk.explanation}`,
    source: "system-digest",
    scope: {},
    createdAt: input.risk.updatedAt,
    fingerprint: `digest:${input.risk.updatedAt.slice(0, 10)}`,
    tradingImpact:
      input.risk.state === "Black"
        ? "Global live halt remains active."
        : "No direct trading impact.",
    metadata: {
      riskState: input.risk.state,
      signalCount: input.signalCount,
      paperDailyPnlUsd: input.paper.dailyPnlUsd,
    },
  };
}

function stateRank(state: MarketRiskState["state"]): number {
  if (state === "Green") return 0;
  if (state === "Yellow") return 1;
  if (state === "Red") return 2;
  return 3;
}

function mapRiskAlertSeverity(
  severity: RiskAlert["severity"],
): AlertEvent["severity"] {
  if (severity === "critical") return "BLACK";
  if (severity === "high") return "CRITICAL";
  if (severity === "medium") return "WATCH";
  return "INFO";
}
