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
import { evaluateDataQuality } from "@/lib/data/quality";
import type { AuditEvent } from "@/lib/domain/types";
import { applyEdgeScoreboardPolicy } from "@/lib/edge/policy";
import { reconcileDryRunAttempts } from "@/lib/execution/dry-run-executor";
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
  const edgeScoreboard = edgePolicy.scoreboard;
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
    signals,
    risk,
    backtest,
    paper,
    edgeScoreboard,
    edgePolicy: edgePolicy.decision,
    systemTrust,
    liveSettings,
    liveEvaluation,
    executionReconciliation,
    operationalRunbook,
    tinyLiveReadiness,
    llmStatus: {
      configured: llmConfigured(llmSettings),
      enabled: llmSettings.enabled,
      model: llmSettings.model,
      baseUrl: llmSettings.baseUrl.replace(/\/\/.*@/, "//[REDACTED]@"),
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
