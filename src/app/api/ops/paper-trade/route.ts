import { NextResponse } from "next/server";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { simulatePaperPortfolio } from "@/lib/paper/paper-broker";
import { LocalStateStore } from "@/lib/state/local-store";

export const dynamic = "force-dynamic";

export async function POST() {
  const store = new LocalStateStore();
  let state = store.read();
  if (!state.signals || !state.risk) {
    await refreshAndPersistMarketState();
    state = store.read();
  }

  const paper = simulatePaperPortfolio({
    signals: state.signals!,
    risk: state.risk!,
  });
  store.update((current) => ({ ...current, paper }));
  store.appendAction({
    action: "paper.trade",
    status: "ok",
    message: `Opened ${paper.trades.length} simulated paper trade(s); ${paper.rejectedSignals.length} rejected.`,
  });

  return NextResponse.json({
    ok: true,
    trades: paper.trades.length,
    positions: paper.positions.length,
    rejected: paper.rejectedSignals.length,
    equityUsd: paper.equityUsd,
  });
}
