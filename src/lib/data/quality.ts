import type {
  DataQualityIssue,
  DataQualityReport,
  MarketDataBundle,
  MarketDataMode,
  MarketSnapshot,
} from "@/lib/domain/types";
import { round } from "@/lib/utils/math";

const PUBLIC_STALE_AFTER_MINUTES = 10;
const SAMPLE_STALE_AFTER_MINUTES = 60 * 24;
const WIDE_SPREAD_BPS = 25;
const EXTREME_SPREAD_BPS = 100;

export interface DataQualitySource {
  connectorId: string;
  connectorLabel: string;
  mode: MarketDataMode;
  assessedAt?: string;
}

export function evaluateDataQuality(
  data: MarketDataBundle,
  source: DataQualitySource,
): DataQualityReport {
  const assessedAt = source.assessedAt ?? new Date().toISOString();
  const dataAgeMinutes = minutesBetween(data.generatedAt, assessedAt);
  const issues: DataQualityIssue[] = [];
  const fallbackUsed = data.advisories.some(
    (advisory) =>
      advisory.id.startsWith("public-ingest-fallback") ||
      advisory.title.toLowerCase().includes("fallback"),
  );
  const fixtureBacked = source.mode === "sample" || fallbackUsed;

  if (!Number.isFinite(dataAgeMinutes)) {
    issues.push({
      code: "invalid-generated-at",
      severity: "critical",
      scope: "bundle",
      message: "Market data bundle has an invalid generatedAt timestamp.",
    });
  } else if (
    source.mode !== "sample" &&
    dataAgeMinutes > PUBLIC_STALE_AFTER_MINUTES
  ) {
    issues.push({
      code: "stale-public-bundle",
      severity: "critical",
      scope: "bundle",
      message: `Public market bundle is ${round(
        dataAgeMinutes,
        1,
      )} minutes old.`,
    });
  } else if (
    source.mode === "sample" &&
    dataAgeMinutes > SAMPLE_STALE_AFTER_MINUTES
  ) {
    issues.push({
      code: "sample-fixture-age",
      severity: "info",
      scope: "bundle",
      message:
        "Sample fixture timestamp is intentionally static and is not used as a freshness guarantee.",
    });
  }

  if (data.markets.length === 0) {
    issues.push({
      code: "no-markets",
      severity: "critical",
      scope: "markets",
      message: "Market bundle contains no market snapshots.",
    });
  }

  if (fallbackUsed) {
    issues.push({
      code: "fixture-fallback",
      severity: source.mode === "sample" ? "info" : "critical",
      scope: "connector",
      message:
        "Connector reported fixture fallback; new paper entries should wait for trusted live data.",
    });
  }

  for (const market of data.markets) {
    issues.push(...issuesForMarket(market, source.mode, assessedAt));
  }

  const criticalIssueCount = issues.filter(
    (issue) => issue.severity === "critical",
  ).length;
  const warningIssueCount = issues.filter(
    (issue) => issue.severity === "warning",
  ).length;
  const blocksPaperTrading = criticalIssueCount > 0;
  const status =
    criticalIssueCount > 0
      ? "blocked"
      : warningIssueCount > 0
        ? "degraded"
        : "healthy";

  return {
    connectorId: source.connectorId,
    connectorLabel: source.connectorLabel,
    mode: source.mode,
    status,
    generatedAt: data.generatedAt,
    assessedAt,
    dataAgeMinutes: Number.isFinite(dataAgeMinutes)
      ? round(dataAgeMinutes, 1)
      : -1,
    marketCount: data.markets.length,
    issueCount: issues.length,
    criticalIssueCount,
    fallbackUsed,
    fixtureBacked,
    blocksPaperTrading,
    summary: summaryFor(status, source.mode, issues, data.markets.length),
    issues,
  };
}

function issuesForMarket(
  market: MarketSnapshot,
  mode: MarketDataMode,
  assessedAt: string,
): DataQualityIssue[] {
  const issues: DataQualityIssue[] = [];
  const scope = `market:${market.id}`;
  const totalDepthUsd = market.orderBook.bidDepthUsd + market.orderBook.askDepthUsd;

  if (!Number.isFinite(market.price) || market.price <= 0) {
    issues.push({
      code: "invalid-price",
      severity: "critical",
      scope,
      message: `${market.id} has an invalid price.`,
    });
  }

  if (market.orderBook.bid > market.orderBook.ask) {
    issues.push({
      code: "crossed-book",
      severity: "critical",
      scope,
      message: `${market.id} has a crossed order book.`,
    });
  }

  if (!Number.isFinite(totalDepthUsd) || totalDepthUsd <= 0) {
    issues.push({
      code: "zero-depth",
      severity: "critical",
      scope,
      message: `${market.id} has no displayed order-book depth.`,
    });
  }

  if (market.orderBook.spreadBps >= EXTREME_SPREAD_BPS) {
    issues.push({
      code: "extreme-spread",
      severity: "critical",
      scope,
      message: `${market.id} spread is ${round(
        market.orderBook.spreadBps,
        1,
      )} bps.`,
    });
  } else if (market.orderBook.spreadBps >= WIDE_SPREAD_BPS) {
    issues.push({
      code: "wide-spread",
      severity: "warning",
      scope,
      message: `${market.id} spread is ${round(
        market.orderBook.spreadBps,
        1,
      )} bps.`,
    });
  }

  const marketAgeMinutes = minutesBetween(market.timestamp, assessedAt);
  if (!Number.isFinite(marketAgeMinutes)) {
    issues.push({
      code: "invalid-market-timestamp",
      severity: "warning",
      scope,
      message: `${market.id} has an invalid timestamp.`,
    });
  } else if (mode !== "sample" && marketAgeMinutes > PUBLIC_STALE_AFTER_MINUTES) {
    issues.push({
      code: "stale-market",
      severity: "critical",
      scope,
      message: `${market.id} snapshot is ${round(
        marketAgeMinutes,
        1,
      )} minutes old.`,
    });
  }

  return issues;
}

function minutesBetween(earlierIso: string, laterIso: string): number {
  return (new Date(laterIso).getTime() - new Date(earlierIso).getTime()) / 60_000;
}

function summaryFor(
  status: DataQualityReport["status"],
  mode: MarketDataMode,
  issues: DataQualityIssue[],
  marketCount: number,
): string {
  if (status === "blocked") {
    return `${issues.filter((issue) => issue.severity === "critical").length} critical data-quality issue(s) block new paper entries.`;
  }

  if (status === "degraded") {
    return `${marketCount} markets available with ${issues.length} non-critical data-quality issue(s).`;
  }

  if (mode === "sample") {
    return `${marketCount} deterministic fixture markets available for local inspection.`;
  }

  return `${marketCount} public market snapshots passed freshness and book checks.`;
}
