import type {
  MarketDataBundle,
  MarketRiskState,
  MarketSnapshot,
  RiskAlert,
  RiskState,
  TradingRestriction,
} from "@/lib/domain/types";
import { clamp, round } from "@/lib/utils/math";

const STATE_SCORE: Record<RiskState, number> = {
  Green: 0,
  Yellow: 35,
  Red: 70,
  Black: 90,
};

export function evaluateMarketRisk(data: MarketDataBundle): MarketRiskState {
  const alerts = [
    ...exchangeStressAlerts(data),
    ...stablecoinAlerts(data),
    ...securityAlerts(data),
    ...liquidityAlerts(data.markets, data.generatedAt),
    ...fundingExtremeAlerts(data.markets, data.generatedAt),
    ...dislocationAlerts(data.markets, data.generatedAt),
    ...chainCongestionAlerts(data),
  ];

  const alertScores = alerts.map((alert) => severityScore(alert.severity));
  const maxAlertScore = Math.max(0, ...alertScores);
  const additionalAlertPressure =
    alertScores.reduce((sum, scoreValue) => sum + scoreValue, 0) - maxAlertScore;
  const score = clamp(maxAlertScore + Math.min(additionalAlertPressure, 84) * 0.58, 0, 100);
  const state = scoreToState(score, alerts);
  const restrictions = dedupeRestrictions(
    alerts.flatMap((alert) => alert.restrictions),
  );

  return {
    state,
    score: round(score, 1),
    explanation: explanationFor(state, alerts),
    activeAlerts: alerts.sort(
      (a, b) => severityScore(b.severity) - severityScore(a.severity),
    ),
    tradingRestrictions: restrictions,
    updatedAt: data.generatedAt,
  };
}

function exchangeStressAlerts(data: MarketDataBundle): RiskAlert[] {
  return data.exchangeHealth
    .filter(
      (health) =>
        health.withdrawals !== "normal" ||
        health.reserveStatus === "flagged" ||
        health.reserveStatus === "stale",
    )
    .map((health) => ({
      id: `exchange-${health.venue}`,
      severity:
        health.withdrawals === "paused" || health.reserveStatus === "flagged"
          ? "critical"
          : health.withdrawals === "delayed"
            ? "high"
            : "medium",
      category: "exchange",
      title: `${health.venue.toUpperCase()} venue health requires review`,
      explanation: health.message,
      source: "exchange status / proof-of-reserves monitor",
      timestamp: health.updatedAt,
      restrictions: [
        {
          code: `venue-review-${health.venue}`,
          description: `Block live trading and transfers on ${health.venue} until venue health is normal.`,
          severity:
            health.withdrawals === "paused" || health.reserveStatus === "flagged"
              ? "Red"
              : "Yellow",
        },
      ],
    }));
}

function stablecoinAlerts(data: MarketDataBundle): RiskAlert[] {
  return data.stablecoins
    .filter((coin) => Math.abs(coin.pegDeviationBps) >= 20)
    .map((coin) => ({
      id: `stablecoin-${coin.asset}-${coin.venue}`,
      severity:
        Math.abs(coin.pegDeviationBps) >= 75
          ? "critical"
          : Math.abs(coin.pegDeviationBps) >= 35
            ? "high"
            : "medium",
      category: "stablecoin",
      title: `${coin.asset} peg deviation on ${coin.venue}`,
      explanation: `${coin.asset} is ${round(
        coin.pegDeviationBps,
        1,
      )} bps from $1 with approximately $${round(
        coin.liquidityUsd / 1_000_000,
        1,
      )}M displayed liquidity.`,
      source: "stablecoin peg monitor",
      timestamp: coin.timestamp,
      restrictions: [
        {
          code: `stablecoin-${coin.asset}-size-cut`,
          description: `Disable live ${coin.asset} depeg trades and cap paper size until redemption and venue liquidity are reviewed.`,
          severity: Math.abs(coin.pegDeviationBps) >= 35 ? "Red" : "Yellow",
        },
      ],
    }));
}

function securityAlerts(data: MarketDataBundle): RiskAlert[] {
  return data.advisories
    .filter((advisory) => advisory.severity !== "info" && advisory.severity !== "low")
    .map((advisory) => ({
      id: `security-${advisory.id}`,
      severity: advisory.severity,
      category: "security",
      title: advisory.title,
      explanation: advisory.summary,
      source: advisory.source,
      timestamp: advisory.publishedAt,
      restrictions: [
        {
          code: `security-${advisory.id}`,
          description:
            "Disable affected bridge/protocol routes and require manual review before paper or live simulation.",
          severity: advisory.severity === "critical" ? "Black" : "Red",
        },
      ],
    }));
}

function liquidityAlerts(
  markets: MarketSnapshot[],
  timestamp: string,
): RiskAlert[] {
  return markets
    .filter(
      (market) =>
        market.orderBook.spreadBps > 8 ||
        market.orderBook.bidDepthUsd + market.orderBook.askDepthUsd < 5_000_000,
    )
    .map((market) => ({
      id: `liquidity-${market.id}`,
      severity: market.orderBook.spreadBps > 12 ? "high" : "medium",
      category: "liquidity",
      title: `${market.base}/${market.quote} liquidity is thin on ${market.venue}`,
      explanation: `Displayed depth is $${round(
        (market.orderBook.bidDepthUsd + market.orderBook.askDepthUsd) / 1_000_000,
        1,
      )}M and spread is ${round(market.orderBook.spreadBps, 1)} bps.`,
      source: "order book monitor",
      timestamp,
      restrictions: [
        {
          code: `liquidity-${market.id}`,
          description: `Require reduced notional for ${market.base}/${market.quote} on ${market.venue}.`,
          severity: "Yellow",
        },
      ],
    }));
}

