import { NextRequest, NextResponse } from "next/server";
import {
  runSchedulerCycle,
  schedulerConfigFromEnv,
  schedulerLeaseHealth,
} from "@/lib/ops/scheduler";
import { getStateStore } from "@/lib/state/store-factory";

export const dynamic = "force-dynamic";

export async function GET() {
  const state = getStateStore().read();
  const config = schedulerConfigFromEnv();
  return NextResponse.json({
    ok: true,
    schedulerStatus: state.schedulerStatus,
    lease: schedulerLeaseHealth(
      state.schedulerStatus,
      new Date(),
      config.staleAfterMs,
    ),
  });
}

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as {
    sendAlerts?: boolean;
    alertLimit?: number;
  } | null;
  const config = schedulerConfigFromEnv();
  const result = await runSchedulerCycle({
    sendAlerts: body?.sendAlerts ?? config.sendAlerts,
    alertLimit: body?.alertLimit ?? config.alertLimit,
    staleAfterMs: config.staleAfterMs,
  });

  return NextResponse.json({
    ok: result.status === "success",
    result,
  });
}
