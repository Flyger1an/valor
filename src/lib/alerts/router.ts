import { formatAlertMessage } from "@/lib/alerts/redaction";
import { SEVERITY_POLICIES } from "@/lib/alerts/severity-policy";
import type {
  AlertDelivery,
  AlertEvent,
  AlertRouterConfig,
  AlertRouterState,
  RoutedAlertResult,
} from "@/lib/alerts/types";

export function routeAlert(
  alert: AlertEvent,
  config: AlertRouterConfig,
  state: AlertRouterState = {
    lastSentByFingerprint: {},
    acknowledgedAlertIds: [],
  },
): RoutedAlertResult {
  const now = config.now ?? new Date();
  const policy = SEVERITY_POLICIES[alert.severity];
  const reasons: string[] = [];
  const nextState: AlertRouterState = {
    lastSentByFingerprint: { ...state.lastSentByFingerprint },
    acknowledgedAlertIds: [...state.acknowledgedAlertIds],
    pausedUntil: state.pausedUntil,
  };

  if (nextState.acknowledgedAlertIds.includes(alert.id)) {
    reasons.push("Alert is already acknowledged.");
    return suppressed(alert, nextState, reasons);
  }

  const lastSent = nextState.lastSentByFingerprint[alert.fingerprint];
  if (lastSent && !cooldownExpired(lastSent, now, policy.cooldownMinutes)) {
    reasons.push(`Cooldown active for fingerprint ${alert.fingerprint}.`);
    return suppressed(alert, nextState, reasons);
  }

  if (!policy.bypassQuietHours && isQuietHours(now, config.quietHours)) {
    reasons.push("Quiet hours suppress non-critical alert.");
    return suppressed(alert, nextState, reasons);
  }

  const redactedMessage = formatAlertMessage(alert);
  const deliveries: AlertDelivery[] = policy.channels.flatMap((channel) => {
    const destinations =
      channel === "telegram" ? config.telegramChatIds : config.smsNumbers;
    return destinations.map((destination) => ({
      id: `delivery:${alert.id}:${channel}:${destination}`,
      alertId: alert.id,
      channel,
      provider: channel === "telegram" ? "telegram-bot-api" : "twilio-messaging",
      status: "queued",
      attemptedAt: now.toISOString(),
      destination: maskDestination(destination),
      redactedMessage,
    }));
  });

  nextState.lastSentByFingerprint[alert.fingerprint] = now.toISOString();

  return {
    alert,
    deliveries,
    suppressed: false,
    reasons,
    nextState,
  };
}

export function acknowledgeAlert(
  alertId: string,
  state: AlertRouterState,
): AlertRouterState {
  return {
    ...state,
    acknowledgedAlertIds: [...new Set([...state.acknowledgedAlertIds, alertId])],
  };
}

export function pauseAlerts(
  durationMs: number,
  now = new Date(),
  state: AlertRouterState = { lastSentByFingerprint: {}, acknowledgedAlertIds: [] },
): AlertRouterState {
  return {
    ...state,
    pausedUntil: new Date(now.getTime() + durationMs).toISOString(),
  };
}

function suppressed(
  alert: AlertEvent,
  nextState: AlertRouterState,
  reasons: string[],
): RoutedAlertResult {
  return {
    alert,
    deliveries: [],
    suppressed: true,
    reasons,
    nextState,
  };
}

function cooldownExpired(
  lastSentIso: string,
  now: Date,
  cooldownMinutes: number,
): boolean {
  const elapsedMs = now.getTime() - new Date(lastSentIso).getTime();
  return elapsedMs >= cooldownMinutes * 60_000;
}

function isQuietHours(
  now: Date,
  quietHours: AlertRouterConfig["quietHours"],
): boolean {
  if (!quietHours.enabled) return false;
  const hour = now.getHours();
  if (quietHours.startHourLocal === quietHours.endHourLocal) return false;
  if (quietHours.startHourLocal < quietHours.endHourLocal) {
    return hour >= quietHours.startHourLocal && hour < quietHours.endHourLocal;
  }
  return hour >= quietHours.startHourLocal || hour < quietHours.endHourLocal;
}

function maskDestination(destination: string): string {
  if (destination.length <= 4) return "****";
  return `${"*".repeat(Math.max(destination.length - 4, 0))}${destination.slice(-4)}`;
}
