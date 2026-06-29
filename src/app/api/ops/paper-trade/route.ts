import { NextResponse } from "next/server";
import { applyEdgeScoreboardPolicy } from "@/lib/edge/policy";
import { requireOpsAuth } from "@/lib/ops/auth";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { simulatePaperPortfolio } from "@/lib/paper/paper-broker";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { getStateStore } from "@/lib/state/store-factory";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const blocked = requireOpsAuth(request, {
    access: "write",
    rateLimit: { scope: "ops.paper-trade", limit: 20, windowMs: 60_000 },
  });
  if (blocked) return blocked;

  const store = getStateStore();
  let state = store.read();
  if (!state.signals || !state.risk) {
    await refreshAndPersistMarketState();
    state = store.read();
  }

  const edgePolicy = applyEdgeScoreboardPolicy({
    signals: state.signals!,
    paper: state.paper ?? simulatePaperPortfolio({
      signals: state.signals!,
      risk: state.risk!,
      dataQuality: state.dataQuality,
      marketData: state.data,
    }),
    updatedAt: state.data?.generatedAt,
  });
  const systemTrust = evaluateSystemTrust({
    dataQuality: state.dataQuality,
    risk: state.risk,
    schedulerStatus: state.schedulerStatus,
    alertDeliveries: state.alertDeliveries,
    killSwitch: state.killSwitch,
    paper: state.paper,
    now: state.data ? new Date(state.data.generatedAt) : undefined,
  });
  const paper = simulatePaperPortfolio({
    signals: edgePolicy.signals,
    risk: state.risk!,
    dataQuality: state.dataQuality,
    systemTrust,
    previousPortfolio: state.paper,
    marketData: state.data,
  });
  store.update((current) => ({
    ...current,
    signals: edgePolicy.signals,
    paper,
    systemTrust,
  }));
  store.appendAction({
    action: "paper.trade",
    status: systemTrust.blocksPaperTrading ? "dry_run" : "ok",
    message: `Opened ${paper.trades.length} simulated paper trade(s); ${paper.rejectedSignals.length} rejected; ${edgePolicy.decision.blockedSignalCount} edge-policy block(s); system trust ${systemTrust.status}.`,
  });

  return NextResponse.json({
    ok: true,
    trades: paper.trades.length,
    positions: paper.positions.length,
    rejected: paper.rejectedSignals.length,
    edgePolicyBlocks: edgePolicy.decision.blockedSignalCount,
    blockedKinds: edgePolicy.decision.blockedKinds,
    systemTrust: {
      status: systemTrust.status,
      blocksPaperTrading: systemTrust.blocksPaperTrading,
      summary: systemTrust.summary,
    },
    equityUsd: paper.equityUsd,
  });
}
