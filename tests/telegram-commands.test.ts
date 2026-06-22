import { describe, expect, it } from "vitest";
import { handleTelegramCommand } from "@/lib/telegram/commands";
import type { AlertRouterState, MarketStatusSummary } from "@/lib/alerts/types";

const alertState: AlertRouterState = {
  lastSentByFingerprint: {},
  acknowledgedAlertIds: [],
};

const summary: MarketStatusSummary = {
  riskState: "Red",
  riskExplanation: "Venue stress requires review.",
  topSignals: [
    {
      id: "s1",
      assetPair: "BTC/USD",
      opportunityScore: 80,
      expectedEdgeBps: 120,
    },
  ],
  paperExposureUsd: 12_000,
  activeAlerts: [
    {
      id: "alert-1",
      severity: "CRITICAL",
      title: "Withdrawal issue",
      message: "Withdrawals delayed.",
      source: "test",
      scope: {},
      createdAt: "2026-06-22T12:00:00.000Z",
      fingerprint: "withdrawal",
      tradingImpact: "Block venue.",
      metadata: {},
    },
  ],
};

describe("telegram commands", () => {
  it("rejects unauthorized chat ids", () => {
    const result = handleTelegramCommand({
      update: { message: { text: "/status", chat: { id: "999" } } },
      authorizedChatIds: ["123"],
      summary,
      alertState,
    });

    expect(result.authorized).toBe(false);
  });

  it("returns risk and signal summaries for authorized chat", () => {
    const risk = handleTelegramCommand({
      update: { message: { text: "/risk", chat: { id: "123" } } },
      authorizedChatIds: ["123"],
      summary,
      alertState,
    });
    const signals = handleTelegramCommand({
      update: { message: { text: "/signals", chat: { id: "123" } } },
      authorizedChatIds: ["123"],
      summary,
      alertState,
    });

    expect(risk.text).toContain("Risk Red");
    expect(signals.text).toContain("BTC/USD");
  });

  it("acknowledges alerts and emits kill action", () => {
    const ack = handleTelegramCommand({
      update: { message: { text: "/ack alert-1", chat: { id: "123" } } },
      authorizedChatIds: ["123"],
      summary,
      alertState,
    });
    const kill = handleTelegramCommand({
      update: { message: { text: "/kill", chat: { id: "123" } } },
      authorizedChatIds: ["123"],
      summary,
      alertState,
    });

    expect(ack.nextAlertState?.acknowledgedAlertIds).toContain("alert-1");
    expect(kill.action).toBe("kill");
  });
});
