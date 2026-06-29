import { NextResponse } from "next/server";
import {
  SESSION_COOKIE_NAME,
  sessionCookieOptions,
} from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export async function POST() {
  const response = NextResponse.json(
    { ok: true },
    { headers: { "cache-control": "no-store" } },
  );
  response.cookies.set(SESSION_COOKIE_NAME, "", {
    ...sessionCookieOptions(),
    maxAge: 0,
  });
  return response;
}
