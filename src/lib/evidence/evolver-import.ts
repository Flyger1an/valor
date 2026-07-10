import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import type {
  EvolverCalibrationSummary,
  EvolverEvidenceIssue,
  EvolverEvidenceReport,
  EvolverEvidenceStatus,
  EvolverRecoveryAction,
  EvolverRecoveryPlan,
  EvolverResearchLoopSummary,
  EvolverShadowSummary,
} from "@/lib/domain/types";

const RESEARCH_LEDGER_FILES = [
  ["research", "research_ledger.jsonl"],
  ["gate", "gate_research_ledger.jsonl"],
  ["fx", "fx_research_ledger.jsonl"],
  ["vol", "vol_research_ledger.jsonl"],
] as const;

const STARTING_EQUITY_USD = 100_000;
const MIN_IMPORTED_EVIDENCE_DAYS = 21;
const MIN_SHADOW_CLOSED_TRADES = 30;
const MIN_SHADOW_WIN_RATE_PCT = 50;
const MIN_SHADOW_CONVERGENCE_RATE_PCT = 50;
const MIN_POSITIVE_SHADOW_PNL_USD = 1;

type JsonObject = Record<string, unknown>;

export function loadEvolverEvidenceReport(
  sourceDir = process.env.VALOR_EVOLVER_EVIDENCE_DIR,
  now = new Date(),
): EvolverEvidenceReport {
  const generatedAt = now.toISOString();
  if (!sourceDir?.trim()) {
    return emptyReport({
      generatedAt,
      configured: false,
      sourceLabel: "not configured",
      status: "not_configured",
      summary:
        "No Evolver evidence directory is configured. Set VALOR_EVOLVER_EVIDENCE_DIR to import live soak ledgers.",
    });
  }

  const sourceLabel = basename(sourceDir);
  const shadow = summarizeShadow(sourceDir);
  const calibration = summarizeCalibration(sourceDir);
  const researchLoops = RESEARCH_LEDGER_FILES.map(([name, file]) =>
    summarizeResearchLoop(name, evidencePath(sourceDir, file)),
  ).filter((loop) => loop.cycleCount > 0);
  const timestamps = collectReportTimestamps(shadow, researchLoops);
  const firstTimestamp = timestamps[0];
  const lastTimestamp = timestamps.at(-1);
  const evidenceDays =
    firstTimestamp && lastTimestamp
      ? daySpan(firstTimestamp, lastTimestamp)
      : 0;
  const totalResearchCycles = researchLoops.reduce(
    (sum, loop) => sum + loop.cycleCount,
    0,
  );
  const surfacedCandidateCount = researchLoops.reduce(
    (sum, loop) => sum + loop.surfacedCount,
    0,
  );
  const issues = collectIssues({
    shadow,
    calibration,
    researchLoops,
    evidenceDays,
    totalResearchCycles,
    surfacedCandidateCount,
  });
  const status = statusFromIssues(issues, {
    shadow,
    researchLoops,
    totalResearchCycles,
  });
  const summary = summaryFor(status, {
    evidenceDays,
    shadow,
    totalResearchCycles,
    surfacedCandidateCount,
    issues,
  });
  const recoveryPlan = buildRecoveryPlan({
    status,
    evidenceDays,
    totalResearchCycles,
    surfacedCandidateCount,
    shadow,
    calibration,
    researchLoops,
  });

  return {
    id: `evolver-evidence:${generatedAt}`,
    generatedAt,
    status,
    configured: true,
    sourceLabel,
    summary,
    evidenceDays,
    firstTimestamp,
    lastTimestamp,
    totalResearchCycles,
    surfacedCandidateCount,
    shadow,
    calibration,
    researchLoops,
    issues,
    recoveryPlan,
  };
}

function emptyReport(input: {
  generatedAt: string;
  configured: boolean;
  sourceLabel: string;
  status: EvolverEvidenceStatus;
  summary: string;
}): EvolverEvidenceReport {
  return {
    id: `evolver-evidence:${input.generatedAt}`,
    generatedAt: input.generatedAt,
    status: input.status,
    configured: input.configured,
    sourceLabel: input.sourceLabel,
    summary: input.summary,
    evidenceDays: 0,
    totalResearchCycles: 0,
    surfacedCandidateCount: 0,
    researchLoops: [],
    issues: [],
    recoveryPlan: unavailableRecoveryPlan({
      status: input.status,
      summary: input.summary,
    }),
  };
}

