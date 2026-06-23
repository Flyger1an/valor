import { NextResponse } from "next/server";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export const dynamic = "force-dynamic";

export async function GET() {
  const state = await getDashboardState();

  return NextResponse.json({
    ok: true,
    generatedAt: state.data.generatedAt,
    lastRefreshAt: state.data.generatedAt,
    dataAgeLabel: state.dataFreshness.ageLabel,
    connector: state.connector.label,
    riskState: state.risk.state,
    riskScore: state.risk.score,
    signalCount: state.signals.length,
    paperEligible: state.signals.filter((signal) => signal.eligibleForPaperTrading)
      .length,
    alertCount: state.risk.activeAlerts.length,
    paperEquityUsd: state.paper.equityUsd,
    killSwitchActive: Boolean(state.killSwitch?.active),
    liveMarketCount: state.dataProvenance.liveMarketCount,
    fixtureMarketCount: state.dataProvenance.fixtureMarketCount,
    llmMode: state.llmStatus.mode,
    timestamp: new Date().toISOString(),
  });
}