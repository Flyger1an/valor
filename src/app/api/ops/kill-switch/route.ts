import { NextRequest, NextResponse } from "next/server";
import { FileKillSwitchStore } from "@/lib/kill-switch/kill-switch";
import { getStateStore } from "@/lib/state/store-factory";

export const dynamic = "force-dynamic";

export async function GET() {
  const state = new FileKillSwitchStore().read();
  return NextResponse.json({ ok: true, state });
}

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as {
    action?: "activate" | "request_resume" | "dashboard_reset";
    reason?: string;
  } | null;
  const killSwitch = new FileKillSwitchStore();
  const local = getStateStore();

  const next =
    body?.action === "dashboard_reset"
      ? killSwitch.manualDashboardReset({ actor: "dashboard" })
      : body?.action === "request_resume"
        ? killSwitch.requestResume({ actor: "dashboard" })
        : killSwitch.activate({
            actor: "dashboard",
            reason: body?.reason ?? "Manual dashboard kill switch activation",
          });

  local.update((state) => ({ ...state, killSwitch: next }));
  local.appendAction({
    action: "kill_switch",
    status: "ok",
    message: next.active ? `Activated: ${next.reason}` : next.reason,
  });

  return NextResponse.json({ ok: true, state: next });
}