function summarizeShadow(sourceDir: string): EvolverShadowSummary | undefined {
  const ledger = readJsonl(evidencePath(sourceDir, "shadow_analyst_ledger.jsonl"));
  const state = readJson(evidencePath(sourceDir, "shadow_analyst_state.json"));
  const closes = ledger.filter((entry) => entry.event === "close");
  if (ledger.length === 0 && !state) return undefined;

  const closedPnls = closes
    .map((entry) => finiteNumber(entry.shadow_pnl_pct))
    .filter((value): value is number => value !== undefined);
  const simPnls = closes
    .map((entry) => finiteNumber(entry.sim_pnl_pct))
    .filter((value): value is number => value !== undefined);
  const approximatedClosedPnlUsd = closes.reduce((sum, entry) => {
    const pnlPct = finiteNumber(entry.shadow_pnl_pct) ?? 0;
    const notional = finiteNumber(entry.notional) ?? 0;
    return sum + pnlPct * notional;
  }, 0);
  const approximatedSimPnlUsd = closes.reduce((sum, entry) => {
    const pnlPct = finiteNumber(entry.sim_pnl_pct) ?? 0;
    const notional = finiteNumber(entry.notional) ?? 0;
    return sum + pnlPct * notional;
  }, 0);
  const wins = closedPnls.filter((value) => value > 0).length;
  const converged = closes.filter((entry) => entry.converged === true).length;
  const equityUsd = finiteNumber(state?.equity);
  const lastClosedAt = latestTimestamp(
    closes
      .flatMap((entry) => [timestampFrom(entry), parseTimestamp(entry.closed)])
      .filter((value): value is string => Boolean(value)),
  );

  return {
    eventCount: ledger.length,
    closedTradeCount: closes.length,
    openPositionCount: arrayLength(state?.open),
    equityUsd,
    startingEquityUsd: STARTING_EQUITY_USD,
    reportedPnlUsd:
      equityUsd === undefined ? undefined : equityUsd - STARTING_EQUITY_USD,
    approximatedClosedPnlUsd,
    approximatedSimPnlUsd,
    winRatePct: closes.length ? (wins / closes.length) * 100 : 0,
    convergenceRatePct: closes.length ? (converged / closes.length) * 100 : 0,
    averageShadowPnlPct: average(closedPnls) * 100,
    medianShadowPnlPct: median(closedPnls) * 100,
    minimumShadowPnlPct: closedPnls.length ? Math.min(...closedPnls) * 100 : 0,
    maximumShadowPnlPct: closedPnls.length ? Math.max(...closedPnls) * 100 : 0,
    lastClosedAt,
  };
}

function summarizeCalibration(
  sourceDir: string,
): EvolverCalibrationSummary | undefined {
  const calibration = readJson(evidencePath(sourceDir, "sim_calibration.json"));
  if (!calibration) return undefined;

  const sampleSize = finiteNumber(calibration.n) ?? 0;
  const statedConfidenceMean = finiteNumber(calibration.stated_conf_mean);
  const realizedConvergenceRate = finiteNumber(calibration.realized_conv_rate);
  const meanDivergencePct = finiteNumber(calibration.mean_divergence_pct);
  const convergenceScale = finiteNumber(calibration.conv_scale);
  const version = stringValue(calibration.version);
  const updatedAt =
    timestampFrom({ ts: calibration.updated_epoch }) ??
    stringValue(calibration.updated_at);
  const confidenceGap =
    statedConfidenceMean !== undefined && realizedConvergenceRate !== undefined
      ? statedConfidenceMean - realizedConvergenceRate
      : 0;
  const status =
    sampleSize >= 30 && confidenceGap >= 0.2
      ? "overconfident"
      : sampleSize > 0
        ? "calibrated"
        : "unknown";

  return {
    sampleSize,
    statedConfidenceMean,
    realizedConvergenceRate,
    meanDivergencePct,
    convergenceScale,
    version,
    updatedAt,
    status,
  };
}

