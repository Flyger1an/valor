import { NextResponse } from "next/server";
import { advancePaperBook } from "@/lib/paper/paper-book";
import { computeFromData } from "@/lib/ops/recompute";
import { LocalStateStore } from "@/lib/state/local-store";

export const dynamic = "force-dynamic";

export async function POST() {
  const store = new LocalStateStore();
  let state = store.read();
  if (!state.signals || !state.risk || !state.data) {
    return NextResponse.json(
      { ok: false, error: "Market state must be refreshed before paper trading." },
      { status: 400 },
    );
  }

  const computed = computeFromData(state.data, state);
  const paperBook = advancePaperBook({
    previous: state.paper,
    signals: state.signals,
    risk: state.risk,
    timestamp: new Date().toISOString(),
    equityHistory: state.equityHistory,
  });

  store.update((current) => ({
    ...current,
    paper: paperBook.portfolio,
    equityHistory: paperBook.equityHistory,
    signalJournal: computed.signalJournal.entries,
  }));

  store.appendAction({
    action: "paper.trade",
    status: "ok",
    message: `Paper book advanced: ${paperBook.opened} opened, ${paperBook.closed} closed, ${paperBook.marked} marked.`,
  });

  return NextResponse.json({
    ok: true,
    opened: paperBook.opened,
    closed: paperBook.closed,
    marked: paperBook.marked,
    positions: paperBook.portfolio.positions.length,
    equityUsd: paperBook.portfolio.equityUsd,
  });
}