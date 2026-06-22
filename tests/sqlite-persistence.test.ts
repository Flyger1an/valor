import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb } from "@/db/client";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { computeFromData } from "@/lib/ops/recompute";
import {
  loadValorStateFromSqlite,
  persistValorStateToSqlite,
} from "@/lib/state/sqlite-persistence";

const TEST_DB = `/tmp/valor-test-${process.pid}.sqlite`;

beforeEach(() => {
  process.env.DATABASE_URL = `file:${TEST_DB}-${Date.now()}`;
  process.env.VALOR_DISABLE_SQLITE = "false";
  closeDb();
});

afterEach(() => {
  closeDb();
});

describe("sqlite persistence", () => {
  it("persists and reloads computed market state", () => {
    const computed = computeFromData(sampleMarketData);

    persistValorStateToSqlite({
      lastRefreshAt: sampleMarketData.generatedAt,
      data: sampleMarketData,
      signals: computed.signals,
      risk: computed.risk,
      backtest: computed.backtest,
      alertEvents: computed.alertEvents,
      alertDeliveries: [],
      alertRouterState: {
        lastSentByFingerprint: {},
        acknowledgedAlertIds: [],
      },
      auditEvents: [],
      actionLog: [],
    });

    const loaded = loadValorStateFromSqlite();
    expect(loaded).not.toBeNull();
    expect(loaded?.data?.markets.length).toBe(sampleMarketData.markets.length);
    expect(loaded?.signals?.length).toBe(computed.signals.length);
    expect(loaded?.risk?.state).toBe(computed.risk.state);
    expect(loaded?.backtest?.strategyName).toBe(computed.backtest.strategyName);
    expect(loaded?.alertEvents.length).toBe(computed.alertEvents.length);
  });
});