function summarizeResearchLoop(
  name: string,
  path: string,
): EvolverResearchLoopSummary {
  const rows = readJsonl(path);
  const familyCounts = new Map<string, number>();
  let surfacedCount = 0;
  let lastSurfacedSummary: string | undefined;
  const timestamps: string[] = [];

  for (const row of rows) {
    const family = stringValue(row.family);
    if (family) familyCounts.set(family, (familyCounts.get(family) ?? 0) + 1);
    if (row.surfaced === true) {
      surfacedCount += 1;
      lastSurfacedSummary = stringValue(row.summary) ?? lastSurfacedSummary;
    }
    const timestamp = timestampFrom(row);
    if (timestamp) timestamps.push(timestamp);
  }

  return {
    name,
    cycleCount: rows.length,
    surfacedCount,
    firstTimestamp: earliestTimestamp(timestamps),
    lastTimestamp: latestTimestamp(timestamps),
    lastSummary: stringValue(rows.at(-1)?.summary),
    lastSurfacedSummary,
    familyCounts: Array.from(familyCounts.entries())
      .map(([family, count]) => ({ family, count }))
      .sort((a, b) => b.count - a.count || a.family.localeCompare(b.family))
      .slice(0, 8),
  };
}

function collectIssues(input: {
  shadow?: EvolverShadowSummary;
  calibration?: EvolverCalibrationSummary;
  researchLoops: EvolverResearchLoopSummary[];
  evidenceDays: number;
  totalResearchCycles: number;
  surfacedCandidateCount: number;
}): EvolverEvidenceIssue[] {
  const issues: EvolverEvidenceIssue[] = [];

  if (!input.shadow && input.totalResearchCycles === 0) {
    issues.push({
      code: "evolver-evidence-empty",
      severity: "warning",
      message: "No Evolver soak ledgers were found in the configured directory.",
      evidence: "Expected shadow_analyst_ledger.jsonl or research ledger files.",
    });
    return issues;
  }

  if (
    input.evidenceDays > 0 &&
    input.evidenceDays < MIN_IMPORTED_EVIDENCE_DAYS
  ) {
    issues.push({
      code: "evolver-evidence-window-short",
      severity: "warning",
      message: "Imported Evolver evidence is shorter than the v0.2 readiness window.",
      evidence: `${input.evidenceDays} imported evidence day(s), minimum ${MIN_IMPORTED_EVIDENCE_DAYS} for tiny-live review.`,
    });
  }

  if (input.shadow) {
    if (input.shadow.closedTradeCount < MIN_SHADOW_CLOSED_TRADES) {
      issues.push({
        code: "evolver-shadow-sample-small",
        severity: "warning",
        message: "Imported shadow evidence has too few closed trades for stable inference.",
        evidence: `${input.shadow.closedTradeCount} closed shadow trade(s), target at least ${MIN_SHADOW_CLOSED_TRADES}.`,
      });
    }

    const pnl =
      input.shadow.reportedPnlUsd ?? input.shadow.approximatedClosedPnlUsd;
    if (input.shadow.closedTradeCount >= 10 && pnl < 0) {
      issues.push({
        code: "evolver-shadow-negative-pnl",
        severity: "critical",
        message: "Imported shadow evidence is net negative and must block promotion.",
        evidence: `${formatUsd(pnl)} PnL across ${input.shadow.closedTradeCount} closed shadow trade(s).`,
      });
    }

    if (
      input.shadow.closedTradeCount >= 10 &&
      input.shadow.winRatePct < MIN_SHADOW_WIN_RATE_PCT
    ) {
      issues.push({
        code: "evolver-shadow-win-rate-low",
        severity: "warning",
        message: "Imported shadow win rate is below break-even posture.",
        evidence: `${input.shadow.winRatePct.toFixed(1)}% win rate across ${input.shadow.closedTradeCount} closed shadow trade(s).`,
      });
    }

    if (
      input.shadow.closedTradeCount >= 10 &&
      input.shadow.convergenceRatePct < MIN_SHADOW_CONVERGENCE_RATE_PCT
    ) {
      issues.push({
        code: "evolver-shadow-convergence-low",
        severity: "warning",
        message: "Imported shadow convergence rate is weak.",
        evidence: `${input.shadow.convergenceRatePct.toFixed(1)}% convergence across ${input.shadow.closedTradeCount} closed shadow trade(s).`,
      });
    }
  }

  if (input.calibration?.status === "overconfident") {
    const stated = input.calibration.statedConfidenceMean ?? 0;
    const realized = input.calibration.realizedConvergenceRate ?? 0;
    issues.push({
      code: "evolver-calibration-overconfident",
      severity: stated - realized >= 0.3 ? "critical" : "warning",
      message: "Imported calibration shows stated confidence is too optimistic.",
      evidence: `Stated ${(stated * 100).toFixed(1)}%, realized ${(realized * 100).toFixed(1)}%, n=${input.calibration.sampleSize}.`,
    });
  }

  if (input.totalResearchCycles > 0 && input.surfacedCandidateCount === 0) {
    issues.push({
      code: "evolver-no-surfaced-candidates",
      severity: "info",
      message: "Research gates have not surfaced a promotable candidate.",
      evidence: `${input.totalResearchCycles} imported research cycle(s), 0 surfaced candidate(s).`,
    });
  }

  return issues;
}

