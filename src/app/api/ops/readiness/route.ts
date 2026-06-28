import { NextResponse } from "next/server";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";

export const dynamic = "force-dynamic";

export async function GET() {
  const state = await buildDashboardState();

  return NextResponse.json({
    ok: true,
    readiness: state.tinyLiveReadiness,
  });
}
