import { createAuditTrail } from "@/lib/audit/audit-log";
import {
  dailyDigestAlert,
  edgeScoreboardToAlertEvents,
  riskAlertToAlertEvent,
  signalToTradeableAlert,
  systemTrustToAlertEvents,
} from "@/lib/alerts/from-domain";
import { routeAlert } from "@/lib/alerts/router";
import { getDefaultConnector } from "@/lib/data/connectors";
import { buildDataProvenance } from "@/lib/data/provenance";
import { evaluateDataQuality } from "@/lib/data/quality";
import type { AuditEvent } from "@/lib/domain/types";
import { applyEdgeScoreboardPolicy } from "@/lib/edge/policy";
import { reconcileDryRunAttempts } from "@/lib/execution/dry-run-executor";
import { loadEvolverEvidenceReport } from "@/lib/evidence/evolver-import";
import {
  appendEvolverRecoverySnapshot,
  evaluateEvolverRecoveryWatchdog,
} from "@/lib/evidence/evolver-watchdog";
import {
  evaluateLiveTradeRequest,
  readLiveTradingSettings,
} from "@/lib/live/live-trading";
import { llmConfigured, readLlmSettings } from "@/lib/llm/settings";
import { computeFromData, refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { evaluateTinyLiveReadiness } from "@/lib/readiness/tiny-live-readiness";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { evaluateOperationalRunbook } from "@/lib/runbook/operational-runbook";
import { emptyPaperPortfolio } from "@/lib/state/local-store";
import { getStateStore } from "@/lib/state/store-factory";
import { buildSignalJournal } from "@/lib/signals/signal-journal";

export async function buildDashboardState() {
  const connector = getDefaultConnector();
  const store = getStateStore();
  let persisted = store.read();

  if (!persisted.data) {
    await refreshAndPersistMarketState();
    persisted = store.read();
  }

  const data = persisted.data!;
  const dataQuality =
    persisted.dataQuality ??
    evaluateDataQuality(data, {
      connectorId: connector.id,
      connectorLabel: connector.label,
      mode: connector.mode,
    });
  const persistedPaper = persisted.paper ?? emptyPaperPortfolio();
  const computed = computeFromData(data, dataQuality, {
    paper: persistedPaper,
    schedulerStatus: persisted.schedulerStatus,
    alertDeliveries: persisted.alertDeliveries,
    killSwitch: persisted.killSwitch,
  });
  const rawSignals = persisted.signals ?? computed.signals;
  const risk = persisted.risk ?? computed.risk;
  const backtest = persisted.backtest ?? computed.backtest;
  const paper = persistedPaper;
  const edgePolicy = applyEdgeScoreboardPolicy({
    signals: rawSignals,
    paper,
    updatedAt: data.generatedAt,
  });
  const signals = edgePolicy.signals;
  const signalJournal = buildSignalJournal({
    signals,
    timestamp: data.generatedAt,
  }).entries;
  const dataProvenance = buildDataProvenance(data, {
    id: connector.id,
    label: connector.label,
  });
  const edgeScoreboard = edgePolicy.scoreboard;
  const evolverEvidence = loadEvolverEvidenceReport(
    undefined,
    new Date(data.generatedAt),
  );
  const recoverySnapshotUpdate = appendEvolverRecoverySnapshot(
    persisted.evolverRecoverySnapshots,
    evolverEvidence,
  );
  let evolverRecoverySnapshots = recoverySnapshotUpdate.snapshots;
  if (recoverySnapshotUpdate.appended) {
    const nextPersisted = {
      ...persisted,
      evolverRecoverySnapshots,
    };
    try {
      store.write(nextPersisted);
      persisted = nextPersisted;
    } catch {
      evolverRecoverySnapshots = [
        recoverySnapshotUpdate.current,
        ...(persisted.evolverRecoverySnapshots ?? []),
      ].slice(0, 72);
    }
  }
  const evolverRecoveryWatchdog = evaluateEvolverRecoveryWatchdog(
    evolverEvidence,
    evolverRecoverySnapshots,
  );
  const systemTrust = evaluateSystemTrust({
    dataQuality,
    risk,
    schedulerStatus: persisted.schedulerStatus,
    alertDeliveries: persisted.alertDeliveries,
    killSwitch: persisted.killSwitch,
    paper,
    now: new Date(data.generatedAt),
  });
  const envLiveSettings = readLiveTradingSettings();
  const liveSettings = {
    ...envLiveSettings,
    killSwitchActive:
      envLiveSettings.killSwitchActive || Boolean(persisted.killSwitch?.active),
  };
  const llmSettings = readLlmSettings();
  const llmHasApiKey = Boolean(llmSettings.apiKey?.trim());
  const llmIsConfigured = llmConfigured(llmSettings);
  const executionReconciliation = reconcileDryRunAttempts(
    persisted.liveTradeAttempts,
    new Date(data.generatedAt),
  );
  const operationalRunbook = evaluateOperationalRunbook({
    dataQuality,
    systemTrust,
    schedulerStatus: persisted.schedulerStatus,
    alertDeliveries: persisted.alertDeliveries,
    paper,
    executionReconciliation,
    killSwitch: persisted.killSwitch,
    now: new Date(data.generatedAt),
  });
  const tinyLiveReadiness = evaluateTinyLiveReadiness({
    dataQuality,
    systemTrust,
    edgeScoreboard,
    paper,
    executionReconciliation,
    operationalRunbook,
    evolverEvidence,
    now: new Date(data.generatedAt),
  });
  const alertEvents =
    persisted.alertEvents.length > 0
      ? persisted.alertEvents
      : [
          ...risk.activeAlerts.map(riskAlertToAlertEvent),
          ...systemTrustToAlertEvents(systemTrust),
          ...edgeScoreboardToAlertEvents(edgeScoreboard),
          ...signals
            .filter((signal) => signal.eligibleForPaperTrading)
            .slice(0, 4)
            .map(signalToTradeableAlert),
          dailyDigestAlert({ risk, paper, signalCount: signals.length }),
        ];
  const alertRoutingPreview = alertEvents.slice(0, 4).map((alert) =>
    routeAlert(alert, {
      telegramChatIds: listFromEnv(process.env.TELEGRAM_AUTHORIZED_CHAT_IDS, [
        "dry-run-chat",
      ]),
      smsNumbers: listFromEnv(process.env.TWILIO_TO_NUMBERS, ["dry-run-sms"]),
      quietHours: {
        enabled: process.env.ALERT_QUIET_HOURS_ENABLED === "true",
        startHourLocal: numberFromEnv(process.env.ALERT_QUIET_HOURS_START, 22),
        endHourLocal: numberFromEnv(process.env.ALERT_QUIET_HOURS_END, 7),
      },
      escalationMinutes: numberFromEnv(process.env.ALERT_ESCALATION_MINUTES, 15),
      now: new Date(risk.updatedAt),
    }),
  );
  const liveEvaluation =
    signals[0] &&
    evaluateLiveTradeRequest({
      signal: signals[0],
      requestedNotionalUsd: 100,
      settings: liveSettings,
      manualConfirmation: false,
      currentDailyPnlUsd: paper.dailyPnlUsd,
      systemTrust,
    });
  const auditEvents: AuditEvent[] = createAuditTrail({
    data,
    signals,
    risk,
    backtest,
    paper,
  });
  const storedAuditEvents = persisted.auditEvents.length
    ? persisted.auditEvents
    : auditEvents;

  return {
    connector: {
      id: connector.id,
      label: connector.label,
      mode: connector.mode,
      needsApiKey: connector.needsApiKey,
    },
    data,
    dataQuality,
    dataProvenance,
    dataFreshness: {
      generatedAt: data.generatedAt,
      ageLabel: `${dataQuality.dataAgeMinutes.toFixed(1)} min`,
      stale: dataQuality.status !== "healthy",
    },
    signals,
    signalJournal,
    risk,
    backtest,
    paper,
    equityHistory: [
      {
        timestamp: data.generatedAt,
        equity: paper.equityUsd,
        cashUsd: paper.cashUsd,
        openPositions: paper.positions.length,
        realizedPnlUsd: paper.realizedPnlUsd,
      },
    ],
    edgeScoreboard,
    evolverEvidence,
    evolverRecoveryWatchdog,
    edgePolicy: edgePolicy.decision,
    systemTrust,
    liveSettings,
    liveEvaluation,
    executionReconciliation,
    operationalRunbook,
    tinyLiveReadiness,
    llmStatus: {
      configured: llmIsConfigured,
      enabled: llmSettings.enabled,
      hasApiKey: llmHasApiKey,
      mode: llmIsConfigured ? ("live" as const) : ("offline" as const),
      model: llmSettings.model,
      baseUrl: llmSettings.baseUrl.replace(/\/\/.*@/, "//[REDACTED]@"),
      setupHint: llmIsConfigured
        ? "LLM calls will hit your configured provider."
        : !llmSettings.enabled
          ? "Set LLM_API_ENABLED=true and LLM_API_KEY to enable live analyst calls."
          : "LLM_API_ENABLED=true but LLM_API_KEY is missing. Analyst stays in offline RAG mode.",
    },
    alertEvents,
    alertRoutingPreview,
    alertDeliveries: persisted.alertDeliveries,
    liveTradeAttempts: persisted.liveTradeAttempts,
    actionLog: persisted.actionLog,
    killSwitch: persisted.killSwitch,
    schedulerStatus: persisted.schedulerStatus,
    auditEvents: storedAuditEvents,
  };
}

function listFromEnv(value: string | undefined, fallback: string[]): string[] {
  if (!value) return fallback;
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function numberFromEnv(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}
