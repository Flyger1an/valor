import { NextResponse } from "next/server";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";

export const dynamic = "force-dynamic";

export async function POST() {
  const { connector, data, computed } = await refreshAndPersistMarketState();
  return NextResponse.json({
    ok: true,
    connector: connector.label,
    generatedAt: data.generatedAt,
    markets: data.markets.length,
    signals: computed.signals.length,
    riskState: computed.risk.state,
    alerts: computed.alertEvents.length,
  });
}