function statusFromIssues(
  issues: EvolverEvidenceIssue[],
  input: {
    shadow?: EvolverShadowSummary;
    researchLoops: EvolverResearchLoopSummary[];
    totalResearchCycles: number;
  },
): EvolverEvidenceStatus {
  if (!input.shadow && input.totalResearchCycles === 0) return "empty";
  if (issues.some((issue) => issue.severity === "critical")) return "blocked";
  if (issues.some((issue) => issue.severity === "warning")) return "watch";
  return "healthy";
}

function summaryFor(
  status: EvolverEvidenceStatus,
  input: {
    evidenceDays: number;
    shadow?: EvolverShadowSummary;
    totalResearchCycles: number;
    surfacedCandidateCount: number;
    issues: EvolverEvidenceIssue[];
  },
): string {
  if (status === "empty") {
    return "Evolver evidence import is configured but no soak ledgers were found.";
  }

  const shadowText = input.shadow
    ? `${input.shadow.closedTradeCount} closed shadow trade(s), ${formatUsd(
        input.shadow.reportedPnlUsd ?? input.shadow.approximatedClosedPnlUsd,
      )} shadow PnL`
    : "no shadow book";
  const issueText =
    input.issues.length === 0
      ? "no imported-evidence issues"
      : `${input.issues.length} imported-evidence issue(s)`;

  return `${status}: ${input.evidenceDays}d imported window, ${shadowText}, ${input.totalResearchCycles} research cycle(s), ${input.surfacedCandidateCount} surfaced candidate(s), ${issueText}.`;
}

function unavailableRecoveryPlan(input: {
  status: EvolverEvidenceStatus;
  summary: string;
}): EvolverRecoveryPlan {
  const action: EvolverRecoveryAction =
    input.status === "not_configured"
      ? {
          code: "configure-evolver-evidence-dir",
          severity: "warning",
          title: "Configure Evolver evidence import",
          current: "VALOR_EVOLVER_EVIDENCE_DIR unset",
          target: "Mounted live soak ledger directory",
          gap: "No imported evidence can be scored yet.",
          rationale:
            "v0.2 cannot quantify recovery until the Evolver ledgers are mounted.",
        }
      : {
          code: "restore-evolver-ledgers",
          severity: "warning",
          title: "Restore Evolver soak ledgers",
          current: "Configured directory has no usable ledgers",
          target: "Shadow and research ledgers present",
          gap: "Need at least one usable soak ledger.",
          rationale:
            "The bridge needs shadow or research records before it can clear imported evidence.",
        };

  return {
    status: input.status === "empty" ? "empty" : "not_configured",
    summary: input.summary,
    minimumEvidenceDays: MIN_IMPORTED_EVIDENCE_DAYS,
    additionalEvidenceDays:
      input.status === "empty" ? MIN_IMPORTED_EVIDENCE_DAYS : 0,
    minimumClosedTrades: MIN_SHADOW_CLOSED_TRADES,
    additionalClosedTrades:
      input.status === "empty" ? MIN_SHADOW_CLOSED_TRADES : 0,
    minimumWinRatePct: MIN_SHADOW_WIN_RATE_PCT,
    winRateGapPct: 0,
    minimumConvergenceRatePct: MIN_SHADOW_CONVERGENCE_RATE_PCT,
    convergenceRateGapPct: 0,
    requiredPnlRecoveryUsd: 0,
    benchCandidates: [],
    actions: [action],
  };
}

