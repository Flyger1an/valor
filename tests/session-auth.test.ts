import { describe, expect, it } from "vitest";
import {
  createSessionToken,
  hashAdminPassword,
  SESSION_COOKIE_NAME,
  sessionFromCookieHeader,
  verifyAdminPassword,
  verifySessionToken,
} from "@/lib/auth/session";

describe("browser session auth", () => {
  it("signs and verifies finite operator sessions", () => {
    const { token, session } = createSessionToken({
      secret: "session-secret",
      nowSeconds: 1_700_000_000,
      ttlSeconds: 60,
    });

    expect(verifySessionToken(token, "session-secret", 1_700_000_001)).toEqual(
      session,
    );
    expect(verifySessionToken(token, "wrong-secret", 1_700_000_001)).toBeNull();
    expect(verifySessionToken(token, "session-secret", 1_700_000_061)).toBeNull();
  });

  it("rejects tampered session payloads", () => {
    const { token } = createSessionToken({
      secret: "session-secret",
      nowSeconds: 1_700_000_000,
      ttlSeconds: 60,
    });
    const [payload, signature] = token.split(".");
    const tampered = `${payload?.replace(/.$/, "A")}.${signature}`;

    expect(verifySessionToken(tampered, "session-secret", 1_700_000_001)).toBeNull();
  });

  it("extracts session cookies from request headers", () => {
    const { token, session } = createSessionToken({
      secret: "session-secret",
      ttlSeconds: 60,
    });

    expect(
      sessionFromCookieHeader(`other=1; ${SESSION_COOKIE_NAME}=${token}`, {
        VALOR_SESSION_SECRET: "session-secret",
      } as NodeJS.ProcessEnv),
    ).toEqual(session);
  });

  it("verifies scrypt admin password hashes", async () => {
    const hash = await hashAdminPassword("correct horse", "fixed-salt");

    await expect(verifyAdminPassword("correct horse", hash)).resolves.toBe(true);
    await expect(verifyAdminPassword("wrong horse", hash)).resolves.toBe(false);
    await expect(verifyAdminPassword("correct horse", "not-a-valid-hash")).resolves.toBe(
      false,
    );
  });
});