function fundingExtremeAlerts(
  markets: MarketSnapshot[],
  timestamp: string,
): RiskAlert[] {
  return markets
    .filter((market) => Math.abs(market.fundingRate8h ?? 0) >= 0.00075)
    .map((market) => ({
      id: `funding-${market.id}`,
      severity: Math.abs(market.fundingRate8h ?? 0) >= 0.0012 ? "high" : "medium",
      category: "funding",
      title: `${market.base} funding rate is elevated on ${market.venue}`,
      explanation: `Funding is ${round(
        (market.fundingRate8h ?? 0) * 100,
        4,
      )}% per 8h with $${round(
        (market.openInterestUsd ?? 0) / 1_000_000_000,
        2,
      )}B open interest.`,
      source: "perpetual funding monitor",
      timestamp,
      restrictions: [
        {
          code: `funding-${market.id}`,
          description:
            "Allow paper research only; live execution requires explicit review of crowded positioning.",
          severity: "Yellow",
        },
      ],
    }));
}

function dislocationAlerts(
  markets: MarketSnapshot[],
  timestamp: string,
): RiskAlert[] {
  const alerts: RiskAlert[] = [];
  const spotMarkets = markets.filter((market) => market.instrumentType === "spot");
  const pairs = [...new Set(spotMarkets.map((market) => `${market.base}/${market.quote}`))];

  for (const pair of pairs) {
    const group = spotMarkets.filter(
      (market) => `${market.base}/${market.quote}` === pair,
    );
    if (group.length < 2) continue;
    const low = group.reduce((min, market) => (market.price < min.price ? market : min));
    const high = group.reduce((max, market) => (market.price > max.price ? market : max));
    const premiumBps = ((high.price - low.price) / low.price) * 10_000;

    if (premiumBps < 50) continue;

    alerts.push({
      id: `dislocation-${pair}`,
      severity: premiumBps > 120 ? "high" : "medium",
      category: "dislocation",
      title: `${pair} cross-venue dislocation`,
      explanation: `${pair} trades ${round(
        premiumBps,
        1,
      )} bps higher on ${high.venue} than ${low.venue}.`,
      source: "cross-exchange price monitor",
      timestamp,
      restrictions: [
        {
          code: `dislocation-${pair}`,
          description:
            "Require transfer-route and withdrawal-health confirmation before treating this as executable edge.",
          severity: "Yellow",
        },
      ],
    });
  }

  return alerts;
}

function chainCongestionAlerts(data: MarketDataBundle): RiskAlert[] {
  return data.chainFees
    .filter((fee) => fee.value > fee.normalRangeHigh)
    .map((fee) => ({
      id: `chain-${fee.chain}`,
      severity: fee.value > fee.normalRangeHigh * 1.8 ? "high" : "medium",
      category: "chain",
      title: `${fee.chain} fee spike`,
      explanation: `${fee.feeMetric} is ${round(
        fee.value,
        1,
      )}, above the normal high threshold of ${fee.normalRangeHigh}.`,
      source: "chain fee monitor",
      timestamp: fee.timestamp,
      restrictions: [
        {
          code: `chain-${fee.chain}`,
          description:
            "Increase slippage/fee assumptions and block bridge-dependent execution routes.",
          severity: "Yellow",
        },
      ],
    }));
}

function severityScore(severity: RiskAlert["severity"]): number {
  switch (severity) {
    case "critical":
      return 45;
    case "high":
      return 28;
    case "medium":
      return 14;
    case "low":
      return 5;
    default:
      return 1;
  }
}

function scoreToState(score: number, alerts: RiskAlert[]): RiskState {
  if (alerts.some((alert) => alert.restrictions.some((r) => r.severity === "Black"))) {
    return "Black";
  }
  if (score >= STATE_SCORE.Black) return "Black";
  if (score >= STATE_SCORE.Red) return "Red";
  if (score >= STATE_SCORE.Yellow) return "Yellow";
  return "Green";
}

function explanationFor(state: RiskState, alerts: RiskAlert[]): string {
  if (alerts.length === 0) {
    return "No active risk alerts. Paper trading can run under normal local limits.";
  }

  const top = alerts
    .slice()
    .sort((a, b) => severityScore(b.severity) - severityScore(a.severity))[0];

  return `${state} because ${top.title.toLowerCase()} and ${alerts.length - 1} additional alert${
    alerts.length === 2 ? "" : "s"
  } require review.`;
}

function dedupeRestrictions(
  restrictions: TradingRestriction[],
): TradingRestriction[] {
  const map = new Map<string, TradingRestriction>();
  restrictions.forEach((restriction) => {
    const existing = map.get(restriction.code);
    if (!existing || STATE_SCORE[restriction.severity] > STATE_SCORE[existing.severity]) {
      map.set(restriction.code, restriction);
    }
  });
  return [...map.values()];
}
