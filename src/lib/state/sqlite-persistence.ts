import { desc, eq } from "drizzle-orm";
import { getDb } from "@/db/client";
import {
  alertDeliveries,
  alertEvents,
  appState,
  backtestRuns,
  marketSnapshots,
  relativeValueSignals,
  riskAlerts,
  riskStates,
} from "@/db/schema";
import type { AlertDelivery, AlertEvent, AlertRouterState } from "@/lib/alerts/types";
import type {
  AuditEvent,
  BacktestReport,
  MarketDataBundle,
  MarketRiskState,
  MarketSnapshot,
  PaperPortfolio,
  RelativeValueSignal,
} from "@/lib/domain/types";
import type { KillSwitchState } from "@/lib/kill-switch/kill-switch";
import type { ValorLocalState } from "@/lib/state/local-store";

type BundleExtras = Omit<MarketDataBundle, "generatedAt" | "markets">;

interface ActionLogEntry {
  id: string;
  timestamp: string;
  action: string;
  status: "ok" | "error" | "dry_run";
  message: string;
}

export function sqlitePersistenceEnabled() {
  return process.env.VALOR_DISABLE_SQLITE !== "true";
}

export function loadValorStateFromSqlite(): ValorLocalState | null {
  if (!sqlitePersistenceEnabled()) return null;

  const db = getDb();
  const [latestRisk] = db
    .select()
    .from(riskStates)
    .orderBy(desc(riskStates.updatedAt))
    .limit(1)
    .all();

  if (!latestRisk) return null;

  const bundleExtras = readAppState<BundleExtras>(db, "bundle_extras");
  const lastRefreshAt = readAppState<string>(db, "last_refresh_at");
  const markets = db.select().from(marketSnapshots).all().map(mapMarketRow);
  const signals = db
    .select()
    .from(relativeValueSignals)
    .orderBy(desc(relativeValueSignals.opportunityScore))
    .all()
    .map(mapSignalRow);
  const activeAlerts = db.select().from(riskAlerts).all().map(mapRiskAlertRow);
  const backtestRow = db
    .select()
    .from(backtestRuns)
    .orderBy(desc(backtestRuns.createdAt))
    .limit(1)
    .all()[0];

  const data: MarketDataBundle | undefined =
    markets.length > 0 && bundleExtras
      ? {
          generatedAt: lastRefreshAt ?? latestRisk.updatedAt,
          markets,
          ...bundleExtras,
        }
      : undefined;

  const risk: MarketRiskState = {
    state: latestRisk.state as MarketRiskState["state"],
    score: latestRisk.score,
    explanation: latestRisk.explanation,
    activeAlerts,
    tradingRestrictions: JSON.parse(latestRisk.restrictionsJson),
    updatedAt: latestRisk.updatedAt,
  };

  return {
    lastRefreshAt: lastRefreshAt ?? latestRisk.updatedAt,
    data,
    signals,
    risk,
    backtest: backtestRow ? mapBacktestRow(backtestRow) : undefined,
    paper: readAppState<PaperPortfolio>(db, "paper_portfolio"),
    alertEvents: db
      .select()
      .from(alertEvents)
      .orderBy(desc(alertEvents.createdAt))
      .all()
      .map(mapAlertEventRow),
    alertDeliveries: db
      .select()
      .from(alertDeliveries)
      .orderBy(desc(alertDeliveries.attemptedAt))
      .all()
      .map(mapAlertDeliveryRow),
    alertRouterState:
      readAppState<AlertRouterState>(db, "alert_router_state") ?? {
        lastSentByFingerprint: {},
        acknowledgedAlertIds: [],
      },
    auditEvents: readAppState<AuditEvent[]>(db, "audit_events") ?? [],
    killSwitch: readAppState<KillSwitchState>(db, "kill_switch"),
    actionLog: readAppState<ActionLogEntry[]>(db, "action_log") ?? [],
  };
}

