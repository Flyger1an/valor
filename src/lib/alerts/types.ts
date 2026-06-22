import type { RiskState } from "@/lib/domain/types";

export type AlertSeverity = "INFO" | "WATCH" | "TRADEABLE" | "CRITICAL" | "BLACK";
export type AlertChannel = "telegram" | "sms";
export type AlertDeliveryStatus = "queued" | "sent" | "suppressed" | "failed" | "dry_run";

export interface AlertEvent {
  id: string;
  severity: AlertSeverity;
  title: string;
  message: string;
  source: string;
  scope: {
    venue?: string;
    asset?: string;
    pair?: string;
  };
  createdAt: string;
  fingerprint: string;
  tradingImpact: string;
  metadata: Record<string, string | number | boolean | undefined>;
  acknowledgedAt?: string;
}

export interface AlertDelivery {
  id: string;
  alertId: string;
  channel: AlertChannel;
  provider: string;
  status: AlertDeliveryStatus;
  attemptedAt: string;
  destination: string;
  redactedMessage: string;
  error?: string;
}

export interface AlertRouterState {
  lastSentByFingerprint: Record<string, string>;
  acknowledgedAlertIds: string[];
  pausedUntil?: string;
}

export interface QuietHours {
  enabled: boolean;
  startHourLocal: number;
  endHourLocal: number;
}

export interface AlertRouterConfig {
  telegramChatIds: string[];
  smsNumbers: string[];
  quietHours: QuietHours;
  escalationMinutes: number;
  now?: Date;
}

export interface RoutedAlertResult {
  alert: AlertEvent;
  deliveries: AlertDelivery[];
  suppressed: boolean;
  reasons: string[];
  nextState: AlertRouterState;
}

export interface MarketStatusSummary {
  riskState: RiskState;
  riskExplanation: string;
  topSignals: Array<{
    id: string;
    assetPair: string;
    opportunityScore: number;
    expectedEdgeBps: number;
  }>;
  paperExposureUsd: number;
  activeAlerts: AlertEvent[];
}
