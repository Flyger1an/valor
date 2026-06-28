import { NextRequest, NextResponse } from "next/server";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import {
  buildOperatorEvidencePacket,
  formatOperatorEvidenceMarkdown,
} from "@/lib/reports/operator-evidence-packet";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const state = await buildDashboardState();
  const packet = buildOperatorEvidencePacket(state);
  const format = request.nextUrl.searchParams.get("format");

  if (format === "markdown" || format === "md") {
    return new NextResponse(formatOperatorEvidenceMarkdown(packet), {
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  }

  return NextResponse.json(
    {
      ok: true,
      packet,
      formats: {
        markdown: "/api/ops/evidence-packet?format=markdown",
      },
    },
    {
      headers: {
        "cache-control": "no-store",
      },
    },
  );
}