export function persistValorStateToSqlite(state: ValorLocalState) {
  if (!sqlitePersistenceEnabled()) return;

  const db = getDb();
  const now = new Date().toISOString();

  db.transaction((tx) => {
    if (state.data) {
      tx.delete(marketSnapshots).run();
      for (const market of state.data.markets) {
        tx.insert(marketSnapshots)
          .values({
            id: market.id,
            venue: market.venue,
            base: market.base,
            quote: market.quote,
            instrumentType: market.instrumentType,
            price: market.price,
            markPrice: market.markPrice ?? null,
            indexPrice: market.indexPrice ?? null,
            fundingRate8h: market.fundingRate8h ?? null,
            openInterestUsd: market.openInterestUsd ?? null,
            volume24hUsd: market.volume24hUsd,
            volatility30d: market.volatility30d,
            change24hPct: market.change24hPct,
            spreadBps: market.orderBook.spreadBps,
            bidDepthUsd: market.orderBook.bidDepthUsd,
            askDepthUsd: market.orderBook.askDepthUsd,
            timestamp: market.timestamp,
          })
          .run();
      }

      const { generatedAt, markets: _markets, ...extras } = state.data;
      writeAppState(tx, "bundle_extras", extras, generatedAt);
      writeAppState(tx, "last_refresh_at", generatedAt, generatedAt);
    }

    if (state.signals) {
      tx.delete(relativeValueSignals).run();
      for (const signal of state.signals) {
        tx.insert(relativeValueSignals)
          .values({
            id: signal.id,
            kind: signal.kind,
            assetPair: signal.assetPair,
            venue: signal.venue,
            direction: signal.direction,
            confidence: signal.confidence,
            expectedEdgeBps: signal.expectedEdgeBps,
            riskScore: signal.riskScore,
            liquidityScore: signal.liquidityScore,
            opportunityScore: signal.opportunityScore,
            explanation: signal.explanation,
            eligibleForPaperTrading: signal.eligibleForPaperTrading,
            eligibleForLiveTrading: signal.eligibleForLiveTrading,
            timestamp: signal.timestamp,
          })
          .run();
      }
    }

    if (state.risk) {
      tx.delete(riskAlerts).run();
      for (const alert of state.risk.activeAlerts) {
        tx.insert(riskAlerts)
          .values({
            id: alert.id,
            severity: alert.severity,
            category: alert.category,
            title: alert.title,
            explanation: alert.explanation,
            source: alert.source,
            restrictionsJson: JSON.stringify(alert.restrictions),
            timestamp: alert.timestamp,
          })
          .run();
      }

      tx.insert(riskStates)
        .values({
          id: "current",
          state: state.risk.state,
          score: state.risk.score,
          explanation: state.risk.explanation,
          restrictionsJson: JSON.stringify(state.risk.tradingRestrictions),
          updatedAt: state.risk.updatedAt,
        })
        .onConflictDoUpdate({
          target: riskStates.id,
          set: {
            state: state.risk.state,
            score: state.risk.score,
            explanation: state.risk.explanation,
            restrictionsJson: JSON.stringify(state.risk.tradingRestrictions),
            updatedAt: state.risk.updatedAt,
          },
        })
        .run();
    }

    if (state.backtest) {
      tx.insert(backtestRuns)
        .values({
          id: "latest",
          strategyName: state.backtest.strategyName,
          startedAt: state.backtest.startedAt,
          endedAt: state.backtest.endedAt,
          startingCashUsd: state.backtest.startingCashUsd,
          endingEquityUsd: state.backtest.endingEquityUsd,
          totalReturnPct: state.backtest.totalReturnPct,
          maxDrawdownPct: state.backtest.maxDrawdownPct,
          sharpe: state.backtest.sharpe,
          sortino: state.backtest.sortino,
          winRatePct: state.backtest.winRatePct,
          exposureAvgPct: state.backtest.exposureAvgPct,
          turnoverUsd: state.backtest.turnoverUsd,
          totalFeesUsd: state.backtest.totalFeesUsd,
          reportJson: JSON.stringify(state.backtest),
          createdAt: now,
        })
        .onConflictDoUpdate({
          target: backtestRuns.id,
          set: {
            strategyName: state.backtest.strategyName,
            startedAt: state.backtest.startedAt,
            endedAt: state.backtest.endedAt,
            startingCashUsd: state.backtest.startingCashUsd,
            endingEquityUsd: state.backtest.endingEquityUsd,
            totalReturnPct: state.backtest.totalReturnPct,
            maxDrawdownPct: state.backtest.maxDrawdownPct,
            sharpe: state.backtest.sharpe,
            sortino: state.backtest.sortino,
            winRatePct: state.backtest.winRatePct,
            exposureAvgPct: state.backtest.exposureAvgPct,
            turnoverUsd: state.backtest.turnoverUsd,
            totalFeesUsd: state.backtest.totalFeesUsd,
            reportJson: JSON.stringify(state.backtest),
            createdAt: now,
          },
        })
        .run();
    }

    if (state.alertEvents.length > 0) {
      for (const alert of state.alertEvents) {
        tx.insert(alertEvents)
          .values({
            id: alert.id,
            severity: alert.severity,
            title: alert.title,
            message: alert.message,
            source: alert.source,
            scopeJson: JSON.stringify(alert.scope),
            fingerprint: alert.fingerprint,
            tradingImpact: alert.tradingImpact,
            metadataJson: JSON.stringify(alert.metadata),
            createdAt: alert.createdAt,
            acknowledgedAt: alert.acknowledgedAt ?? null,
          })
          .onConflictDoUpdate({
            target: alertEvents.id,
            set: {
              severity: alert.severity,
              title: alert.title,
              message: alert.message,
              source: alert.source,
              scopeJson: JSON.stringify(alert.scope),
              fingerprint: alert.fingerprint,
              tradingImpact: alert.tradingImpact,
              metadataJson: JSON.stringify(alert.metadata),
              createdAt: alert.createdAt,
              acknowledgedAt: alert.acknowledgedAt ?? null,
            },
          })
          .run();
      }
    }

    if (state.alertDeliveries.length > 0) {
      for (const delivery of state.alertDeliveries) {
        tx.insert(alertDeliveries)
          .values({
            id: delivery.id,
            alertId: delivery.alertId,
            channel: delivery.channel,
            provider: delivery.provider,
            status: delivery.status,
            attemptedAt: delivery.attemptedAt,
            destination: delivery.destination,
            redactedMessage: delivery.redactedMessage,
            error: delivery.error ?? null,
          })
          .onConflictDoUpdate({
            target: alertDeliveries.id,
            set: {
              alertId: delivery.alertId,
              channel: delivery.channel,
              provider: delivery.provider,
              status: delivery.status,
              attemptedAt: delivery.attemptedAt,
              destination: delivery.destination,
              redactedMessage: delivery.redactedMessage,
              error: delivery.error ?? null,
            },
          })
          .run();
      }
    }

    writeAppState(tx, "paper_portfolio", state.paper ?? null, now);
    writeAppState(tx, "alert_router_state", state.alertRouterState, now);
    writeAppState(tx, "audit_events", state.auditEvents, now);
    writeAppState(tx, "kill_switch", state.killSwitch ?? null, now);
    writeAppState(tx, "action_log", state.actionLog, now);
  });
}

