import type { LlmSettings } from "@/lib/llm/types";

export function readLlmSettings(env: NodeJS.ProcessEnv = process.env): LlmSettings {
  return {
    enabled: env.LLM_API_ENABLED === "true",
    provider: "openai-compatible",
    apiKey: env.LLM_API_KEY,
    baseUrl: env.LLM_API_BASE_URL ?? "https://api.openai.com/v1",
    model: env.LLM_MODEL ?? "gpt-4.1",
    timeoutMs: numberFromEnv(env.LLM_TIMEOUT_MS, 20_000),
    maxContextChars: numberFromEnv(env.LLM_MAX_CONTEXT_CHARS, 24_000),
    temperature: optionalNumberFromEnv(env.LLM_TEMPERATURE),
  };
}

export function llmConfigured(settings = readLlmSettings()): boolean {
  return Boolean(settings.enabled && settings.apiKey && settings.model);
}

function numberFromEnv(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function optionalNumberFromEnv(value: string | undefined): number | undefined {
  if (value === undefined || value.trim() === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}
