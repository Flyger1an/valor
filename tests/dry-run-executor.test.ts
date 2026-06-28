import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import type { RelativeValueSignal } from "@/lib/domain/types";
import {
  DryRunExecutor,
  executeDryRunOrderIntent,
  reconcileDryRunAttempts,
} from "@/lib/execution/dry-run-executor";
import { readLiveTradingSettings } from "@/lib/live/live-trading";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

const now = new Date("2026-06-22T12:30:00.000Z");

describe("dry-run executor", () => {
  it("records blocked intents by default without producing fills", () => {
    const [signal] = generateRelativeValueSignals(sampleMarketData);
    const attempt = executeDryRunOrderIntent({
      signal,
      requestedNotionalUsd: 100,
      settings: readLiveTradingSettings({}),
      manualConfirmation: false,
      currentDailyPnlUsd: 0,
      now,
    });

    expect(attempt.allowed).toBe(false);
    expect(attempt.dryRun).toBe(true);
    expect(attempt.status).toBe("blocked");
    expect(attempt.fills).toHaveLength(0);
    expect(attempt.reasons).toContain("ENABLE_LIVE_TRADING is not true.");
    expect(attempt.reasons).toContain("Live kill switch is active.");
    expect(attempt.preview.notes[0]).toContain("no exchange client");
  });

  it("records a dry-run intent when existing guardrails pass", () => {
    const [signal] = generateRelativeValueSignals(sampleMarketData);
    const liveEligibleSignal: RelativeValueSignal = {
      ...signal,
      eligibleForLiveTrading: true,
    };
    const attempt = executeDryRunOrderIntent({
      signal: liveEligibleSignal,
      requestedNotionalUsd: 100,
      settings: readLiveTradingSettings({
        LIVE_TRADING_ENABLED: "true",
        LIVE_KILL_SWITCH: "false",
      }),
      manualConfirmation: true,
      currentDailyPnlUsd: 0,
      now,
    });

    expect(attempt.allowed).toBe(true);
    expect(attempt.status).toBe("dry_run_recorded");
    expect(attempt.reasons).toEqual([]);
    expect(attempt.fills).toHaveLength(1);
    expect(attempt.preview.estimatedFeesUsd).toBeGreaterThan(0);
    expect(attempt.preview.estimatedTotalCostUsd).toBeGreaterThan(100);
  });

  it("requires manual confirmation even when live settings try to relax it", () => {
    const [signal] = generateRelativeValueSignals(sampleMarketData);
    const liveEligibleSignal: RelativeValueSignal = {
      ...signal,
      eligibleForLiveTrading: true,
    };
    const attempt = executeDryRunOrderIntent({
      signal: liveEligibleSignal,
      requestedNotionalUsd: 100,
      settings: readLiveTradingSettings({
        LIVE_TRADING_ENABLED: "true",
        LIVE_KILL_SWITCH: "false",
        REQUIRE_MANUAL_LIVE_CONFIRMATION: "false",
      }),
      manualConfirmation: false,
      currentDailyPnlUsd: 0,
      now,
    });

    expect(attempt.allowed).toBe(false);
    expect(attempt.status).toBe("blocked");
    expect(attempt.fills).toHaveLength(0);
    expect(attempt.reasons).toContain(
      "Manual confirmation is required for dry-run execution.",
    );
  });

  it("supports local balance, fill, and cancel interfaces without a live venue", async () => {
    const [signal] = generateRelativeValueSignals(sampleMarketData);
    const liveEligibleSignal: RelativeValueSignal = {
      ...signal,
      eligibleForLiveTrading: true,
    };
    const executor = new DryRunExecutor();
    const balances = await executor.listBalances(now);
    const attempt = await executor.placeOrder({
      signal: liveEligibleSignal,
      requestedNotionalUsd: 100,
      settings: readLiveTradingSettings({
        LIVE_TRADING_ENABLED: "true",
        LIVE_KILL_SWITCH: "false",
      }),
      manualConfirmation: true,
      currentDailyPnlUsd: 0,
      now,
    });
    const fills = await executor.listFills(attempt);
    const cancelled = await executor.cancelOrder({ attempt, now });

    expect(balances.some((balance) => balance.venue === "dry-run")).toBe(true);
    expect(fills).toHaveLength(1);
    expect(cancelled.status).toBe("cancelled");
    expect(cancelled.fills).toHaveLength(0);
  });

  it("reconciles clean dry-run ledgers and flags impossible fill states", () => {
    const [signal] = generateRelativeValueSignals(sampleMarketData);
    const liveEligibleSignal: RelativeValueSignal = {
      ...signal,
      eligibleForLiveTrading: true,
    };
    const attempt = executeDryRunOrderIntent({
      signal: liveEligibleSignal,
      requestedNotionalUsd: 100,
      settings: readLiveTradingSettings({
        LIVE_TRADING_ENABLED: "true",
        LIVE_KILL_SWITCH: "false",
      }),
      manualConfirmation: true,
      currentDailyPnlUsd: 0,
      now,
    });

    const clean = reconcileDryRunAttempts([attempt], now);
    const broken = reconcileDryRunAttempts([{ ...attempt, fills: [] }], now);

    expect(clean.status).toBe("clean");
    expect(clean.allowedCount).toBe(1);
    expect(clean.dryRunFillCount).toBe(1);
    expect(broken.status).toBe("blocked");
    expect(
      broken.issues.some((issue) => issue.code === "allowed-attempt-without-fill"),
    ).toBe(true);
  });
});
