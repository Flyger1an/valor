import { describe, expect, it } from "vitest";
import {
  createSessionToken,
  SESSION_COOKIE_NAME,
} from "@/lib/auth/session";
import { requireOpsAuth } from "@/lib/ops/auth";
import { clearOpsRateLimitBuckets } from "@/lib/ops/rate-limit";

function request(headers: Record<string, string> = {}) {
  return new Request("http://localhost/api/ops/refresh", {
    method: "POST",
    headers,
  });
}

describe("ops API authorization", () => {
  it("allows local development when no ops secret is configured", () => {
    const result = requireOpsAuth(request(), {}, {
      NODE_ENV: "development",
    } as NodeJS.ProcessEnv);

    expect(result).toBeNull();
  });

  it("fails closed in production when the ops secret is missing", () => {
    const result = requireOpsAuth(request(), {}, {
      NODE_ENV: "production",
    } as NodeJS.ProcessEnv);

    expect(result?.status).toBe(503);
  });

  it("allows production read APIs only when explicitly made public", () => {
    const result = requireOpsAuth(
      request(),
      { access: "read" },
      {
        NODE_ENV: "production",
        VALOR_PUBLIC_READ_APIS: "true",
      } as NodeJS.ProcessEnv,
    );

    expect(result).toBeNull();
  });

  it("rejects missing or wrong secrets when configured", () => {
    const env = {
      NODE_ENV: "production",
      VALOR_OPS_SECRET: "chef-secret",
    } as NodeJS.ProcessEnv;

    expect(requireOpsAuth(request(), {}, env)?.status).toBe(401);
    expect(
      requireOpsAuth(request({ "x-valor-ops-secret": "wrong" }), {}, env)?.status,
    ).toBe(401);
  });

  it("accepts the configured header or bearer token", () => {
    const env = {
      NODE_ENV: "production",
      VALOR_OPS_SECRET: "chef-secret",
    } as NodeJS.ProcessEnv;

    expect(
      requireOpsAuth(request({ "x-valor-ops-secret": "chef-secret" }), {}, env),
    ).toBeNull();
    expect(
      requireOpsAuth(request({ authorization: "Bearer chef-secret" }), {}, env),
    ).toBeNull();
  });

  it("accepts a valid browser session cookie without the ops secret header", () => {
    const { token } = createSessionToken({
      secret: "session-secret",
      ttlSeconds: 60,
    });
    const env = {
      NODE_ENV: "production",
      VALOR_SESSION_SECRET: "session-secret",
    } as NodeJS.ProcessEnv;

    expect(
      requireOpsAuth(
        request({ cookie: `${SESSION_COOKIE_NAME}=${token}` }),
        { access: "read" },
        env,
      ),
    ).toBeNull();
  });

  it("blocks cross-origin browser mutations that rely on the session cookie", () => {
    const { token } = createSessionToken({
      secret: "session-secret",
      ttlSeconds: 60,
    });
    const env = {
      NODE_ENV: "production",
      VALOR_SESSION_SECRET: "session-secret",
    } as NodeJS.ProcessEnv;

    const result = requireOpsAuth(
      request({
        cookie: `${SESSION_COOKIE_NAME}=${token}`,
        origin: "https://evil.example",
        "x-forwarded-host": "valor.example",
      }),
      { access: "write" },
      env,
    );

    expect(result?.status).toBe(403);
  });

  it("rate limits authenticated ops requests by scope and client", () => {
    clearOpsRateLimitBuckets();
    const env = {
      NODE_ENV: "production",
      VALOR_OPS_SECRET: "chef-secret",
    } as NodeJS.ProcessEnv;
    const headers = {
      "x-valor-ops-secret": "chef-secret",
      "x-forwarded-for": "203.0.113.10",
    };
    const options = {
      access: "write" as const,
      rateLimit: {
        scope: "test.ops",
        limit: 2,
        windowMs: 60_000,
        nowMs: 1_000,
      },
    };

    expect(requireOpsAuth(request(headers), options, env)).toBeNull();
    expect(requireOpsAuth(request(headers), options, env)).toBeNull();
    expect(requireOpsAuth(request(headers), options, env)?.status).toBe(429);

    clearOpsRateLimitBuckets();
  });
});
