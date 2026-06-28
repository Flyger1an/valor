import { NextResponse } from "next/server";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";

export const dynamic = "force-dynamic";

export async function POST() {
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