function buildRecoveryPlan(input: {
  status: EvolverEvidenceStatus;
  evidenceDays: number;
  totalResearchCycles: number;
  surfacedCandidateCount: number;
  shadow?: EvolverShadowSummary;
  calibration?: EvolverCalibrationSummary;
  researchLoops: EvolverResearchLoopSummary[];
}): EvolverRecoveryPlan {
  if (input.status === "empty") {
    return unavailableRecoveryPlan({
      status: "empty",
      summary:
        "Restore Evolver soak ledgers before imported evidence can recover.",
    });
  }

  const shadowPnl =
    input.shadow?.reportedPnlUsd ?? input.shadow?.approximatedClosedPnlUsd;
  const additionalEvidenceDays = Math.max(
    0,
    MIN_IMPORTED_EVIDENCE_DAYS - input.evidenceDays,
  );
  const additionalClosedTrades = Math.max(
    0,
    MIN_SHADOW_CLOSED_TRADES - (input.shadow?.closedTradeCount ?? 0),
  );
  const requiredPnlRecoveryUsd =
    shadowPnl === undefined
      ? 0
      : roundMoney(Math.max(0, MIN_POSITIVE_SHADOW_PNL_USD - shadowPnl));
  const winRateGapPct = roundPct(
    Math.max(0, MIN_SHADOW_WIN_RATE_PCT - (input.shadow?.winRatePct ?? 0)),
  );
  const convergenceRateGapPct = roundPct(
    Math.max(
      0,
      MIN_SHADOW_CONVERGENCE_RATE_PCT -
        (input.shadow?.convergenceRatePct ?? 0),
    ),
  );
  const confidenceHaircutPct = confidenceHaircut(input.calibration);
  const benchCandidates = collectBenchCandidates({
    researchLoops: input.researchLoops,
    surfacedCandidateCount: input.surfacedCandidateCount,
    shadowNeedsRecovery:
      requiredPnlRecoveryUsd > 0 ||
      winRateGapPct > 0 ||
      convergenceRateGapPct > 0,
  });
  const actions: EvolverRecoveryAction[] = [];

  if (!input.shadow) {
    actions.push({
      code: "restore-shadow-book",
      severity: "critical",
      title: "Restore shadow book evidence",
      current: "No imported shadow book",
      target: `${MIN_SHADOW_CLOSED_TRADES} closed shadow trades with positive PnL`,
      gap: "shadow_analyst_ledger.jsonl or shadow_analyst_state.json is missing.",
      rationale:
        "Shadow performance is the bridge's main evidence for whether Evolver output can be trusted.",
    });
  }

  if (additionalEvidenceDays > 0) {
    actions.push({
      code: "extend-evidence-window",
      severity: "warning",
      title: "Extend imported soak window",
      current: `${input.evidenceDays} imported day(s)`,
      target: `${MIN_IMPORTED_EVIDENCE_DAYS} imported day(s)`,
      gap: `${additionalEvidenceDays} additional day(s) of usable evidence`,
      rationale:
        "The imported soak needs a full multi-week window before it can stop contributing a readiness warning.",
    });
  }

  if (input.shadow && additionalClosedTrades > 0) {
    actions.push({
      code: "collect-shadow-closes",
      severity: "warning",
      title: "Collect more closed shadow trades",
      current: `${input.shadow.closedTradeCount} closed shadow trade(s)`,
      target: `${MIN_SHADOW_CLOSED_TRADES} closed shadow trade(s)`,
      gap: `${additionalClosedTrades} additional close(s)`,
      rationale:
        "The shadow book needs enough closes to make win-rate and convergence checks meaningful.",
    });
  }

  if (shadowPnl !== undefined && requiredPnlRecoveryUsd > 0) {
    actions.push({
      code: "recover-shadow-pnl",
      severity: "critical",
      title: "Recover shadow PnL",
      current: formatUsd(shadowPnl),
      target: `${formatUsd(MIN_POSITIVE_SHADOW_PNL_USD)} or better`,
      gap: `${formatUsd(requiredPnlRecoveryUsd)} net recovery`,
      rationale:
        "Negative imported shadow PnL blocks promotion even when local v0.2 paper evidence improves.",
    });
  }

  if (input.shadow && winRateGapPct > 0) {
    actions.push({
      code: "repair-shadow-win-rate",
      severity: "warning",
      title: "Repair shadow win rate",
      current: `${input.shadow.winRatePct.toFixed(1)}%`,
      target: `${MIN_SHADOW_WIN_RATE_PCT.toFixed(1)}% or better`,
      gap: `+${winRateGapPct.toFixed(1)} percentage point(s)`,
      rationale:
        "The imported shadow book needs at least break-even hit-rate posture before the bridge stops warning.",
    });
  }

  if (input.shadow && convergenceRateGapPct > 0) {
    actions.push({
      code: "repair-shadow-convergence",
      severity: "warning",
      title: "Repair shadow convergence",
      current: `${input.shadow.convergenceRatePct.toFixed(1)}%`,
      target: `${MIN_SHADOW_CONVERGENCE_RATE_PCT.toFixed(1)}% or better`,
      gap: `+${convergenceRateGapPct.toFixed(1)} percentage point(s)`,
      rationale:
        "Low realized convergence means the simulator and shadow book are not yet agreeing often enough.",
    });
  }

  if (confidenceHaircutPct !== undefined && confidenceHaircutPct > 0) {
    const stated = input.calibration?.statedConfidenceMean ?? 0;
    const realized = input.calibration?.realizedConvergenceRate ?? 0;
    actions.push({
      code: "haircut-calibration-confidence",
      severity:
        input.calibration?.status === "overconfident"
          ? "critical"
          : "warning",
      title: "Haircut calibration confidence",
      current: `Stated ${(stated * 100).toFixed(1)}%, realized ${(realized * 100).toFixed(1)}%`,
      target: "Stated confidence no higher than realized convergence",
      gap: `Apply about ${confidenceHaircutPct.toFixed(1)}% confidence haircut`,
      rationale:
        "Overconfident calibration can make weak candidates look promotable before realized convergence supports them.",
    });
  }

  if (benchCandidates.length > 0) {
    actions.push({
      code: "bench-unsurfaced-families",
      severity: input.surfacedCandidateCount === 0 ? "info" : "warning",
      title: "Bench unsurfaced families",
      current: `${input.surfacedCandidateCount} surfaced candidate(s) across ${input.totalResearchCycles} research cycle(s)`,
      target: "At least one gated surfaced candidate before promotion consideration",
      gap: benchCandidates.join(", "),
      rationale:
        "Families that repeatedly fail research gates should remain out of promotion consideration while the bridge is blocked.",
    });
  }

  const planStatus = input.status === "healthy" ? "clear" : input.status;

  return {
    status: planStatus,
    summary: recoverySummary({
      status: planStatus,
      additionalEvidenceDays,
      additionalClosedTrades,
      requiredPnlRecoveryUsd,
      winRateGapPct,
      convergenceRateGapPct,
      confidenceHaircutPct,
      benchCandidates,
      actions,
    }),
    minimumEvidenceDays: MIN_IMPORTED_EVIDENCE_DAYS,
    additionalEvidenceDays,
    minimumClosedTrades: MIN_SHADOW_CLOSED_TRADES,
    additionalClosedTrades,
    minimumWinRatePct: MIN_SHADOW_WIN_RATE_PCT,
    winRateGapPct,
    minimumConvergenceRatePct: MIN_SHADOW_CONVERGENCE_RATE_PCT,
    convergenceRateGapPct,
    requiredPnlRecoveryUsd,
    confidenceHaircutPct,
    benchCandidates,
    actions,
  };
}

