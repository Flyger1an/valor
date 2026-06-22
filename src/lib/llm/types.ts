export interface LlmSettings {
  enabled: boolean;
  provider: "openai-compatible";
  apiKey?: string;
  baseUrl: string;
  model: string;
  timeoutMs: number;
  maxContextChars: number;
}

export interface RagDocument {
  id: string;
  title: string;
  kind: "risk" | "signal" | "backtest" | "paper" | "audit" | "docs";
  content: string;
  timestamp?: string;
}

export interface AnalystCitation {
  id: string;
  title: string;
  kind: RagDocument["kind"];
}

export interface AnalystResponse {
  mode: "offline" | "llm";
  answer: string;
  citations: AnalystCitation[];
  guardrail: string;
  model?: string;
  provider?: string;
}

export interface ExtractedRiskItem {
  severity: "INFO" | "WATCH" | "TRADEABLE" | "CRITICAL" | "BLACK";
  title: string;
  summary: string;
  affectedAssets: string[];
  affectedVenues: string[];
  confidence: number;
}
