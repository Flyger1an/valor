import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, expect, it } from "vitest";
import { createAuditTrail } from "@/lib/audit/audit-log";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import type { LiveTradeAttempt } from "@/lib/domain/types";
import { computeFromData } from "@/lib/ops/recompute";
import { LocalStateStore, type ValorLocalState } from "@/lib/state/local-store";
import { getStateStore } from "@/lib/state/store-factory";
import { SqliteStateStore } from "@/lib/state/sqlite-store";

let dir: string | null = null;

afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = null;
});

describe("sqlite state store", () => {
  it("reconstructs dashboard state from a SQLite database after restart", () => {
    const path = tempDbPath();
    const state = buildState();
    const first = new SqliteStateStore(path);

    first.write(state);
    first.close();

    const second = new SqliteStateStore(path);
    const restored = second.read();
    second.close();

    expect(restored.data?.generatedAt).toBe(sampleMarketData.generatedAt);
    expect(restored.dataQuality?.status).toBe("healthy");
    expect(restored.signals?.length).toBe(state.signals?.length);
    expect(restored.risk?.state).toBe(state.risk?.state);
    expect(restored.backtest?.strategyName).toBe(state.backtest?.strategyName);
    expect(restored.paper?.positions.length).toBe(state.paper?.positions.length);
    expect(restored.auditEvents.length).toBeGreaterThan(0);
  });

  it("upserts normalized rows without duplicating logical refresh state", () => {
    const path = tempDbPath();
    const state = buildState();
    const store = new SqliteStateStore(path);

    store.write(state);
    store.write(state);
    store.close();

    const db = new DatabaseSync(path);
    const signalCount = countRows(db, "relative_value_signals");
    const marketCount = countRows(db, "market_snapshots");
    const qualityCount = countRows(db, "data_quality_reports");
    const liveAttemptCount = countRows(db, "live_trade_attempts");
    const snapshotCount = countRows(db, "app_state");
    db.close();

    expect(signalCount).toBe(state.signals?.length);
    expect(marketCount).toBe(sampleMarketData.markets.length);
    expect(qualityCount).toBe(1);
    expect(liveAttemptCount).toBe(state.liveTradeAttempts.length);
    expect(snapshotCount).toBe(1);
  });

  it("selects SQLite for file URLs and JSON fallback for unsupported URLs", () => {
    const path = tempDbPath();

    expect(getStateStore({ DATABASE_URL: `file:${path}` })).toBeInstanceOf(
      SqliteStateStore,
    );
    expect(
      getStateStore({ DATABASE_URL: "postgresql://valor:test@localhost/valor" }),
    ).toBeInstanceOf(LocalStateStore);
    expect(
      getStateStore({
        DATABASE_URL: `file:${path}`,
        VALOR_STATE_BACKEND: "json",
      }),
    ).toBeInstanceOf(LocalStateStore);
  });

  it("persists a backtest with a null sortino without aborting the state write", () => {
    // 802231a made sortino number|null (undefined when there's no downside deviation), but the
    // backtest_runs.sortino column was REAL NOT NULL, so a null sortino crashed the whole write.
    const path = tempDbPath();
    const state = buildState();
    const withNull = { ...state, backtest: { ...state.backtest!, sortino: null } };
    const store = new SqliteStateStore(path);
    expect(() => store.write(withNull)).not.toThrow();

    const db = new DatabaseSync(path);
    const col = (
      db.prepare("PRAGMA table_info(backtest_runs)").all() as Array<{
        name: string;
        notnull: number;
      }>
    ).find((c) => c.name === "sortino");
    expect(col?.notnull).toBe(0); // migration made the column nullable
    const row = db.prepare("SELECT sortino FROM backtest_runs").get() as {
      sortino: number | null;
    };
    expect(row.sortino).toBeNull();
    db.close();
    store.close();

    const restored = new SqliteStateStore(path);
    expect(restored.read().backtest?.sortino).toBeNull(); // round-trips via report_json
    restored.close();
  });
});

function buildState(): ValorLocalState {
  const dataQuality = evaluateDataQuality(sampleMarketData, {
    connectorId: "sample-fixtures",
    connectorLabel: "Deterministic sample market bundle",
    mode: "sample",
    assessedAt: sampleMarketData.generatedAt,
  });
  const computed = computeFromData(sampleMarketData, dataQuality);
  const auditEvents = createAuditTrail({
    data: sampleMarketData,
    signals: computed.signals,
    risk: computed.risk,
    backtest: computed.backtest,
    paper: computed.paperPreview,
  });

  return {
    lastRefreshAt: sampleMarketData.generatedAt,
    data: sampleMarketData,
    dataQuality,
    signals: computed.signals,
    risk: computed.risk,
    backtest: computed.backtest,
    paper: computed.paperPreview,
    systemTrust: computed.systemTrust,
    liveTradeAttempts: [dryRunAttempt(computed.signals[0].id)],
    alertEvents: computed.alertEvents,
    alertDeliveries: [],
    alertRouterState: {
      lastSentByFingerprint: {},
      acknowledgedAlertIds: [],
    },
    auditEvents,
    schedulerStatus: {
      running: false,
      cycleCount: 0,
      consecutiveErrors: 0,
      lastMessage: "Test scheduler idle.",
    },
    actionLog: [
      {
        id: "action-test-refresh",
        timestamp: sampleMarketData.generatedAt,
        action: "data.refresh",
        status: "ok",
        message: "Test refresh.",
      },
    ],
  };
}

function dryRunAttempt(signalId: string): LiveTradeAttempt {
  return {
    id: "dry-run:test",
    mode: "dry_run",
    signalId,
    signalKind: "spot_perp_basis",
    assetPair: "BTC/USD",
    venue: "coinbase-spot---binance-perp",
    direction: "long_spot_short_perp",
    requestedNotionalUsd: 100,
    allowed: false,
    dryRun: true,
    status: "blocked",
    reasons: ["test guard block"],
    evaluationAuditLabel: "live.trade_attempt.blocked",
    preview: {
      id: "preview:test",
      mode: "dry_run",
      signalId,
      signalKind: "spot_perp_basis",
      assetPair: "BTC/USD",
      venue: "coinbase-spot---binance-perp",
      direction: "long_spot_short_perp",
      side: "spread",
      requestedNotionalUsd: 100,
      estimatedFeesUsd: 0.08,
      estimatedSlippageUsd: 0.05,
      estimatedTotalCostUsd: 100.13,
      createdAt: sampleMarketData.generatedAt,
      notes: ["test"],
    },
    fills: [],
    createdAt: sampleMarketData.generatedAt,
  };
}

function tempDbPath(): string {
  dir = mkdtempSync(join(tmpdir(), "valor-state-"));
  return join(dir, "valor.sqlite");
}

function countRows(db: DatabaseSync, table: string): number {
  const row = db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get() as {
    count: number;
  };
  return row.count;
}
