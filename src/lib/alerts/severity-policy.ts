import type { AlertChannel, AlertSeverity } from "@/lib/alerts/types";

export interface SeverityPolicy {
  channels: AlertChannel[];
  cooldownMinutes: number;
  bypassQuietHours: boolean;
  requiresAcknowledgement: boolean;
  repeatUntilAcknowledged: boolean;
}

export const SEVERITY_POLICIES: Record<AlertSeverity, SeverityPolicy> = {
  INFO: {
    channels: ["telegram"],
    cooldownMinutes: 60,
    bypassQuietHours: false,
    requiresAcknowledgement: false,
    repeatUntilAcknowledged: false,
  },
  WATCH: {
    channels: ["telegram"],
    cooldownMinutes: 45,
    bypassQuietHours: false,
    requiresAcknowledgement: false,
    repeatUntilAcknowledged: false,
  },
  TRADEABLE: {
    channels: ["telegram"],
    cooldownMinutes: 15,
    bypassQuietHours: false,
    requiresAcknowledgement: false,
    repeatUntilAcknowledged: false,
  },
  CRITICAL: {
    channels: ["telegram", "sms"],
    cooldownMinutes: 5,
    bypassQuietHours: true,
    requiresAcknowledgement: true,
    repeatUntilAcknowledged: true,
  },
  BLACK: {
    channels: ["telegram", "sms"],
    cooldownMinutes: 1,
    bypassQuietHours: true,
    requiresAcknowledgement: true,
    repeatUntilAcknowledged: true,
  },
};

export function severityFromRiskState(state: string): AlertSeverity {
  if (state === "Black") return "BLACK";
  if (state === "Red") return "CRITICAL";
  if (state === "Yellow") return "WATCH";
  return "INFO";
}
