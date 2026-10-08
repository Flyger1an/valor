import { NextResponse } from "next/server";
import { requireOpsAuth } from "@/lib/ops/auth";
import { loadTradingRuntimeStatus } from "@/lib/trading/runtime-status";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const blocked = requireOpsAuth(request, {
    access: "read", rateLimit: { scope: "ops.trading-runtime", limit: 120, windowMs: 60_000 },
  }, { ...process.env, VALOR_PUBLIC_READ_APIS: "false", VALOR_REQUIRE_OPS_AUTH: "true" });
  if (blocked) return blocked;
  const report = loadTradingRuntimeStatus();
  return NextResponse.json({ ok: report.status === "current", report }, {
    headers: { "cache-control": "no-store" },
  });
}
