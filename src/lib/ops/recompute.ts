import {
  dailyDigestAlert,
  riskAlertToAlertEvent,
  riskTransitionAlert,
  signalToTradeableAlert,
} from "@/lib/alerts/from-domain";
import { runBasisCarryBacktest } from "@/lib/backtest/backtester";
import { buildDataProvenance } from "@/lib/data/provenance";
import { getDefaultConnector } from "@/lib/data/connectors";
import type {
  AlertEvent,
  AlertRouterConfig,
  AlertRouterState,
} from "@/lib/alerts/types";
import type { MarketDataBundle } from "@/lib/domain/types";
import { advancePaperBook } from "@/lib/paper/paper-book";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { buildSignalJournal } from "@/lib/signals/signal-journal";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";
import { LocalStateStore } from "@/lib/state/local-store";

export async function refreshAndPersistMarketState() {
  const store = new LocalStateStore();
  const previous = store.read();
  const connector = getDefaultConnector();
  const data = await connector.fetchLatest();
  const computed = computeFromData(data, previous);
  const provenance = buildDataProvenance(data, connector);

  const next = store.update((state) => ({
    ...state,
    lastRefreshAt: data.generatedAt,
    data,
    signals: computed.signals,
    risk: computed.risk,
    backtest: computed.backtest,
    paper: computed.paper,
    equityHistory: computed.equityHistory,
    signalJournal: computed.signalJournal.entries,
    dataProvenance: provenance,
    alertEvents: mergeAlerts(state.alertEvents, computed.alertEvents),
  }));

  store.appendAction({
    action: "data.refresh",
    status: "ok",
    message: `Refreshed ${data.markets.length} markets via ${connector.label}; paper ${computed.paperBookSummary.opened} opened / ${computed.paperBookSummary.closed} closed; journal ${computed.signalJournal.persistedSignals} persistent signals.`,
    timestamp: data.generatedAt,
  });

  return { connector, data, computed, state: next, provenance };
}

export function computeFromData(
  data: MarketDataBundle,
  previous?: ReturnType<LocalStateStore["read"]>,
) {
  const signals = generateRelativeValueSignals(data);
  const risk = evaluateMarketRisk(data);
  const backtest = runBasisCarryBacktest(data.backtestHistory);
  const paperBook = advancePaperBook({
    previous: previous?.paper,
    signals,
    risk,
    timestamp: data.generatedAt,
    equityHistory: previous?.equityHistory,
  });
  const signalJournal = buildSignalJournal({
    signals,
    previous: previous?.signalJournal,
    timestamp: data.generatedAt,
  });
  const transition = previous?.risk
    ? riskTransitionAlert({ previousState: previous.risk.state, next: risk })
    : null;
  const alertEvents = [
    ...(transition ? [transition] : []),
    ...risk.activeAlerts.map(riskAlertToAlertEvent),
    ...signals
      .filter((signal) => signal.eligibleForPaperTrading)
      .slice(0, 4)
      .map(signalToTradeableAlert),
    dailyDigestAlert({
      risk,
      paper: paperBook.portfolio,
      signalCount: signals.length,
    }),
  ];

  return {
    signals,
    risk,
    backtest,
    paper: paperBook.portfolio,
    equityHistory: paperBook.equityHistory,
    signalJournal,
    paperBookSummary: {
      opened: paperBook.opened,
      closed: paperBook.closed,
      marked: paperBook.marked,
    },
    alertEvents,
  };
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