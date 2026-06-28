import type {
  Asset,
  ExecutionBalance,
  ExecutionFill,
  ExecutionOrderPreview,
  ExecutionOrderSide,
  ExecutionReconciliationIssue,
  ExecutionReconciliationReport,
  LiveTradeAttempt,
  LiveTradingSettings,
  RelativeValueSignal,
  SystemTrustVerdict,
} from "@/lib/domain/types";
import {
  evaluateLiveTradeRequest,
  readLiveTradingSettings,
} from "@/lib/live/live-trading";
import { round } from "@/lib/utils/math";

const DEFAULT_DRY_RUN_BALANCES: ExecutionBalance[] = [
  {
    venue: "dry-run",
    asset: "USD",
    available: 100_000,
    reserved: 0,
    mode: "dry_run",
    updatedAt: "1970-01-01T00:00:00.000Z",
  },
  {
    venue: "dry-run",
    asset: "BTC",
    available: 0,
    reserved: 0,
    mode: "dry_run",
    updatedAt: "1970-01-01T00:00:00.000Z",
  },
  {
    venue: "dry-run",
    asset: "ETH",
    available: 0,
    reserved: 0,
    mode: "dry_run",
    updatedAt: "1970-01-01T00:00:00.000Z",
  },
];

const DRY_RUN_CONFIRMATION_REASON =
  "Manual confirmation is required for dry-run execution.";

export interface ExecutionOrderRequest {
  signal: RelativeValueSignal;
  requestedNotionalUsd: number;
  settings?: LiveTradingSettings;
  manualConfirmation: boolean;
  currentDailyPnlUsd: number;
  systemTrust?: SystemTrustVerdict;
  now?: Date;
}

export interface ExchangeExecutor {
  readonly mode: "dry_run";
  listBalances(now?: Date): Promise<ExecutionBalance[]>;
  previewOrder(input: ExecutionOrderRequest): Promise<ExecutionOrderPreview>;
  placeOrder(input: ExecutionOrderRequest): Promise<LiveTradeAttempt>;
  cancelOrder(input: {
    attempt: LiveTradeAttempt;
    now?: Date;
    reason?: string;
  }): Promise<LiveTradeAttempt>;
  listFills(attempt: LiveTradeAttempt): Promise<ExecutionFill[]>;
}

export class DryRunExecutor implements ExchangeExecutor {
  readonly mode = "dry_run" as const;

  async listBalances(now = new Date()): Promise<ExecutionBalance[]> {
    const updatedAt = now.toISOString();
    return DEFAULT_DRY_RUN_BALANCES.map((balance) => ({
      ...balance,
      updatedAt,
    }));
  }

  async previewOrder(input: ExecutionOrderRequest): Promise<ExecutionOrderPreview> {
    return buildPreview(input);
  }

  async placeOrder(input: ExecutionOrderRequest): Promise<LiveTradeAttempt> {
    return executeDryRunOrderIntent(input);
  }

  async cancelOrder(input: {
    attempt: LiveTradeAttempt;
    now?: Date;
    reason?: string;
  }): Promise<LiveTradeAttempt> {
    return {
      ...input.attempt,
      allowed: false,
      status: "cancelled",
      reasons: [
        ...input.attempt.reasons,
        input.reason ?? "Dry-run order intent cancelled before any live placement path.",
      ],
      fills: [],
      createdAt: input.now?.toISOString() ?? input.attempt.createdAt,
    };
  }

  async listFills(attempt: LiveTradeAttempt): Promise<ExecutionFill[]> {
    return attempt.fills;
  }
}

export function executeDryRunOrderIntent(
  input: ExecutionOrderRequest,
): LiveTradeAttempt {
  const settings = input.settings ?? readLiveTradingSettings();
  const preview = buildPreview(input);
  const evaluation = evaluateLiveTradeRequest({
    signal: input.signal,
    requestedNotionalUsd: input.requestedNotionalUsd,
    settings,
    manualConfirmation: input.manualConfirmation,
    currentDailyPnlUsd: input.currentDailyPnlUsd,
    systemTrust: input.systemTrust,
  });
  const reasons = [...evaluation.reasons];

  if (!evaluation.dryRun) {
    reasons.push("Dry-run executor refuses non-dry-run live placement mode.");
  }

  if (!input.manualConfirmation) {
    reasons.push(DRY_RUN_CONFIRMATION_REASON);
  }

  const allowed = evaluation.allowed && evaluation.dryRun && input.manualConfirmation;
  const createdAt = input.now?.toISOString() ?? new Date().toISOString();
  const id = `dry-run:${createdAt}:${input.signal.id}`;
  const fills = allowed
    ? [
        {
          id: `fill:${id}`,
          orderIntentId: id,
          mode: "dry_run" as const,
          status: "dry_run" as const,
          assetPair: input.signal.assetPair,
          venue: input.signal.venue,
          notionalUsd: round(input.requestedNotionalUsd, 2),
          price: 0,
          feesUsd: preview.estimatedFeesUsd,
          createdAt,
        },
      ]
    : [];

  return {
    id,
    mode: "dry_run",
    signalId: input.signal.id,
    signalKind: input.signal.kind,
    assetPair: input.signal.assetPair,
    venue: input.signal.venue,
    direction: input.signal.direction,
    requestedNotionalUsd: round(input.requestedNotionalUsd, 2),
    allowed,
    dryRun: true,
    status: allowed ? "dry_run_recorded" : "blocked",
    reasons,
    evaluationAuditLabel: evaluation.auditLabel,
    preview,
    fills,
    createdAt,
  };
}

