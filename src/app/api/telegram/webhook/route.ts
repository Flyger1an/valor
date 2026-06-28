import { NextRequest, NextResponse } from "next/server";
import { TelegramAlertProvider } from "@/lib/alerts/providers";
import type { MarketStatusSummary } from "@/lib/alerts/types";
import { riskAlertToAlertEvent } from "@/lib/alerts/from-domain";
import { FileKillSwitchStore } from "@/lib/kill-switch/kill-switch";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { getStateStore } from "@/lib/state/store-factory";
import {
  handleTelegramCommand,
  type TelegramUpdate,
} from "@/lib/telegram/commands";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const update = (await request.json()) as TelegramUpdate;
  const chatId = String(update.message?.chat.id ?? "");
  const store = getStateStore();
  let state = store.read();
  if (!state.risk || !state.signals) {
    await refreshAndPersistMarketState();
    state = store.read();
  }

  const summary: MarketStatusSummary = {
    riskState: state.risk!.state,
    riskExplanation: state.risk!.explanation,
    topSignals: state.signals!.slice(0, 5).map((signal) => ({
      id: signal.id,
      assetPair: signal.assetPair,
      opportunityScore: signal.opportunityScore,
      expectedEdgeBps: signal.expectedEdgeBps,
    })),
    paperExposureUsd:
      state.paper?.positions.reduce((sum, position) => sum + position.notionalUsd, 0) ??
      0,
    activeAlerts:
      state.alertEvents.length > 0
        ? state.alertEvents
        : state.risk!.activeAlerts.map(riskAlertToAlertEvent),
  };

  const result = handleTelegramCommand({
    update,
    authorizedChatIds: listFromEnv(process.env.TELEGRAM_AUTHORIZED_CHAT_IDS),
    summary,
    alertState: state.alertRouterState,
  });

  if (result.nextAlertState) {
    store.update((current) => ({
      ...current,
      alertRouterState: result.nextAlertState!,
    }));
  }

  if (result.action === "kill") {
    const killState = new FileKillSwitchStore().activate({
      actor: `telegram:${chatId}`,
      reason: "Telegram /kill command",
    });
    store.update((current) => ({ ...current, killSwitch: killState }));
  }

  if (process.env.TELEGRAM_BOT_TOKEN && chatId) {
    const provider = new TelegramAlertProvider(
      process.env.TELEGRAM_BOT_TOKEN,
      process.env.TELEGRAM_DRY_RUN !== "false",
    );
    await provider.send(
      {
        id: `telegram-response:${Date.now()}`,
        severity: result.authorized ? "INFO" : "WATCH",
        title: "Valor command response",
        message: result.text,
        source: "telegram-webhook",
        scope: {},
        createdAt: new Date().toISOString(),
        fingerprint: `telegram:${chatId}`,
        tradingImpact: "None.",
        metadata: {},
      },
      chatId,
    );
  }

  store.appendAction({
    action: "telegram.command",
    status: result.authorized ? "ok" : "error",
    message: result.text,
  });

  return NextResponse.json(result);
}

function listFromEnv(value: string | undefined): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
