import { afterEach, describe, expect, it } from "vitest";
import type { NextRequest } from "next/server";
import { POST } from "@/app/api/telegram/webhook/route";

const ORIGINAL = {
  TELEGRAM_WEBHOOK_SECRET: process.env.TELEGRAM_WEBHOOK_SECRET,
  TELEGRAM_AUTHORIZED_CHAT_IDS: process.env.TELEGRAM_AUTHORIZED_CHAT_IDS,
  TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN,
};

afterEach(() => {
  // Restore exactly — assigning `undefined` would coerce to the string
  // "undefined" and leak into other tests, so delete when originally unset.
  for (const [key, value] of Object.entries(ORIGINAL)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

function req(body: string | object, headers: Record<string, string> = {}): NextRequest {
  const init: RequestInit = {
    method: "POST",
    headers,
    body: typeof body === "string" ? body : JSON.stringify(body),
  };
  return new Request("http://localhost/api/telegram/webhook", init) as unknown as NextRequest;
}

// All cases below short-circuit before any state read, recompute, or mutation.
describe("telegram webhook security", () => {
  it("rejects with 401 when the configured secret token is missing or wrong", async () => {
    process.env.TELEGRAM_WEBHOOK_SECRET = "s3cret";
    process.env.TELEGRAM_AUTHORIZED_CHAT_IDS = "123";

    const missing = await POST(req({ message: { chat: { id: 123 }, text: "/status" } }));
    expect(missing.status).toBe(401);

    const wrong = await POST(
      req(
        { message: { chat: { id: 123 }, text: "/status" } },
        { "x-telegram-bot-api-secret-token": "nope" },
      ),
    );
    expect(wrong.status).toBe(401);
  });

  it("accepts the matching secret token but still blocks an unauthorized chat", async () => {
    process.env.TELEGRAM_WEBHOOK_SECRET = "s3cret";
    process.env.TELEGRAM_AUTHORIZED_CHAT_IDS = "123";

    const res = await POST(
      req(
        { message: { chat: { id: 999 }, text: "/kill" } },
        { "x-telegram-bot-api-secret-token": "s3cret" },
      ),
    );

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.authorized).toBe(false);
    expect(body.action).toBe("none");
  });

  it("returns 400 for malformed JSON instead of throwing a 500", async () => {
    process.env.TELEGRAM_WEBHOOK_SECRET = "";
    const res = await POST(req("definitely not json"));
    expect(res.status).toBe(400);
  });

  it("blocks an unauthorized chat even when no secret is configured", async () => {
    process.env.TELEGRAM_WEBHOOK_SECRET = "";
    process.env.TELEGRAM_AUTHORIZED_CHAT_IDS = "123";

    const res = await POST(req({ message: { chat: { id: 999 }, text: "/kill" } }));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.authorized).toBe(false);
  });
});
