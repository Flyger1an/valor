import { NextResponse } from "next/server";
import { readLiveTradingSettings } from "@/lib/live/live-trading";
import { buildDeploymentHealthReport } from "@/lib/ops/deployment-health";
import { schedulerConfigFromEnv } from "@/lib/ops/scheduler";
import { getStateStore } from "@/lib/state/store-factory";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const state = getStateStore().read();
    const schedulerConfig = schedulerConfigFromEnv();
    const report = buildDeploymentHealthReport({
      state,
      liveSettings: readLiveTradingSettings(),
      schedulerStaleAfterMs: schedulerConfig.staleAfterMs,
    });

    return NextResponse.json(
      {
        ok: report.ready,
        report,
      },
      {
        headers: {
          "cache-control": "no-store",
        },
      },
    );
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        report: {
          status: "blocked",
          ready: false,
          summary: `Deployment health failed to read local state: ${
            error instanceof Error ? error.message : "unknown error"
          }`,
        },
      },
      { status: 500 },
    );
  }
}
