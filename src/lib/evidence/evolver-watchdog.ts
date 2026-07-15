import { createHash } from "node:crypto";
import type {
  EvolverBenchGuardReport,
  EvolverEvidenceReport,
  EvolverRecoveryMetricTrend,
  EvolverRecoverySnapshot,
  EvolverRecoveryTrendPosture,
  EvolverRecoveryWatchdogReport,
  SignalKind,
} from "@/lib/domain/types";

const MAX_RECOVERY_SNAPSHOTS = 72;
const SIGNAL_KINDS: SignalKind[] = [
  "spot_perp_basis",
  "funding_carry",
  "cross_exchange_premium",
  "btc_eth_ratio",
  "stablecoin_depeg",
  "pair_spread_zscore",
  "volatility_regime",
];

export function buildEvolverRecoverySnapshot(
  report: EvolverEvidenceReport,
): EvolverRecoverySnapshot {
  const plan = report.recoveryPlan;
  const gapScore = recoveryGapScore(report);
  const signature = signatureFor({
    sourceLabel: report.sourceLabel,
    evidenceStatus: report.status,
    recoveryStatus: plan.status,
    requiredPnlRecoveryUsd: plan.requiredPnlRecoveryUsd,
    additionalEvidenceDays: plan.additionalEvidenceDays,
    additionalClosedTrades: plan.additionalClosedTrades,
    winRateGapPct: plan.winRateGapPct,
    convergenceRateGapPct: plan.convergenceRateGapPct,
    confidenceHaircutPct: plan.confidenceHaircutPct,
    gapScore,
    benchCandidates: plan.benchCandidates,
    actionCodes: plan.actions.map((action) => action.code),
  });

  return {
    id: `evolver-recovery:${report.generatedAt}:${signature.slice(0, 12)}`,
    generatedAt: report.generatedAt,
    sourceLabel: report.sourceLabel,
    evidenceStatus: report.status,
    recoveryStatus: plan.status,
    requiredPnlRecoveryUsd: plan.requiredPnlRecoveryUsd,
    additionalEvidenceDays: plan.additionalEvidenceDays,
    additionalClosedTrades: plan.additionalClosedTrades,
    winRateGapPct: plan.winRateGapPct,
    convergenceRateGapPct: plan.convergenceRateGapPct,
    confidenceHaircutPct: plan.confidenceHaircutPct,
    gapScore,
    benchCandidates: plan.benchCandidates,
    actionCodes: plan.actions.map((action) => action.code),
    signature,
  };
}

export function appendEvolverRecoverySnapshot(
  snapshots: EvolverRecoverySnapshot[] | undefined,
  report: EvolverEvidenceReport,
): {
  snapshots: EvolverRecoverySnapshot[];
  current: EvolverRecoverySnapshot;
  appended: boolean;
} {
  const current = buildEvolverRecoverySnapshot(report);
  const history = sanitizeSnapshots(snapshots);
  const latest = history[0];

  if (latest?.signature === current.signature) {
    return { snapshots: history, current: latest, appended: false };
  }

  return {
    snapshots: [current, ...history].slice(0, MAX_RECOVERY_SNAPSHOTS),
    current,
    appended: true,
  };
}

export function evaluateEvolverRecoveryWatchdog(
  report: EvolverEvidenceReport,
  snapshots: EvolverRecoverySnapshot[] | undefined,
): EvolverRecoveryWatchdogReport {
  const history = sanitizeSnapshots(snapshots);
  const current = history[0] ?? buildEvolverRecoverySnapshot(report);
  const previous = history.find(
    (snapshot) => snapshot.signature !== current.signature,
  );
  const benchGuard = buildBenchGuard(report.recoveryPlan.benchCandidates);
  const metrics = trendMetrics(current, previous);
  const posture = postureFor(report, current, previous);

  return {
    id: `evolver-watchdog:${report.generatedAt}`,
    generatedAt: report.generatedAt,
    posture,
    summary: summaryFor(posture, current, previous, benchGuard),
    snapshotCount: history.length,
    current,
    previous,
    metrics,
    benchGuard,
  };
}

export function benchedSignalKindsFromEvidence(
  report: EvolverEvidenceReport | undefined,
): SignalKind[] {
  return matchingSignalKinds(report?.recoveryPlan.benchCandidates ?? []);
}

function recoveryGapScore(report: EvolverEvidenceReport): number {
  const plan = report.recoveryPlan;
  const score =
    plan.requiredPnlRecoveryUsd +
    plan.additionalEvidenceDays * 100 +
    plan.additionalClosedTrades * 50 +
    plan.winRateGapPct * 100 +
    plan.convergenceRateGapPct * 100 +
    (plan.confidenceHaircutPct ?? 0) * 25;

  return round(score, 2);
}

function postureFor(
  report: EvolverEvidenceReport,
  current: EvolverRecoverySnapshot,
  previous: EvolverRecoverySnapshot | undefined,
): EvolverRecoveryTrendPosture {
  if (!report.configured || report.status === "empty") return "unavailable";
  if (current.recoveryStatus === "clear") return "clear";
  if (!previous) return "new";

  const delta = round(current.gapScore - previous.gapScore, 2);
  if (delta < -1) return "improving";
  if (delta > 1) return "deteriorating";
  return "flat";
}

