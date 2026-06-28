import { acknowledgeAlert, pauseAlerts } from "@/lib/alerts/router";
import type {
  AlertRouterState,
  MarketStatusSummary,
} from "@/lib/alerts/types";

export interface TelegramUpdate {
  message?: {
    text?: string;
    chat: {
      id: number | string;
    };
    from?: {
      id?: number;
      username?: string;
    };
  };
}

export interface TelegramCommandResult {
  authorized: boolean;
  text: string;
  nextAlertState?: AlertRouterState;
  action?: "ack" | "pause" | "kill" | "resume_request" | "none";
}

export function handleTelegramCommand(input: {
  update: TelegramUpdate;
  authorizedChatIds: string[];
  summary: MarketStatusSummary;
  alertState: AlertRouterState;
  now?: Date;
}): TelegramCommandResult {
  const chatId = String(input.update.message?.chat.id ?? "");
  if (!input.authorizedChatIds.includes(chatId)) {
    return {
      authorized: false,
      text: "Unauthorized chat.",
      action: "none",
    };
  }

  const text = input.update.message?.text?.trim() ?? "";
  const [command, arg] = text.split(/\s+/, 2);

  switch (command) {
    case "/status":
      return ok(
        `Risk ${input.summary.riskState}. ${input.summary.topSignals.length} top signals tracked. Paper exposure is redacted; open positions notional ${Math.round(
          input.summary.paperExposureUsd,
        )} USD.`,
      );
    case "/risk":
      return ok(`Risk ${input.summary.riskState}: ${input.summary.riskExplanation}`);
    case "/signals":
      return ok(
        input.summary.topSignals
          .map(
            (signal, index) =>
              `${index + 1}. ${signal.assetPair} opp ${signal.opportunityScore.toFixed(
                1,
              )}, edge ${signal.expectedEdgeBps.toFixed(1)} bps`,
          )
          .join("\n") || "No active signals.",
      );
    case "/positions":
      return ok(
        `Exposure summary: ${Math.round(
          input.summary.paperExposureUsd,
        )} USD simulated notional. Full balances and account identifiers are never sent over chat.`,
      );
    case "/alerts":
      return ok(
        input.summary.activeAlerts
          .filter((alert) => !input.alertState.acknowledgedAlertIds.includes(alert.id))
          .map((alert) => `${alert.id} ${alert.severity} ${alert.title}`)
          .join("\n") || "No unacknowledged alerts.",
      );
    case "/ack": {
      if (!arg) return ok("Usage: /ack ALERT_ID");
      return {
        authorized: true,
        text: `Acknowledged ${arg}.`,
        action: "ack",
        nextAlertState: acknowledgeAlert(arg, input.alertState),
      };
    }
    case "/pause": {
      const duration = parseDuration(arg);
      if (!duration) return ok("Usage: /pause 1h|6h|24h");
      return {
        authorized: true,
        text: `Paused new paper/live actions for ${arg}.`,
        action: "pause",
        nextAlertState: pauseAlerts(duration, input.now, input.alertState),
      };
    }
    case "/kill":
      return {
        authorized: true,
        text: "Kill switch activation requested. BLACK state must persist and live trading remains halted.",
        action: "kill",
      };
    case "/resume":
      return {
        authorized: true,
        text: "Resume requested. Dashboard confirmation is required before any live trading can exist.",
        action: "resume_request",
      };
    default:
      return ok(
        "Commands: /status /risk /signals /positions /alerts /ack ALERT_ID /pause 1h|6h|24h /kill /resume",
      );
  }
}

function ok(text: string): TelegramCommandResult {
  return {
    authorized: true,
    text,
    action: "none",
  };
}

function parseDuration(value?: string): number | null {
  if (!value) return null;
  if (value === "1h") return 60 * 60_000;
  if (value === "6h") return 6 * 60 * 60_000;
  if (value === "24h") return 24 * 60 * 60_000;
  return null;
}
