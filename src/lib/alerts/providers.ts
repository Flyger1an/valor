import type { AlertDelivery, AlertEvent } from "@/lib/alerts/types";
import { formatAlertMessage } from "@/lib/alerts/redaction";

export interface AlertProvider {
  channel: "telegram" | "sms";
  send(alert: AlertEvent, destination: string): Promise<AlertDelivery>;
}

export class TelegramAlertProvider implements AlertProvider {
  channel = "telegram" as const;

  constructor(
    private readonly token?: string,
    private readonly dryRun = true,
    private readonly fetcher: typeof fetch = fetch,
  ) {}

  async send(alert: AlertEvent, destination: string): Promise<AlertDelivery> {
    const redactedMessage = formatAlertMessage(alert);
    const base = deliveryBase(alert, this.channel, destination, redactedMessage);

    if (!this.token || this.dryRun) {
      return { ...base, provider: "telegram-bot-api", status: "dry_run" };
    }

    const response = await this.fetcher(
      `https://api.telegram.org/bot${this.token}/sendMessage`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          chat_id: destination,
          text: redactedMessage,
          disable_web_page_preview: true,
        }),
      },
    );

    return {
      ...base,
      provider: "telegram-bot-api",
      status: response.ok ? "sent" : "failed",
      error: response.ok ? undefined : `Telegram HTTP ${response.status}`,
    };
  }
}

export class TwilioSmsAlertProvider implements AlertProvider {
  channel = "sms" as const;

  constructor(
    private readonly config: {
      accountSid?: string;
      authToken?: string;
      fromNumber?: string;
      dryRun?: boolean;
    },
    private readonly fetcher: typeof fetch = fetch,
  ) {}

  async send(alert: AlertEvent, destination: string): Promise<AlertDelivery> {
    const redactedMessage = formatAlertMessage(alert);
    const base = deliveryBase(alert, this.channel, destination, redactedMessage);
    const { accountSid, authToken, fromNumber, dryRun = true } = this.config;

    if (!accountSid || !authToken || !fromNumber || dryRun) {
      return { ...base, provider: "twilio-messaging", status: "dry_run" };
    }

    const body = new URLSearchParams({
      To: destination,
      From: fromNumber,
      Body: redactedMessage.slice(0, 1500),
    });
    const auth = Buffer.from(`${accountSid}:${authToken}`).toString("base64");
    const response = await this.fetcher(
      `https://api.twilio.com/2010-04-01/Accounts/${accountSid}/Messages.json`,
      {
        method: "POST",
        headers: {
          authorization: `Basic ${auth}`,
          "content-type": "application/x-www-form-urlencoded",
        },
        body,
      },
    );

    return {
      ...base,
      provider: "twilio-messaging",
      status: response.ok ? "sent" : "failed",
      error: response.ok ? undefined : `Twilio HTTP ${response.status}`,
    };
  }
}

function deliveryBase(
  alert: AlertEvent,
  channel: AlertDelivery["channel"],
  destination: string,
  redactedMessage: string,
): Omit<AlertDelivery, "provider" | "status"> {
  return {
    id: `provider:${alert.id}:${channel}:${destination}`,
    alertId: alert.id,
    channel,
    attemptedAt: new Date().toISOString(),
    destination: maskDestination(destination),
    redactedMessage,
  };
}

function maskDestination(destination: string): string {
  if (destination.length <= 4) return "****";
  return `${"*".repeat(Math.max(destination.length - 4, 0))}${destination.slice(-4)}`;
}
