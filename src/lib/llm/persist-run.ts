import { getDb } from "@/db/client";
import { llmAnalystRuns } from "@/db/schema";
import type { AnalystResponse } from "@/lib/llm/types";

export function persistAnalystRun(input: {
  question: string;
  response: AnalystResponse;
}) {
  if (process.env.VALOR_DISABLE_SQLITE === "true") return;

  const db = getDb();
  db.insert(llmAnalystRuns)
    .values({
      id: `llm:${Date.now()}:${Math.random().toString(16).slice(2)}`,
      mode: input.response.mode,
      model: input.response.model ?? null,
      questionRedacted: input.question.slice(0, 2000),
      answerRedacted: input.response.answer.slice(0, 8000),
      citationsJson: JSON.stringify(input.response.citations),
      createdAt: new Date().toISOString(),
    })
    .run();
}