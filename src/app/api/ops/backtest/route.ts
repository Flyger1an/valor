import { NextResponse } from "next/server";
import { runBasisCarryBacktest } from "@/lib/backtest/backtester";
import { requireOpsAuth } from "@/lib/ops/auth";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { getStateStore } from "@/lib/state/store-factory";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const blocked = requireOpsAuth(request, {
    access: "write",
    rateLimit: { scope: "ops.backtest", limit: 10, windowMs: 60_000 },
  });
  if (blocked) return blocked;

  const store = getStateStore();
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
