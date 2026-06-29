import { NextRequest, NextResponse } from "next/server";
import {
  createSessionTokenFromEnv,
  SESSION_COOKIE_NAME,
  verifyAdminPassword,
} from "@/lib/auth/session";
import { checkOpsRateLimit } from "@/lib/ops/rate-limit";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const rateLimited = checkOpsRateLimit(request, {
    scope: "auth.login",
    limit: 5,
    windowMs: 60_000,
  });
  if (rateLimited) return rateLimited;

  const body = (await request.json().catch(() => null)) as {
    password?: unknown;
  } | null;
  const password = typeof body?.password === "string" ? body.password : "";

  if (
    !process.env.VALOR_SESSION_SECRET?.trim() ||
    !process.env.VALOR_ADMIN_PASSWORD_HASH?.trim()
  ) {
    return NextResponse.json(
      {
        ok: false,
        error:
          "Browser session auth is not configured. Set VALOR_SESSION_SECRET and VALOR_ADMIN_PASSWORD_HASH.",
      },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }

  const valid = await verifyAdminPassword(
    password,
    process.env.VALOR_ADMIN_PASSWORD_HASH,
  );
  if (!valid) {
    return NextResponse.json(
      { ok: false, error: "Invalid credentials." },
      { status: 401, headers: { "cache-control": "no-store" } },
    );
  }

  const created = createSessionTokenFromEnv();
  if (!created) {
    return NextResponse.json(
      { ok: false, error: "Browser session auth is not configured." },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }

  const response = NextResponse.json(
    {
      ok: true,
      expiresAt: new Date(created.session.expiresAt * 1000).toISOString(),
    },
    { headers: { "cache-control": "no-store" } },
  );
  response.cookies.set(SESSION_COOKIE_NAME, created.token, created.cookie);
  return response;
}
