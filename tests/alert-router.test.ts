import { describe, expect, it } from "vitest";
import { routeAlert } from "@/lib/alerts/router";
import type { AlertEvent } from "@/lib/alerts/types";

const baseAlert: AlertEvent = {
  id: "alert-1",
  severity: "TRADEABLE",
  title: "BTC basis candidate",
  message: "Expected edge 80 bps with risk score 40 and liquidity score 90.",
  source: "test",
  scope: { pair: "BTC/USD" },
  createdAt: "2026-06-22T12:00:00.000Z",
  fingerprint: "signal:btc",
  tradingImpact: "Paper trading allowed; live trading requires guards.",
  metadata: {},
};

const config = {
  telegramChatIds: ["12345"],
  smsNumbers: ["+15555550000"],
  quietHours: {
    enabled: false,
    startHourLocal: 22,
    endHourLocal: 7,
  },
  escalationMinutes: 15,
  now: new Date("2026-06-22T12:00:00.000Z"),
};

describe("alert router", () => {
  it("routes TRADEABLE alerts to Telegram only", () => {
    const result = routeAlert(baseAlert, config);

    expect(result.suppressed).toBe(false);
    expect(result.deliveries.map((delivery) => delivery.channel)).toEqual([
      "telegram",
    ]);
  });

  it("routes CRITICAL and BLACK alerts to Telegram and SMS", () => {
    const critical = routeAlert({ ...baseAlert, id: "critical", severity: "CRITICAL" }, config);
    const black = routeAlert({ ...baseAlert, id: "black", severity: "BLACK" }, config);

    expect(critical.deliveries.map((delivery) => delivery.channel).sort()).toEqual([
      "sms",
      "telegram",
    ]);
    expect(black.deliveries.map((delivery) => delivery.channel).sort()).toEqual([
      "sms",
      "telegram",
    ]);
  });

  it("deduplicates alerts during cooldown", () => {
    const first = routeAlert(baseAlert, config);
    const second = routeAlert(
      { ...baseAlert, id: "alert-2" },
      {
        ...config,
        now: new Date("2026-06-22T12:10:00.000Z"),
      },
      first.nextState,
    );

    expect(second.suppressed).toBe(true);
    expect(second.reasons[0]).toContain("Cooldown active");
  });

  it("quiet hours suppress WATCH but not CRITICAL", () => {
    const quietConfig = {
      ...config,
      quietHours: { enabled: true, startHourLocal: 22, endHourLocal: 7 },
      now: new Date(2026, 5, 22, 23, 0, 0),
    };

    const watch = routeAlert({ ...baseAlert, severity: "WATCH" }, quietConfig);
    const critical = routeAlert(
      { ...baseAlert, id: "critical-2", severity: "CRITICAL", fingerprint: "critical-2" },
      quietConfig,
    );

    expect(watch.suppressed).toBe(true);
    expect(critical.suppressed).toBe(false);
  });
});
