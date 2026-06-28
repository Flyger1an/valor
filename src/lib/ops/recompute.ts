import {
  dailyDigestAlert,
  edgeScoreboardToAlertEvents,
  riskAlertToAlertEvent,
  signalToTradeableAlert,
  systemTrustToAlertEvents,
} from "@/lib/alerts/from-domain";
import { runBasisCarryBacktest } from "@/lib/backtest/backtester";
import { getDefaultConnector } from "@/lib/data/connectors";
import { evaluateDataQuality } from "@/lib/data/quality";
import type {
  AlertDelivery,
  AlertEvent,
  AlertRouterConfig,
  AlertRouterState,
} from "@/lib/alerts/types";
import { createAuditTrail } from "@/lib/audit/audit-log";
import type {
  DataQualityReport,
  EdgeScoreboard,
  MarketDataBundle,
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
  SystemTrustVerdict,
} from "@/lib/domain/types";
import { applyEdgeScoreboardPolicy } from "@/lib/edge/policy";
import { simulatePaperPortfolio } from "@/lib/paper/paper-broker";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";
import { getStateStore } from "@/lib/state/store-factory";
import type { SchedulerStatus, StateStore } from "@/lib/state/local-store";
import type { KillSwitchState } from "@/lib/kill-switch/kill-switch";

export async function refreshAndPersistMarketState(
  options: {
    store?: StateStore;
    assessedAt?: Date;
  } = {},
) {
  const store = options.store ?? getStateStore();
  const previousState = store.read();
  const connector = getDefaultConnector();
  const data = await connector.fetchLatest();
  const dataQuality = evaluateDataQuality(data, {
    connectorId: connector.id,
    connectorLabel: connector.label,
    mode: connector.mode,
    assessedAt: options.assessedAt?.toISOString(),
  });
  const computed = computeFromData(data, dataQuality, {
    paper: previousState.paper,
    schedulerStatus: previousState.schedulerStatus,
    alertDeliveries: previousState.alertDeliveries,
    killSwitch: previousState.killSwitch,
    now: options.assessedAt,
  });

  const next = store.update((state) => ({
    ...state,
    lastRefreshAt: data.generatedAt,
    data,
    dataQuality,
    signals: computed.signals,
    risk: computed.risk,
    backtest: computed.backtest,
    systemTrust: computed.systemTrust,
    alertEvents: computed.alertEvents,
    auditEvents: createAuditTrail({
      data,
      signals: computed.signals,
      risk: computed.risk,
      backtest: computed.backtest,
      paper: previousState.paper ?? computed.paperPreview,
    }),
  }));

  store.appendAction({
    action: "data.refresh",
    status: dataQuality.blocksPaperTrading ? "error" : "ok",
    message: `Refreshed ${data.markets.length} markets via ${connector.label}; data quality ${dataQuality.status}.`,
    timestamp: data.generatedAt,
  });

  return { connector, data, dataQuality, computed, state: next };
}

export function computeFromData(
  data: MarketDataBundle,
  dataQuality?: DataQualityReport,
  options: {
    paper?: PaperPortfolio;
    schedulerStatus?: SchedulerStatus;
    alertDeliveries?: AlertDelivery[];
    killSwitch?: KillSwitchState;
    now?: Date;
  } = {},
) {
  const generatedSignals = generateRelativeValueSignals(data);
  const risk = evaluateMarketRisk(data);
  const backtest = runBasisCarryBacktest(data.backtestHistory);
  const evidencePaper =
    options.paper ??
    simulatePaperPortfolio({
      signals: generatedSignals,
      risk,
      dataQuality,
      marketData: data,
    });
  const edgePolicy = applyEdgeScoreboardPolicy({
    signals: generatedSignals,
    paper: evidencePaper,
    updatedAt: data.generatedAt,
  });
  const signals = edgePolicy.signals;
  const systemTrust = evaluateSystemTrust({
    dataQuality,
    risk,
    schedulerStatus: options.schedulerStatus,
    alertDeliveries: options.alertDeliveries,
    killSwitch: options.killSwitch,
    paper: evidencePaper,
    now: options.now ?? new Date(data.generatedAt),
  });
  const paperPreview = simulatePaperPortfolio({
    signals,
    risk,
    dataQuality,
    systemTrust,
    marketData: data,
  });
  const alertEvents = buildComputedAlertEvents({
    risk,
    signals,
    paper: paperPreview,
    systemTrust,
    edgeScoreboard: edgePolicy.scoreboard,
  });

  return {
    signals,
    risk,
    backtest,
    paperPreview,
    alertEvents,
    edgePolicy: edgePolicy.decision,
    edgeScoreboard: edgePolicy.scoreboard,
    systemTrust,
  };
}

function buildComputedAlertEvents(input: {
  risk: MarketRiskState;
  signals: RelativeValueSignal[];
  paper: PaperPortfolio;
  systemTrust: SystemTrustVerdict;
  edgeScoreboard: EdgeScoreboard;
}) {
  return [
    ...input.risk.activeAlerts.map(riskAlertToAlertEvent),
    ...systemTrustToAlertEvents(input.systemTrust),
    ...edgeScoreboardToAlertEvents(input.edgeScoreboard),
    ...input.signals
      .filter((signal) => signal.eligibleForPaperTrading)
      .slice(0, 4)
      .map(signalToTradeableAlert),
    dailyDigestAlert({
      risk: input.risk,
      paper: input.paper,
      signalCount: input.signals.length,
    }),
  ];
}

export function buildAlertRouterConfig(now = new Date()): AlertRouterConfig {
  return {
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
    now,
  };
}

export function mergeAlerts(
  existing: AlertEvent[],
  incoming: AlertEvent[],
): AlertEvent[] {
  const map = new Map<string, AlertEvent>();
  [...incoming, ...existing].forEach((alert) => map.set(alert.id, alert));
  return [...map.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function mergeAlertRouterState(
  state: AlertRouterState | undefined,
): AlertRouterState {
  return (
    state ?? {
      lastSentByFingerprint: {},
      acknowledgedAlertIds: [],
    }
  );
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