function readAppState<T>(db: ReturnType<typeof getDb>, key: string): T | undefined {
  const row = db.select().from(appState).where(eq(appState.key, key)).all()[0];
  if (!row) return undefined;
  return JSON.parse(row.valueJson) as T;
}

type SqliteTx = Parameters<Parameters<ReturnType<typeof getDb>["transaction"]>[0]>[0];

function writeAppState(
  tx: SqliteTx,
  key: string,
  value: unknown,
  updatedAt: string,
) {
  tx.insert(appState)
    .values({
      key,
      valueJson: JSON.stringify(value),
      updatedAt,
    })
    .onConflictDoUpdate({
      target: appState.key,
      set: {
        valueJson: JSON.stringify(value),
        updatedAt,
      },
    })
    .run();
}

function mapMarketRow(row: typeof marketSnapshots.$inferSelect): MarketSnapshot {
  return {
    id: row.id,
    venue: row.venue as MarketSnapshot["venue"],
    base: row.base as MarketSnapshot["base"],
    quote: row.quote as MarketSnapshot["quote"],
    instrumentType: row.instrumentType as MarketSnapshot["instrumentType"],
    price: row.price,
    markPrice: row.markPrice ?? undefined,
    indexPrice: row.indexPrice ?? undefined,
    fundingRate8h: row.fundingRate8h ?? undefined,
    openInterestUsd: row.openInterestUsd ?? undefined,
    volume24hUsd: row.volume24hUsd,
    volatility30d: row.volatility30d,
    change24hPct: row.change24hPct,
    timestamp: row.timestamp,
    orderBook: {
      bid: row.price,
      ask: row.price,
      bidDepthUsd: row.bidDepthUsd,
      askDepthUsd: row.askDepthUsd,
      spreadBps: row.spreadBps,
    },
  };
}

function mapSignalRow(
  row: typeof relativeValueSignals.$inferSelect,
): RelativeValueSignal {
  return {
    id: row.id,
    kind: row.kind as RelativeValueSignal["kind"],
    assetPair: row.assetPair,
    venue: row.venue,
    direction: row.direction as RelativeValueSignal["direction"],
    confidence: row.confidence,
    expectedEdgeBps: row.expectedEdgeBps,
    riskScore: row.riskScore,
    liquidityScore: row.liquidityScore,
    opportunityScore: row.opportunityScore,
    explanation: row.explanation,
    eligibleForPaperTrading: row.eligibleForPaperTrading,
    eligibleForLiveTrading: row.eligibleForLiveTrading,
    timestamp: row.timestamp,
  };
}

function mapRiskAlertRow(row: typeof riskAlerts.$inferSelect) {
  return {
    id: row.id,
    severity: row.severity as MarketRiskState["activeAlerts"][number]["severity"],
    category: row.category as MarketRiskState["activeAlerts"][number]["category"],
    title: row.title,
    explanation: row.explanation,
    source: row.source,
    timestamp: row.timestamp,
    restrictions: JSON.parse(row.restrictionsJson),
  };
}

function mapBacktestRow(row: typeof backtestRuns.$inferSelect): BacktestReport {
  return JSON.parse(row.reportJson) as BacktestReport;
}

function mapAlertEventRow(row: typeof alertEvents.$inferSelect): AlertEvent {
  return {
    id: row.id,
    severity: row.severity as AlertEvent["severity"],
    title: row.title,
    message: row.message,
    source: row.source,
    scope: JSON.parse(row.scopeJson),
    fingerprint: row.fingerprint,
    tradingImpact: row.tradingImpact,
    metadata: JSON.parse(row.metadataJson),
    createdAt: row.createdAt,
    acknowledgedAt: row.acknowledgedAt ?? undefined,
  };
}

function mapAlertDeliveryRow(
  row: typeof alertDeliveries.$inferSelect,
): AlertDelivery {
  return {
    id: row.id,
    alertId: row.alertId,
    channel: row.channel as AlertDelivery["channel"],
    provider: row.provider,
    status: row.status as AlertDelivery["status"],
    attemptedAt: row.attemptedAt,
    destination: row.destination,
    redactedMessage: row.redactedMessage,
    error: row.error ?? undefined,
  };
}