function confidenceHaircut(
  calibration: EvolverCalibrationSummary | undefined,
): number | undefined {
  const stated = calibration?.statedConfidenceMean;
  const realized = calibration?.realizedConvergenceRate;
  if (
    stated === undefined ||
    realized === undefined ||
    stated <= 0 ||
    realized >= stated
  ) {
    return undefined;
  }

  return roundPct(((stated - realized) / stated) * 100);
}

function collectBenchCandidates(input: {
  researchLoops: EvolverResearchLoopSummary[];
  surfacedCandidateCount: number;
  shadowNeedsRecovery: boolean;
}): string[] {
  const counts = new Map<string, number>();
  for (const loop of input.researchLoops) {
    for (const family of loop.familyCounts) {
      counts.set(family.family, (counts.get(family.family) ?? 0) + family.count);
    }
  }

  const families = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([family]) => family)
    .slice(0, 8);

  if (families.length > 0 && input.surfacedCandidateCount === 0) {
    return families;
  }
  if (families.length > 0 && input.shadowNeedsRecovery) {
    return families;
  }
  return input.shadowNeedsRecovery ? ["shadow_analyst"] : [];
}

function recoverySummary(input: {
  status: EvolverRecoveryPlan["status"];
  additionalEvidenceDays: number;
  additionalClosedTrades: number;
  requiredPnlRecoveryUsd: number;
  winRateGapPct: number;
  convergenceRateGapPct: number;
  confidenceHaircutPct?: number;
  benchCandidates: string[];
  actions: EvolverRecoveryAction[];
}): string {
  if (input.status === "clear") {
    return "Imported Evolver evidence has no recovery gaps against the v0.2 bridge thresholds.";
  }

  const gaps = [
    input.requiredPnlRecoveryUsd > 0
      ? `${formatUsd(input.requiredPnlRecoveryUsd)} shadow PnL recovery`
      : undefined,
    input.additionalEvidenceDays > 0
      ? `${input.additionalEvidenceDays} additional evidence day(s)`
      : undefined,
    input.additionalClosedTrades > 0
      ? `${input.additionalClosedTrades} additional shadow close(s)`
      : undefined,
    input.winRateGapPct > 0
      ? `+${input.winRateGapPct.toFixed(1)} pp win rate`
      : undefined,
    input.convergenceRateGapPct > 0
      ? `+${input.convergenceRateGapPct.toFixed(1)} pp convergence`
      : undefined,
    input.confidenceHaircutPct !== undefined && input.confidenceHaircutPct > 0
      ? `${input.confidenceHaircutPct.toFixed(1)}% confidence haircut`
      : undefined,
  ].filter((value): value is string => Boolean(value));

  const recoveryText =
    gaps.length > 0
      ? gaps.join(", ")
      : input.actions
          .slice(0, 3)
          .map((action) => action.title.toLowerCase())
          .join(", ");
  const benchText =
    input.benchCandidates.length > 0
      ? ` Bench ${input.benchCandidates.join(", ")}.`
      : "";

  return `${input.status}: ${recoveryText || "review imported evidence"} before imported Evolver blockers clear.${benchText}`;
}

