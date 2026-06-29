import { NextRequest, NextResponse } from "next/server";
import {
  runSchedulerCycle,
  schedulerConfigFromEnv,
  schedulerLeaseHealth,
} from "@/lib/ops/scheduler";
import { requireOpsAuth } from "@/lib/ops/auth";
import { getStateStore } from "@/lib/state/store-factory";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const blocked = requireOpsAuth(request, {
    access: "read",
    rateLimit: { scope: "ops.scheduler.read", limit: 120, windowMs: 60_000 },
  });
  if (blocked) return blocked;

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
  const blocked = requireOpsAuth(request, {
    access: "write",
    rateLimit: { scope: "ops.scheduler.write", limit: 12, windowMs: 60_000 },
  });
  if (blocked) return blocked;

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
