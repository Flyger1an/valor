import { timingSafeEqual } from "node:crypto";
import { NextResponse } from "next/server";
import {
  browserAuthRequired,
  sessionFromCookieHeader,
} from "@/lib/auth/session";
import { OPS_SECRET_HEADER } from "@/lib/ops/auth-headers";
import {
  checkOpsRateLimit,
  type OpsRateLimitOptions,
} from "@/lib/ops/rate-limit";

export interface OpsAuthOptions {
  access?: "read" | "write";
  rateLimit?: OpsRateLimitOptions;
}

export function requireOpsAuth(
  request: Request,
  options: OpsAuthOptions = {},
  env: NodeJS.ProcessEnv = process.env,
): NextResponse | null {
  const access = options.access ?? "write";
  const expectedSecret = env.VALOR_OPS_SECRET?.trim();
  const presentedSecret =
    request.headers.get(OPS_SECRET_HEADER) ??
    bearerTokenFromAuthorization(request.headers.get("authorization"));

  if (access === "read" && env.VALOR_PUBLIC_READ_APIS === "true") {
    return applyRateLimit(request, options);
  }

  if (presentedSecret) {
    if (!expectedSecret) {
      return opsAuthError(
        "Ops authorization header was presented, but VALOR_OPS_SECRET is not configured.",
        503,
      );
    }

    if (!constantTimeEqual(presentedSecret, expectedSecret)) {
      return opsAuthError("Unauthorized ops request.", 401);
    }

    return applyRateLimit(request, options);
  }

  const browserSession = sessionFromCookieHeader(request.headers.get("cookie"), env);
  if (browserSession) {
    if (access === "write") {
      const csrfBlocked = blockCrossOriginSessionWrite(request, env);
      if (csrfBlocked) return csrfBlocked;
    }
    return applyRateLimit(request, options);
  }

  if (!opsAuthRequired(env)) {
    return applyRateLimit(request, options);
  }

  if (!expectedSecret && !env.VALOR_SESSION_SECRET?.trim()) {
    return opsAuthError(
      "Ops authorization is required, but neither VALOR_OPS_SECRET nor VALOR_SESSION_SECRET is configured.",
      503,
    );
  }

  return opsAuthError("Unauthorized ops request.", 401);
}

function applyRateLimit(
  request: Request,
  options: OpsAuthOptions,
): NextResponse | null {
  if (options.rateLimit) {
    const rateLimited = checkOpsRateLimit(request, options.rateLimit);
    if (rateLimited) return rateLimited;
  }
  return null;
}

function opsAuthRequired(env: NodeJS.ProcessEnv): boolean {
  return (
    env.NODE_ENV === "production" ||
    env.VALOR_REQUIRE_OPS_AUTH === "true" ||
    browserAuthRequired(env)
  );
}

function blockCrossOriginSessionWrite(
  request: Request,
  env: NodeJS.ProcessEnv,
): NextResponse | null {
  if (env.VALOR_DISABLE_CSRF_ORIGIN_CHECK === "true") return null;

  const origin = request.headers.get("origin");
  if (!origin) return null;

  const host =
    request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!host) return null;

  try {
    if (new URL(origin).host === host) return null;
  } catch {
    return opsAuthError("Invalid request origin.", 403);
  }

  return opsAuthError("Cross-origin browser mutation blocked.", 403);
}

function bearerTokenFromAuthorization(value: string | null): string | null {
  if (!value) return null;
  const [scheme, token] = value.split(" ");
  if (scheme?.toLowerCase() !== "bearer" || !token) return null;
  return token.trim() || null;
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  if (leftBuffer.length !== rightBuffer.length) return false;
  return timingSafeEqual(leftBuffer, rightBuffer);
}

function opsAuthError(error: string, status: 401 | 403 | 503): NextResponse {
  return NextResponse.json(
    { ok: false, error },
    {
      status,
      headers: {
        "cache-control": "no-store",
      },
    },
  );
}
