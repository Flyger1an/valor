import { NextRequest, NextResponse } from "next/server";
import { sendAlertNow } from "@/lib/ops/alert-delivery";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { LocalStateStore } from "@/lib/state/local-store";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as {
    alertId?: string;
  } | null;
  const store = new LocalStateStore();
  let state = store.read();
  if (state.alertEvents.length === 0) {
    await refreshAndPersistMarketState();
    state = store.read();
  }

  const alert =
    state.alertEvents.find((item) => item.id === body?.alertId) ??
    state.alertEvents[0];

  if (!alert) {
    return NextResponse.json({ ok: false, error: "No alert available" }, { status: 404 });
  }

  const result = await sendAlertNow(alert);

  return NextResponse.json({
    ok: true,
    alertId: alert.id,
    suppressed: result.routed.suppressed,
    deliveries: result.deliveries.map((delivery) => ({
      channel: delivery.channel,
      status: delivery.status,
      destination: delivery.destination,
      error: delivery.error,
    })),
    reasons: result.routed.reasons,
  });
}
