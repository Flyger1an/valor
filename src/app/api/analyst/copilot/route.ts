import { NextRequest, NextResponse } from "next/server";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import { askAnalyst } from "@/lib/llm/analyst";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as {
    question?: string;
  } | null;

  const question = body?.question?.trim();
  if (!question) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  const state = await buildDashboardState();
  const response = await askAnalyst({ question, state });

  return NextResponse.json(response);
}
