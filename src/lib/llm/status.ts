import { llmConfigured, readLlmSettings } from "@/lib/llm/settings";

export interface LlmRuntimeStatus {
  enabled: boolean;
  hasApiKey: boolean;
  configured: boolean;
  mode: "offline" | "live";
  model: string | null;
  baseUrl: string;
  setupHint: string;
}

export function buildLlmRuntimeStatus(
  env: NodeJS.ProcessEnv = process.env,
): LlmRuntimeStatus {
  const settings = readLlmSettings(env);
  const hasApiKey = Boolean(settings.apiKey?.trim());
  const configured = llmConfigured(settings);

  return {
    enabled: settings.enabled,
    hasApiKey,
    configured,
    mode: configured ? "live" : "offline",
    model: configured ? settings.model : null,
    baseUrl: settings.baseUrl.replace(/\/\/.*@/, "//[REDACTED]@"),
    setupHint: configured
      ? "LLM calls will hit your configured provider."
      : !settings.enabled
        ? "Set LLM_API_ENABLED=true and LLM_API_KEY in .env.local to enable live analyst calls."
        : "LLM_API_ENABLED=true but LLM_API_KEY is missing. Analyst stays in offline RAG mode.",
  };
}