function trendMetrics(
  current: EvolverRecoverySnapshot,
  previous: EvolverRecoverySnapshot | undefined,
): EvolverRecoveryMetricTrend[] {
  return [
    metric("gap_score", "Gap Score", "score", current.gapScore, previous?.gapScore),
    metric(
      "required_pnl_recovery_usd",
      "PnL Recovery",
      "usd",
      current.requiredPnlRecoveryUsd,
      previous?.requiredPnlRecoveryUsd,
    ),
    metric(
      "additional_evidence_days",
      "Days Gap",
      "days",
      current.additionalEvidenceDays,
      previous?.additionalEvidenceDays,
    ),
    metric(
      "additional_closed_trades",
      "Closes Gap",
      "trades",
      current.additionalClosedTrades,
      previous?.additionalClosedTrades,
    ),
    metric(
      "win_rate_gap_pct",
      "Win Gap",
      "pp",
      current.winRateGapPct,
      previous?.winRateGapPct,
    ),
    metric(
      "convergence_rate_gap_pct",
      "Convergence Gap",
      "pp",
      current.convergenceRateGapPct,
      previous?.convergenceRateGapPct,
    ),
    metric(
      "confidence_haircut_pct",
      "Confidence Haircut",
      "pct",
      current.confidenceHaircutPct ?? 0,
      previous?.confidenceHaircutPct,
    ),
  ];
}

function metric(
  key: EvolverRecoveryMetricTrend["key"],
  label: string,
  unit: EvolverRecoveryMetricTrend["unit"],
  current: number,
  previous: number | undefined,
): EvolverRecoveryMetricTrend {
  if (previous === undefined) {
    return { key, label, unit, current, direction: "new" };
  }

  const delta = round(current - previous, unit === "usd" ? 2 : 1);
  const direction =
    Math.abs(delta) < (unit === "usd" ? 1 : 0.1)
      ? "flat"
      : delta < 0
        ? "improved"
        : "deteriorated";

  return {
    key,
    label,
    unit,
    current,
    previous,
    delta,
    direction,
  };
}

function buildBenchGuard(benchCandidates: string[]): EvolverBenchGuardReport {
  const signalKindMatches = matchingSignalKinds(benchCandidates);
  const active = benchCandidates.length > 0;

  return {
    active,
    benchedCandidates: benchCandidates,
    matchingSignalKinds: signalKindMatches,
    summary: active
      ? signalKindMatches.length > 0
        ? `Bench guard active for ${benchCandidates.join(
            ", ",
          )}; matching v0.2 signal kind(s): ${signalKindMatches.join(", ")}.`
        : `Bench guard active for ${benchCandidates.join(
            ", ",
          )}; no exact v0.2 signal-kind matches yet.`
      : "Bench guard inactive; no imported Evolver bench candidates.",
  };
}

function matchingSignalKinds(benchCandidates: string[]): SignalKind[] {
  const valid = new Set<SignalKind>(SIGNAL_KINDS);
  const normalized = new Set(benchCandidates.map((candidate) => candidate.trim()));
  return SIGNAL_KINDS.filter(
    (kind) => valid.has(kind) && normalized.has(kind),
  );
}

function summaryFor(
  posture: EvolverRecoveryTrendPosture,
  current: EvolverRecoverySnapshot,
  previous: EvolverRecoverySnapshot | undefined,
  benchGuard: EvolverBenchGuardReport,
): string {
  if (posture === "clear") {
    return `clear: imported Evolver recovery gaps are closed. ${benchGuard.summary}`;
  }
  if (posture === "unavailable") {
    return `unavailable: recovery trend needs configured, usable imported evidence. ${benchGuard.summary}`;
  }
  if (!previous) {
    return `new: recovery watchdog baseline captured at gap score ${current.gapScore.toFixed(
      1,
    )}. ${benchGuard.summary}`;
  }

  const delta = round(current.gapScore - previous.gapScore, 2);
  const direction =
    posture === "improving"
      ? "improved"
      : posture === "deteriorating"
        ? "deteriorated"
        : "held flat";

  return `${posture}: recovery gap ${direction} by ${Math.abs(delta).toFixed(
    1,
  )} since the prior distinct snapshot. ${benchGuard.summary}`;
}

function sanitizeSnapshots(
  snapshots: EvolverRecoverySnapshot[] | undefined,
): EvolverRecoverySnapshot[] {
  return (snapshots ?? [])
    .filter((snapshot) => snapshot && typeof snapshot.signature === "string")
    .slice()
    .sort((a, b) => b.generatedAt.localeCompare(a.generatedAt));
}

function signatureFor(value: unknown): string {
  return createHash("sha256")
    .update(JSON.stringify(value))
    .digest("hex");
}

function round(value: number, digits: number): number {
  const multiplier = 10 ** digits;
  return Math.round(value * multiplier) / multiplier;
}
