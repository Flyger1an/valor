import { NextResponse } from "next/server";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import { requireOpsAuth } from "@/lib/ops/auth";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const blocked = requireOpsAuth(request, {
    access: "read",
    rateLimit: { scope: "ops.evolver-evidence", limit: 120, windowMs: 60_000 },
  });
  if (blocked) return blocked;

  const state = await buildDashboardState();

  return NextResponse.json(
    {
      ok: true,
      evolverEvidence: state.evolverEvidence,
    },
    {
      headers: {
        "cache-control": "no-store",
      },
    },
  );
}
