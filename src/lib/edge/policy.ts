import type {
  EdgeScoreboard,
  EdgeScoreboardRow,
  PaperPortfolio,
  RelativeValueSignal,
  SignalKind,
} from "@/lib/domain/types";
import { buildEdgeScoreboard } from "@/lib/edge/scoreboard";

export interface EdgePolicyDecision {
  blockedKinds: SignalKind[];
  blockedSignalIds: string[];
  blockedSignalCount: number;
  reasonsByKind: Partial<Record<SignalKind, string>>;
}

export interface EdgePolicyResult {
  signals: RelativeValueSignal[];
  scoreboard: EdgeScoreboard;
  evidenceScoreboard: EdgeScoreboard;
  decision: EdgePolicyDecision;
}

export function applyEdgeScoreboardPolicy(input: {
  signals: RelativeValueSignal[];
  paper: PaperPortfolio;
  updatedAt?: string;
}): EdgePolicyResult {
  const evidenceScoreboard = buildEdgeScoreboard(input);
  const underperformingRows = evidenceScoreboard.rows.filter(
    (row) => row.status === "underperforming",
  );
  const reasonsByKind = Object.fromEntries(
    underperformingRows.map((row) => [row.kind, reasonForRow(row)]),
  ) as Partial<Record<SignalKind, string>>;
  const blockedKinds = underperformingRows.map((row) => row.kind);
  const blockedKindSet = new Set(blockedKinds);
  const blockedSignalIds: string[] = [];

  const signals = input.signals.map((signal) => {
    if (!blockedKindSet.has(signal.kind)) return clearEdgePolicy(signal);
    if (signal.eligibleForPaperTrading) blockedSignalIds.push(signal.id);
    const direction: RelativeValueSignal["direction"] =
      signal.direction === "risk_off" ? "risk_off" : "watch_only";
    const reason =
      reasonsByKind[signal.kind] ?? "Edge policy marked this family watch-only.";
    const edgePolicy: NonNullable<RelativeValueSignal["edgePolicy"]> = {
      action: "watch_only",
      source: "edge_scoreboard",
      reason,
    };

    return {
      ...signal,
      direction,
      eligibleForPaperTrading: false,
      eligibleForLiveTrading: false,
      edgePolicy,
      explanation: appendPolicyReason(signal.explanation, reason),
    };
  });

  return {
    signals,
    evidenceScoreboard,
    scoreboard: buildEdgeScoreboard({
      signals,
      paper: input.paper,
      updatedAt: input.updatedAt,
    }),
    decision: {
      blockedKinds,
      blockedSignalIds,
      blockedSignalCount: blockedSignalIds.length,
      reasonsByKind,
    },
  };
}

export function isBlockedByEdgePolicy(signal: RelativeValueSignal): boolean {
  return (
    signal.edgePolicy?.source === "edge_scoreboard" &&
    signal.edgePolicy.action === "watch_only"
  );
}

function clearEdgePolicy(signal: RelativeValueSignal): RelativeValueSignal {
  if (!signal.edgePolicy) return signal;
  const { edgePolicy: _edgePolicy, ...rest } = signal;
  return rest;
}

function reasonForRow(row: EdgeScoreboardRow): string {
  return `Edge policy: ${row.kind.replaceAll(
    "_",
    " ",
  )} is watch-only because closed paper outcomes are net negative (${row.closedCount} close(s), ${row.winRatePct.toFixed(
    1,
  )}% win rate, ${formatSignedUsd(row.totalPnlUsd)} total PnL).`;
}

function appendPolicyReason(explanation: string, reason: string): string {
  if (explanation.includes("Edge policy:")) return explanation;
  return `${explanation} ${reason}`;
}

function formatSignedUsd(value: number): string {
  return `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(2)}`;
}
