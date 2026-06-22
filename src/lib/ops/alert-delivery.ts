import {
  TelegramAlertProvider,
  TwilioSmsAlertProvider,
} from "@/lib/alerts/providers";
import { routeAlert } from "@/lib/alerts/router";
import type { AlertDelivery, AlertEvent } from "@/lib/alerts/types";
import {
  buildAlertRouterConfig,
  mergeAlertRouterState,
} from "@/lib/ops/recompute";
import { LocalStateStore } from "@/lib/state/local-store";

export async function sendAlertNow(alert: AlertEvent) {
  const store = new LocalStateStore();
  const state = store.read();
  const config = buildAlertRouterConfig(new Date());
  const routed = routeAlert(
    alert,
    config,
    mergeAlertRouterState(state.alertRouterState),
  );

  const deliveries: AlertDelivery[] = [];
  if (!routed.suppressed) {
    const telegram = new TelegramAlertProvider(
      process.env.TELEGRAM_BOT_TOKEN,
      process.env.TELEGRAM_DRY_RUN !== "false",
    );
    const sms = new TwilioSmsAlertProvider({
      accountSid: process.env.TWILIO_ACCOUNT_SID,
      authToken: process.env.TWILIO_AUTH_TOKEN,
      fromNumber: process.env.TWILIO_FROM_NUMBER,
      dryRun: process.env.TWILIO_DRY_RUN !== "false",
    });

    if (routed.deliveries.some((delivery) => delivery.channel === "telegram")) {
      for (const chatId of config.telegramChatIds) {
        deliveries.push(await telegram.send(alert, chatId));
      }
    }

    if (routed.deliveries.some((delivery) => delivery.channel === "sms")) {
      for (const phoneNumber of config.smsNumbers) {
        deliveries.push(await sms.send(alert, phoneNumber));
      }
    }
  }

  store.update((current) => ({
    ...current,
    alertEvents: [alert, ...current.alertEvents.filter((item) => item.id !== alert.id)],
    alertDeliveries: [...deliveries, ...current.alertDeliveries].slice(0, 200),
    alertRouterState: routed.nextState,
  }));

  store.appendAction({
    action: "alert.send",
    status: deliveries.every((delivery) => delivery.status === "dry_run")
      ? "dry_run"
      : "ok",
    message: routed.suppressed
      ? `Suppressed ${alert.id}: ${routed.reasons.join(", ")}`
      : `Processed ${alert.id} with ${deliveries.length} delivery attempt(s).`,
  });

  return { routed, deliveries };
}
