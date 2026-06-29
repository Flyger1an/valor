import { NextResponse } from "next/server";
import type { AuditEvent } from "@/lib/domain/types";
import { applyEdgeScoreboardPolicy } from "@/lib/edge/policy";
import {
  DryRunExecutor,
  reconcileDryRunAttempts,
} from "@/lib/execution/dry-run-executor";
import { readLiveTradingSettings } from "@/lib/live/live-trading";
import { requireOpsAuth } from "@/lib/ops/auth";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { simulatePaperPortfolio } from "@/lib/paper/paper-broker";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { getStateStore } from "@/lib/state/store-factory";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const blocked = requireOpsAuth(request, {
    access: "read",
    rateLimit: { scope: "ops.dry-run-execution.read", limit: 120, windowMs: 60_000 },
  });
  if (blocked) return blocked;

  const store = getStateStore();
  const state = store.read();
  const now = new Date();
  const executor = new DryRunExecutor();

  return NextResponse.json({
    ok: true,
    attempts: state.liveTradeAttempts,
    balances: await executor.listBalances(now),
    reconciliation: reconcileDryRunAttempts(state.liveTradeAttempts, now),
  });
}

export async function POST(request: Request) {
  const blocked = requireOpsAuth(request, {
    access: "write",
    rateLimit: { scope: "ops.dry-run-execution.write", limit: 20, windowMs: 60_000 },
  });
  if (blocked) return blocked;

  const store = getStateStore();
  let state = store.read();

  if (!state.signals || !state.risk) {
    await refreshAndPersistMarketState();
    state = store.read();
  }

  const body = await readBody(request);
  const paper =
    state.paper ??
    simulatePaperPortfolio({
      signals: state.signals!,
      risk: state.risk!,
      dataQuality: state.dataQuality,
      marketData: state.data,
    });
  const edgePolicy = applyEdgeScoreboardPolicy({
    signals: state.signals!,
    paper,
    updatedAt: state.data?.generatedAt,
  });
  const signals = edgePolicy.signals;
  const signal =
    signals.find((entry) => entry.id === body.signalId) ??
    signals.find((entry) => entry.opportunityScore > 0) ??
    signals[0];

  if (!signal) {
    return NextResponse.json(
      { ok: false, error: "No signal is available for dry-run execution." },
      { status: 409 },
    );
  }

  const now = new Date();
  const systemTrust = evaluateSystemTrust({
    dataQuality: state.dataQuality,
    risk: state.risk,
    schedulerStatus: state.schedulerStatus,
    alertDeliveries: state.alertDeliveries,
    killSwitch: state.killSwitch,
    paper,
    now,
  });
  const envLiveSettings = readLiveTradingSettings();
  const liveSettings = {
    ...envLiveSettings,
    killSwitchActive:
      envLiveSettings.killSwitchActive || Boolean(state.killSwitch?.active),
  };
  const requestedNotionalUsd = requestedNotionalFromBody(
    body.requestedNotionalUsd,
    liveSettings.maxTradeUsd,
  );
  const executor = new DryRunExecutor();
  const balances = await executor.listBalances(now);
  const attempt = await executor.placeOrder({
    signal,
    requestedNotionalUsd,
    settings: liveSettings,
    manualConfirmation: body.manualConfirmation === true,
    currentDailyPnlUsd: paper.dailyPnlUsd,
    systemTrust,
    now,
  });
  const auditEvent = liveAttemptAuditEvent(attempt);

  const updated = store.update((current) => ({
    ...current,
    signals,
    systemTrust,
    liveTradeAttempts: [attempt, ...current.liveTradeAttempts].slice(0, 100),
    auditEvents: [auditEvent, ...current.auditEvents].slice(0, 250),
  }));
  store.appendAction({
    action: "dry_run.execution",
    status: attempt.allowed ? "dry_run" : "error",
    message: attempt.allowed
      ? `Recorded dry-run order intent for ${attempt.assetPair} at ${attempt.venue}.`
      : `Dry-run order intent blocked for ${attempt.assetPair}: ${attempt.reasons.join(" ")}`,
    timestamp: attempt.createdAt,
  });

  return NextResponse.json({
    ok: true,
    attempt,
    balances,
    reconciliation: reconcileDryRunAttempts(updated.liveTradeAttempts, now),
    edgePolicyBlocks: edgePolicy.decision.blockedSignalCount,
    systemTrust: {
      status: systemTrust.status,
      blocksLiveTrading: systemTrust.blocksLiveTrading,
      summary: systemTrust.summary,
    },
  });
}

async function readBody(request: Request): Promise<{
  signalId?: string;
  requestedNotionalUsd?: unknown;
  manualConfirmation?: unknown;
}> {
  try {
    const parsed = (await request.json()) as Record<string, unknown>;
    return {
      signalId: typeof parsed.signalId === "string" ? parsed.signalId : undefined,
      requestedNotionalUsd: parsed.requestedNotionalUsd,
      manualConfirmation: parsed.manualConfirmation,
    };
  } catch {
    return {};
  }
}

function requestedNotionalFromBody(value: unknown, maxTradeUsd: number): number {
  const parsed = Number(value);
  if (Number.isFinite(parsed) && parsed > 0) return parsed;
  return Math.max(1, Math.min(100, maxTradeUsd));
}

function liveAttemptAuditEvent(
  attempt: Awaited<ReturnType<DryRunExecutor["placeOrder"]>>,
): AuditEvent {
  return {
    id: `audit:${attempt.id}`,
    timestamp: attempt.createdAt,
    actor: "live_guard",
    action: "live.trade_attempt",
    summary: attempt.allowed
      ? `Dry-run execution intent recorded for ${attempt.assetPair} at ${attempt.venue}.`
      : `Dry-run execution intent blocked for ${attempt.assetPair}.`,
    metadata: {
      signalId: attempt.signalId,
      requestedNotionalUsd: attempt.requestedNotionalUsd,
      allowed: attempt.allowed,
      dryRun: attempt.dryRun,
      reasonCount: attempt.reasons.length,
    },
  };
}
