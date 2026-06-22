import {
  dailyDigestAlert,
  riskAlertToAlertEvent,
  signalToTradeableAlert,
} from "@/lib/alerts/from-domain";
import { runBasisCarryBacktest } from "@/lib/backtest/backtester";
import { getDefaultConnector } from "@/lib/data/connectors";
import type {
  AlertEvent,
  AlertRouterConfig,
  AlertRouterState,
} from "@/lib/alerts/types";
import type { MarketDataBundle } from "@/lib/domain/types";
import { simulatePaperPortfolio } from "@/lib/paper/paper-broker";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";
import { LocalStateStore } from "@/lib/state/local-store";

export async function refreshAndPersistMarketState() {
  const store = new LocalStateStore();
  const connector = getDefaultConnector();
  const data = await connector.fetchLatest();
  const computed = computeFromData(data);

  const next = store.update((state) => ({
    ...state,
    lastRefreshAt: data.generatedAt,
    data,
    signals: computed.signals,
    risk: computed.risk,
    backtest: computed.backtest,
    alertEvents: computed.alertEvents,
  }));

  store.appendAction({
    action: "data.refresh",
    status: "ok",
    message: `Refreshed ${data.markets.length} markets via ${connector.label}.`,
    timestamp: data.generatedAt,
  });

  return { connector, data, computed, state: next };
}

export function computeFromData(data: MarketDataBundle) {
  const signals = generateRelativeValueSignals(data);
  const risk = evaluateMarketRisk(data);
  const backtest = runBasisCarryBacktest(data.backtestHistory);
  const paperPreview = simulatePaperPortfolio({ signals, risk });
  const alertEvents = [
    ...risk.activeAlerts.map(riskAlertToAlertEvent),
    ...signals
      .filter((signal) => signal.eligibleForPaperTrading)
      .slice(0, 4)
      .map(signalToTradeableAlert),
    dailyDigestAlert({ risk, paper: paperPreview, signalCount: signals.length }),
  ];

  return { signals, risk, backtest, paperPreview, alertEvents };
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
