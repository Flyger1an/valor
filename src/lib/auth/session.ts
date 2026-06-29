import {
  createHmac,
  randomBytes,
  scrypt,
  timingSafeEqual,
  type ScryptOptions,
} from "node:crypto";
import { promisify } from "node:util";

export const SESSION_COOKIE_NAME = "valor_session";
export const DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60;

const HASH_ALGORITHM = "scrypt";
const HASH_KEY_LENGTH = 64;
const DEFAULT_SCRYPT = {
  cost: 16_384,
  blockSize: 8,
  parallelization: 1,
};

const scryptAsync = promisify(scrypt) as (
  password: string,
  salt: string,
  keylen: number,
  options: ScryptOptions,
) => Promise<Buffer>;

export interface BrowserSession {
  subject: "operator";
  issuedAt: number;
  expiresAt: number;
  nonce: string;
}

export interface SessionCookieOptions {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge: number;
}

export function browserAuthRequired(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.NODE_ENV === "production" || env.VALOR_REQUIRE_BROWSER_AUTH === "true";
}

export function sessionTtlSeconds(env: NodeJS.ProcessEnv = process.env): number {
  const parsed = Number(env.VALOR_SESSION_TTL_SECONDS);
  return Number.isFinite(parsed) && parsed > 0
    ? Math.floor(parsed)
    : DEFAULT_SESSION_TTL_SECONDS;
}

export function sessionCookieOptions(
  env: NodeJS.ProcessEnv = process.env,
): SessionCookieOptions {
  return {
    httpOnly: true,
    secure: env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: sessionTtlSeconds(env),
  };
}

export function createSessionToken(input: {
  secret: string;
  nowSeconds?: number;
  ttlSeconds?: number;
}): { token: string; session: BrowserSession } {
  const now = input.nowSeconds ?? nowSeconds();
  const ttl = input.ttlSeconds ?? DEFAULT_SESSION_TTL_SECONDS;
  const session: BrowserSession = {
    subject: "operator",
    issuedAt: now,
    expiresAt: now + ttl,
    nonce: randomBytes(16).toString("base64url"),
  };

  return {
    session,
    token: signSession(session, input.secret),
  };
}

export function createSessionTokenFromEnv(
  env: NodeJS.ProcessEnv = process.env,
): { token: string; session: BrowserSession; cookie: SessionCookieOptions } | null {
  const secret = env.VALOR_SESSION_SECRET?.trim();
  if (!secret) return null;
  const created = createSessionToken({
    secret,
    ttlSeconds: sessionTtlSeconds(env),
  });
  return {
    ...created,
    cookie: sessionCookieOptions(env),
  };
}

export function sessionFromCookieHeader(
  cookieHeader: string | null,
  env: NodeJS.ProcessEnv = process.env,
): BrowserSession | null {
  const token = cookieValue(cookieHeader, SESSION_COOKIE_NAME);
  return sessionFromToken(token, env);
}

export function sessionFromToken(
  token: string | undefined | null,
  env: NodeJS.ProcessEnv = process.env,
): BrowserSession | null {
  const secret = env.VALOR_SESSION_SECRET?.trim();
  if (!token || !secret) return null;
  return verifySessionToken(token, secret);
}

export function verifySessionToken(
  token: string,
  secret: string,
  now = nowSeconds(),
): BrowserSession | null {
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return null;

  const expected = hmac(payload, secret);
  if (!constantTimeEqual(signature, expected)) return null;

  try {
    const parsed = JSON.parse(
      Buffer.from(payload, "base64url").toString("utf8"),
    ) as Partial<BrowserSession>;

    const issuedAt = parsed.issuedAt;
    const expiresAt = parsed.expiresAt;

    if (parsed.subject !== "operator") return null;
    if (!Number.isFinite(issuedAt) || !Number.isFinite(expiresAt)) {
      return null;
    }
    if (expiresAt === undefined || issuedAt === undefined || expiresAt <= now) {
      return null;
    }
    if (typeof parsed.nonce !== "string" || parsed.nonce.length < 16) return null;

    return {
      subject: parsed.subject,
      issuedAt,
      expiresAt,
      nonce: parsed.nonce,
    };
  } catch {
    return null;
  }
}

export async function hashAdminPassword(
  password: string,
  salt = randomBytes(16).toString("base64url"),
): Promise<string> {
  const hash = await scryptHash(password, salt, DEFAULT_SCRYPT);
  return [
    HASH_ALGORITHM,
    DEFAULT_SCRYPT.cost,
    DEFAULT_SCRYPT.blockSize,
    DEFAULT_SCRYPT.parallelization,
    salt,
    hash.toString("base64url"),
  ].join("$");
}

export async function verifyAdminPassword(
  password: string,
  encodedHash: string | undefined,
): Promise<boolean> {
  if (!encodedHash) return false;
  const parts = encodedHash.split("$");
  if (parts.length !== 6 || parts[0] !== HASH_ALGORITHM) return false;

  const [, costRaw, blockSizeRaw, parallelizationRaw, salt, expectedRaw] = parts;
  const params = {
    cost: Number(costRaw),
    blockSize: Number(blockSizeRaw),
    parallelization: Number(parallelizationRaw),
  };

  if (
    !Number.isFinite(params.cost) ||
    !Number.isFinite(params.blockSize) ||
    !Number.isFinite(params.parallelization) ||
    !salt ||
    !expectedRaw
  ) {
    return false;
  }

  const expected = Buffer.from(expectedRaw, "base64url");
  const actual = await scryptHash(password, salt, params);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

function signSession(session: BrowserSession, secret: string): string {
  const payload = Buffer.from(JSON.stringify(session)).toString("base64url");
  return `${payload}.${hmac(payload, secret)}`;
}

function hmac(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  if (leftBuffer.length !== rightBuffer.length) return false;
  return timingSafeEqual(leftBuffer, rightBuffer);
}

async function scryptHash(
  password: string,
  salt: string,
  params: typeof DEFAULT_SCRYPT,
): Promise<Buffer> {
  return scryptAsync(password, salt, HASH_KEY_LENGTH, {
    N: params.cost,
    r: params.blockSize,
    p: params.parallelization,
  });
}

function cookieValue(cookieHeader: string | null, name: string): string | null {
  if (!cookieHeader) return null;
  for (const part of cookieHeader.split(";")) {
    const [rawName, ...rawValue] = part.trim().split("=");
    if (rawName === name) return rawValue.join("=") || null;
  }
  return null;
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}
