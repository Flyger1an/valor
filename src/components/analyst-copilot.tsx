"use client";

import { Bot, SendHorizontal } from "lucide-react";
import { useState } from "react";
import type { LlmRuntimeStatus } from "@/lib/llm/status";

interface AnalystResponse {
  mode: "offline" | "llm";
  answer: string;
  guardrail: string;
  citations: Array<{ id: string; title: string; kind: string }>;
  model?: string;
}

export function AnalystCopilot(props: { llm: LlmRuntimeStatus }) {
  const [question, setQuestion] = useState(
    "What changed in risk state and which signals are paper-review candidates?",
  );
  const [response, setResponse] = useState<AnalystResponse | null>(null);
  const [loading, setLoading] = useState(false);

  async function ask() {
    setLoading(true);
    try {
      const result = await fetch("/api/analyst/copilot", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question }),
      });
      setResponse((await result.json()) as AnalystResponse);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="analyst-grid">
      <div className="panel">
        <h3>
          <Bot size={15} aria-hidden="true" />
          Analyst Copilot
        </h3>
        <div className="copilot-status">
          <span className={props.llm.configured ? "pill ok" : "pill muted-pill"}>
            {props.llm.configured ? "Live LLM" : "Offline RAG only"}
          </span>
          {props.llm.model ? <span className="pill">{props.llm.model}</span> : null}
        </div>
        <p className="setup-hint">{props.llm.setupHint}</p>
        <textarea
          className="copilot-input"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={4}
        />
        <button className="copilot-button" type="button" onClick={ask} disabled={loading}>
          <SendHorizontal size={15} aria-hidden="true" />
          {loading ? "Thinking" : props.llm.configured ? "Ask LLM" : "Ask offline analyst"}
        </button>
      </div>
      <div className="panel copilot-output">
        <h3>Answer</h3>
        {response ? (
          <>
            <p className="copilot-mode">
              Response mode: <strong>{response.mode}</strong>
              {response.model ? ` · ${response.model}` : ""}
            </p>
            <p className="copilot-answer">{response.answer}</p>
            <p className="guardrail-copy">{response.guardrail}</p>
            <div className="citation-list">
              {response.citations.map((citation) => (
                <span key={citation.id}>
                  {citation.id} · {citation.kind}
                </span>
              ))}
            </div>
          </>
        ) : (
          <p className="muted">
            Offline mode still searches local signals, risk, backtests, and paper
            state. Live mode requires LLM_API_ENABLED=true and LLM_API_KEY.
          </p>
        )}
      </div>
    </div>
  );
}