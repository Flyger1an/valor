import { createAuditTrail } from "@/lib/audit/audit-log";
import {
  dailyDigestAlert,
  riskAlertToAlertEvent,
  signalToTradeableAlert,
} from "@/lib/alerts/from-domain";
import { routeAlert } from "@/lib/alerts/router";
import { getDefaultConnector } from "@/lib/data/connectors";
import type { AuditEvent } from "@/lib/domain/types";
import {
  evaluateLiveTradeRequest,
  readLiveTradingSettings,
} from "@/lib/live/live-trading";
import { llmConfigured, readLlmSettings } from "@/lib/llm/settings";
import { computeFromData, refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { emptyPaperPortfolio, LocalStateStore } from "@/lib/state/local-store";

export async function buildDashboardState() {
  const connector = getDefaultConnector();
  const store = new LocalStateStore();
  let persisted = store.read();

  if (!persisted.data) {
    await refreshAndPersistMarketState();
    persisted = store.read();
  }

  const data = persisted.data!;
  const computed = computeFromData(data);
  const signals = persisted.signals ?? computed.signals;
  const risk = persisted.risk ?? computed.risk;
  const backtest = persisted.backtest ?? computed.backtest;
  const paper = persisted.paper ?? emptyPaperPortfolio();
  const envLiveSettings = readLiveTradingSettings();
  const liveSettings = {
    ...envLiveSettings,
    killSwitchActive:
      envLiveSettings.killSwitchActive || Boolean(persisted.killSwitch?.active),
  };
  const llmSettings = readLlmSettings();
  const alertEvents =
    persisted.alertEvents.length > 0
      ? persisted.alertEvents
      : [
          ...risk.activeAlerts.map(riskAlertToAlertEvent),
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
      needsApiKey: connector.needsApiKey,
    },
    data,
    signals,
    risk,
    backtest,
    paper,
    liveSettings,
    liveEvaluation,
    llmStatus: {
      configured: llmConfigured(llmSettings),
      enabled: llmSettings.enabled,
      model: llmSettings.model,
      baseUrl: llmSettings.baseUrl.replace(/\/\/.*@/, "//[REDACTED]@"),
    },
    alertEvents,
    alertRoutingPreview,
    alertDeliveries: persisted.alertDeliveries,
    actionLog: persisted.actionLog,
    killSwitch: persisted.killSwitch,
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
