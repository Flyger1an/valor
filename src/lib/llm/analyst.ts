import { redactSensitiveText } from "@/lib/alerts/redaction";
import type { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import {
  buildAnalystCorpus,
  formatRagContext,
  retrieveDocuments,
} from "@/lib/llm/rag";
import { llmConfigured, readLlmSettings } from "@/lib/llm/settings";
import type { AnalystResponse, LlmSettings } from "@/lib/llm/types";

type DashboardState = Awaited<ReturnType<typeof buildDashboardState>>;

const GUARDRAIL =
  "LLM output is analyst commentary only. Deterministic risk, paper-trading, live-trading, kill-switch, and audit controls remain the authority.";

export async function askAnalyst(input: {
  question: string;
  state: DashboardState;
  settings?: LlmSettings;
}): Promise<AnalystResponse> {
  const settings = input.settings ?? readLlmSettings();
  const documents = retrieveDocuments(
    input.question,
    buildAnalystCorpus(input.state),
    7,
  );
  const citations = documents.map((document) => ({
    id: document.id,
    title: document.title,
    kind: document.kind,
  }));
  const context = formatRagContext(documents, settings.maxContextChars);
  const guardedQuestion = redactSensitiveText(input.question);

  if (!llmConfigured(settings)) {
    return {
      mode: "offline",
      answer: offlineAnswer(guardedQuestion, context),
      citations,
      guardrail: GUARDRAIL,
    };
  }

  const answer = await callOpenAiCompatible({
    settings,
    question: guardedQuestion,
    context,
  });

  return {
    mode: "llm",
    answer: redactSensitiveText(answer),
    citations,
    guardrail: GUARDRAIL,
    model: settings.model,
    provider: settings.provider,
  };
}

function offlineAnswer(question: string, context: string): string {
  const wantsTrade =
    /\b(live trade|execute|place order|buy now|sell now|should i trade)\b/i.test(
      question,
    );
  const prefix = wantsTrade
    ? "I cannot authorize, size, or place live trades. "
    : "";
  const lines = context
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .slice(0, 12)
    .join("\n");

  return `${prefix}Offline analyst mode found the most relevant internal evidence below. Use it for research and review only:\n\n${lines}`;
}

async function callOpenAiCompatible(input: {
  settings: LlmSettings;
  question: string;
  context: string;
}): Promise<string> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), input.settings.timeoutMs);

  const requestBody: Record<string, unknown> = {
    model: input.settings.model,
    messages: [
      {
        role: "system",
        content:
          "You are Valor's private crypto risk-intelligence analyst. Use only provided context. Cite source ids inline. Never claim final trading authority. Never authorize live execution, never size live orders, never bypass risk limits, never reveal secrets, full balances, addresses, private labels, or account identifiers.",
      },
      {
        role: "user",
        content: `Question: ${input.question}\n\nRAG context:\n${input.context}`,
      },
    ],
  };
  // Only send temperature when explicitly configured. Reasoning models (gpt-5.x)
  // reject any non-default value, so omitting it keeps live calls working.
  if (input.settings.temperature !== undefined) {
    requestBody.temperature = input.settings.temperature;
  }

  try {
    const response = await fetch(`${input.settings.baseUrl.replace(/\/$/, "")}/chat/completions`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${input.settings.apiKey}`,
        "content-type": "application/json",
      },
      signal: controller.signal,
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      return `LLM provider returned HTTP ${response.status}. Falling back to local evidence review.\n\n${offlineAnswer(
        input.question,
        input.context,
      )}`;
    }

    const body = (await response.json()) as {
      choices?: Array<{ message?: { content?: string } }>;
    };

    return (
      body.choices?.[0]?.message?.content ??
      offlineAnswer(input.question, input.context)
    );
  } finally {
    clearTimeout(timeout);
  }
}