export function reconcileDryRunAttempts(
  attempts: LiveTradeAttempt[],
  now = new Date(),
): ExecutionReconciliationReport {
  const issues: ExecutionReconciliationIssue[] = [];
  const generatedAt = now.toISOString();

  for (const attempt of attempts) {
    if (attempt.mode !== "dry_run" || !attempt.dryRun) {
      issues.push({
        code: "non-dry-run-attempt",
        severity: "critical",
        scope: attempt.id,
        message: "Execution attempt is not marked as dry-run.",
      });
    }

    if (attempt.allowed && attempt.fills.length === 0) {
      issues.push({
        code: "allowed-attempt-without-fill",
        severity: "critical",
        scope: attempt.id,
        message: "Allowed dry-run attempt has no synthetic fill record.",
      });
    }

    if (!attempt.allowed && attempt.fills.length > 0) {
      issues.push({
        code: "blocked-attempt-with-fill",
        severity: "critical",
        scope: attempt.id,
        message: "Blocked dry-run attempt contains fill records.",
      });
    }

    for (const fill of attempt.fills) {
      if (fill.mode !== "dry_run" || fill.status !== "dry_run") {
        issues.push({
          code: "non-dry-run-fill",
          severity: "critical",
          scope: fill.id,
          message: "Fill is not marked as a synthetic dry-run fill.",
        });
      }
      if (Math.abs(fill.notionalUsd - attempt.requestedNotionalUsd) > 0.01) {
        issues.push({
          code: "fill-notional-mismatch",
          severity: "warning",
          scope: fill.id,
          message: "Synthetic fill notional differs from requested notional.",
        });
      }
    }
  }

  if (attempts.length === 0) {
    issues.push({
      code: "no-dry-run-attempts",
      severity: "info",
      scope: "execution",
      message: "No dry-run execution attempts have been recorded yet.",
    });
  }

  const criticalIssueCount = issues.filter(
    (issue) => issue.severity === "critical",
  ).length;
  const warningIssueCount = issues.filter(
    (issue) => issue.severity === "warning",
  ).length;
  const status =
    criticalIssueCount > 0
      ? "blocked"
      : warningIssueCount > 0
        ? "attention"
        : "clean";

  return {
    id: `execution-reconciliation:${generatedAt}`,
    mode: "dry_run",
    generatedAt,
    status,
    attemptCount: attempts.length,
    allowedCount: attempts.filter((attempt) => attempt.allowed).length,
    blockedCount: attempts.filter((attempt) => !attempt.allowed).length,
    dryRunFillCount: attempts.reduce(
      (sum, attempt) => sum + attempt.fills.length,
      0,
    ),
    totalNotionalUsd: round(
      attempts.reduce((sum, attempt) => sum + attempt.requestedNotionalUsd, 0),
      2,
    ),
    totalEstimatedCostUsd: round(
      attempts.reduce(
        (sum, attempt) => sum + attempt.preview.estimatedTotalCostUsd,
        0,
      ),
      2,
    ),
    issueCount: issues.length,
    criticalIssueCount,
    issues,
  };
}

function buildPreview(input: ExecutionOrderRequest): ExecutionOrderPreview {
  const createdAt = input.now?.toISOString() ?? new Date().toISOString();
  const requestedNotionalUsd = Math.max(0, input.requestedNotionalUsd);
  const feeBps = 8;
  const slippageBps = Math.max(1, round((100 - input.signal.liquidityScore) / 8, 2));
  const estimatedFeesUsd = round((requestedNotionalUsd * feeBps) / 10_000, 2);
  const estimatedSlippageUsd = round(
    (requestedNotionalUsd * slippageBps) / 10_000,
    2,
  );

  return {
    id: `preview:${createdAt}:${input.signal.id}`,
    mode: "dry_run",
    signalId: input.signal.id,
    signalKind: input.signal.kind,
    assetPair: input.signal.assetPair,
    venue: input.signal.venue,
    direction: input.signal.direction,
    side: sideForDirection(input.signal.direction),
    requestedNotionalUsd: round(requestedNotionalUsd, 2),
    estimatedFeesUsd,
    estimatedSlippageUsd,
    estimatedTotalCostUsd: round(
      requestedNotionalUsd + estimatedFeesUsd + estimatedSlippageUsd,
      2,
    ),
    createdAt,
    notes: [
      "Dry-run preview only; no exchange client is initialized.",
      `Fee estimate uses ${feeBps} bps; slippage estimate uses ${slippageBps} bps from signal liquidity.`,
    ],
  };
}

function sideForDirection(direction: RelativeValueSignal["direction"]): ExecutionOrderSide {
  if (
    direction === "long_spot_short_perp" ||
    direction === "buy_low_venue_sell_high_venue" ||
    direction === "long_first_short_second"
  ) {
    return "spread";
  }
  if (direction === "short_first_long_second" || direction === "short_perp_receive_funding") {
    return "spread";
  }
  return "buy";
}

export function primaryAssetFromPair(assetPair: string): Asset | null {
  const [base] = assetPair.split("/");
  if (
    base === "BTC" ||
    base === "ETH" ||
    base === "SOL" ||
    base === "USDC" ||
    base === "USDT" ||
    base === "DAI" ||
    base === "USD"
  ) {
    return base;
  }
  return null;
}
