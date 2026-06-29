import { NextResponse } from "next/server";
import { requireOpsAuth } from "@/lib/ops/auth";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const blocked = requireOpsAuth(request, {
    access: "write",
    rateLimit: { scope: "ops.refresh", limit: 20, windowMs: 60_000 },
  });
  if (blocked) return blocked;

  const { connector, data, dataQuality, computed } =
    await refreshAndPersistMarketState();
  return NextResponse.json({
    ok: true,
    connector: connector.label,
    dataQuality: {
      status: dataQuality.status,
      blocksPaperTrading: dataQuality.blocksPaperTrading,
      issues: dataQuality.issueCount,
      criticalIssues: dataQuality.criticalIssueCount,
    },
    generatedAt: data.generatedAt,
    markets: data.markets.length,
    signals: computed.signals.length,
    riskState: computed.risk.state,
    alerts: computed.alertEvents.length,
  });
}
