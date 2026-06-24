import { NextRequest, NextResponse } from "next/server";
import { TelegramAlertProvider } from "@/lib/alerts/providers";
import type { MarketStatusSummary } from "@/lib/alerts/types";
import { riskAlertToAlertEvent } from "@/lib/alerts/from-domain";
import { FileKillSwitchStore } from "@/lib/kill-switch/kill-switch";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { LocalStateStore } from "@/lib/state/local-store";
import {
  handleTelegramCommand,
  type TelegramUpdate,
} from "@/lib/telegram/commands";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  // 1) Verify Telegram's secret token before doing ANY work. This endpoint can
  // trip the kill switch, so when a secret is configured we fail closed — chat
  // ids are not secret and cannot be the only line of defense.
  const expectedSecret = process.env.TELEGRAM_WEBHOOK_SECRET;
  if (expectedSecret) {
    const presented = request.headers.get("x-telegram-bot-api-secret-token");
    if (presented !== expectedSecret) {
      return NextResponse.json({ ok: false, error: "Unauthorized." }, { status: 401 });
    }
  }

  // 2) Parse and shape-validate the body before touching any state.
  const update = await parseUpdate(request);
  if (!update) {
    return NextResponse.json(
      { ok: false, error: "Invalid update payload." },
      { status: 400 },
    );
  }

  // 3) Authorize the chat up front. Unauthorized callers get a flat response
  // with no recompute and no state mutation (prevents resource-amplification).
  const chatId = String(update.message?.chat.id ?? "");
  const authorizedChatIds = listFromEnv(process.env.TELEGRAM_AUTHORIZED_CHAT_IDS);
  if (!chatId || !authorizedChatIds.includes(chatId)) {
    return NextResponse.json(
      { authorized: false, text: "Unauthorized chat.", action: "none" },
      { status: 200 },
    );
  }

  // 4) Authorized path: load state, refreshing only if we have nothing to report.
  const store = new LocalStateStore();
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
    authorizedChatIds,
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
        id: `telegram-response:${chatId}:${new Date().toISOString()}`,
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

/**
 * Parse the request body and reject anything that is not a well-formed Telegram
 * update. Returns null for malformed JSON or a structurally invalid message so
 * the caller can respond 400 instead of throwing an unhandled 500.
 */
async function parseUpdate(request: NextRequest): Promise<TelegramUpdate | null> {
  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object") return null;

  const message = (raw as { message?: unknown }).message;
  if (message !== undefined) {
    if (typeof message !== "object" || message === null) return null;
    const chat = (message as { chat?: unknown }).chat;
    if (!chat || typeof chat !== "object") return null;
    const id = (chat as { id?: unknown }).id;
    if (typeof id !== "number" && typeof id !== "string") return null;
  }

  return raw as TelegramUpdate;
}

function listFromEnv(value: string | undefined): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
