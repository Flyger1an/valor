import { NextResponse } from "next/server";
import { runBasisCarryBacktest } from "@/lib/backtest/backtester";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { LocalStateStore } from "@/lib/state/local-store";

export const dynamic = "force-dynamic";

export async function POST() {
  const store = new LocalStateStore();
  let state = store.read();
  if (!state.data) {
    await refreshAndPersistMarketState();
    state = store.read();
  }

  const backtest = runBasisCarryBacktest(state.data!.backtestHistory);
  store.update((current) => ({ ...current, backtest }));
  store.appendAction({
    action: "backtest.run",
    status: "ok",
    message: `${backtest.strategyName} return ${backtest.totalReturnPct}% with ${backtest.trades.length} trades.`,
  });

  return NextResponse.json({
    ok: true,
    strategyName: backtest.strategyName,
    totalReturnPct: backtest.totalReturnPct,
    maxDrawdownPct: backtest.maxDrawdownPct,
    trades: backtest.trades.length,
  });
}
