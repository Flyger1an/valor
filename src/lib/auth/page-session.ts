import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import {
  browserAuthRequired,
  SESSION_COOKIE_NAME,
  sessionFromToken,
  type BrowserSession,
} from "@/lib/auth/session";

export async function getBrowserSession(
  env: NodeJS.ProcessEnv = process.env,
): Promise<BrowserSession | null> {
  const cookieStore = await cookies();
  return sessionFromToken(cookieStore.get(SESSION_COOKIE_NAME)?.value, env);
}

export async function requireBrowserSession(
  env: NodeJS.ProcessEnv = process.env,
): Promise<BrowserSession> {
  const session = await getBrowserSession(env);
  if (session) return session;

  if (!browserAuthRequired(env)) {
    return {
      subject: "operator",
      issuedAt: 0,
      expiresAt: Number.MAX_SAFE_INTEGER,
      nonce: "development-auth-bypass",
    };
  }

  redirect("/login");
}