function collectReportTimestamps(
  shadow: EvolverShadowSummary | undefined,
  researchLoops: EvolverResearchLoopSummary[],
): string[] {
  return [
    shadow?.lastClosedAt,
    ...researchLoops.flatMap((loop) => [
      loop.firstTimestamp,
      loop.lastTimestamp,
    ]),
  ]
    .filter((value): value is string => Boolean(value))
    .sort();
}

function readJsonl(path: string): JsonObject[] {
  if (!existsSync(path)) return [];

  return readFileSync(path, "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .flatMap((line) => {
      try {
        const parsed = JSON.parse(line) as unknown;
        return isObject(parsed) ? [parsed] : [];
      } catch {
        return [];
      }
    });
}

function evidencePath(sourceDir: string, filename: string): string {
  return join(/* turbopackIgnore: true */ sourceDir, filename);
}

function readJson(path: string): JsonObject | undefined {
  if (!existsSync(path)) return undefined;
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as unknown;
    return isObject(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0
    ? value.trim()
    : undefined;
}

function arrayLength(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function timestampFrom(value: JsonObject | undefined): string | undefined {
  if (!value) return undefined;
  for (const key of [
    "timestamp",
    "ts",
    "time",
    "created_at",
    "started_at",
    "ended_at",
    "generated_at",
    "entry_ts",
    "exit_ts",
    "closed",
  ]) {
    const timestamp = parseTimestamp(value[key]);
    if (timestamp) return timestamp;
  }
  return undefined;
}

function parseTimestamp(value: unknown): string | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    const millis = value > 10_000_000_000 ? value : value * 1000;
    return new Date(millis).toISOString();
  }

  if (typeof value !== "string" || value.trim().length === 0) {
    return undefined;
  }

  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}:00Z`
    : value;
  const millis = Date.parse(normalized);
  return Number.isFinite(millis) ? new Date(millis).toISOString() : undefined;
}

function earliestTimestamp(values: string[]): string | undefined {
  return values.slice().sort()[0];
}

function latestTimestamp(values: string[]): string | undefined {
  return values.slice().sort().at(-1);
}

function daySpan(firstTimestamp: string, lastTimestamp: string): number {
  const first = Date.parse(firstTimestamp);
  const last = Date.parse(lastTimestamp);
  if (!Number.isFinite(first) || !Number.isFinite(last)) return 0;
  return Math.max(1, Math.floor((last - first) / 86_400_000) + 1);
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = values.slice().sort((a, b) => a - b);
  const midpoint = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[midpoint - 1] + sorted[midpoint]) / 2
    : sorted[midpoint];
}

function formatUsd(value: number): string {
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

function roundMoney(value: number): number {
  return Math.round(value * 100) / 100;
}

function roundPct(value: number): number {
  return Math.round(value * 10) / 10;
}
