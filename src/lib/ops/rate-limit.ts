import { NextResponse } from "next/server";

export interface OpsRateLimitOptions {
  scope: string;
  limit: number;
  windowMs: number;
  identifier?: string;
  nowMs?: number;
}

interface RateLimitBucket {
  count: number;
  resetAt: number;
}

const buckets = new Map<string, RateLimitBucket>();

export function checkOpsRateLimit(
  request: Request,
  options: OpsRateLimitOptions,
): NextResponse | null {
  const now = options.nowMs ?? Date.now();
  const key = `${options.scope}:${options.identifier ?? clientIdentifier(request)}`;
  const existing = buckets.get(key);
  const bucket =
    existing && existing.resetAt > now
      ? existing
      : { count: 0, resetAt: now + options.windowMs };

  bucket.count += 1;
  buckets.set(key, bucket);
  cleanupExpiredBuckets(now);

  if (bucket.count <= options.limit) {
    return null;
  }

  const retryAfterSeconds = Math.max(
    1,
    Math.ceil((bucket.resetAt - now) / 1000),
  );

  return NextResponse.json(
    {
      ok: false,
      error: "Ops rate limit exceeded.",
      scope: options.scope,
      retryAfterSeconds,
    },
    {
      status: 429,
      headers: {
        "cache-control": "no-store",
        "retry-after": String(retryAfterSeconds),
        "x-ratelimit-limit": String(options.limit),
        "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": new Date(bucket.resetAt).toISOString(),
      },
    },
  );
}

export function clearOpsRateLimitBuckets() {
  buckets.clear();
}

function clientIdentifier(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  const realIp = request.headers.get("x-real-ip")?.trim();
  const connectingIp = request.headers.get("cf-connecting-ip")?.trim();
  return forwarded || realIp || connectingIp || "local";
}

function cleanupExpiredBuckets(now: number) {
  if (buckets.size < 1_000) return;
  for (const [key, bucket] of buckets) {
    if (bucket.resetAt <= now) buckets.delete(key);
  }
}
