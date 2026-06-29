"use client";

import { Bot, SendHorizontal } from "lucide-react";
import { useState } from "react";

interface AnalystResponse {
  mode: "offline" | "llm";
  answer: string;
  guardrail: string;
  citations: Array<{ id: string; title: string; kind: string }>;
  model?: string;
}

export function AnalystCopilot(props: {
  configured: boolean;
  model: string;
}) {
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
      const json = await result.json();
      if (!result.ok) {
        setResponse({
          mode: "offline",
          answer: json.error ?? "Analyst request was blocked.",
          guardrail: "Ops authorization blocked this request.",
          citations: [],
        });
        return;
      }
      setResponse(json as AnalystResponse);
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
          <span className={props.configured ? "pill ok" : "pill muted-pill"}>
            {props.configured ? "LLM plugged" : "Offline mode"}
          </span>
          <span className="pill">{props.model}</span>
        </div>
        <textarea
          className="copilot-input"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={4}
        />
        <button className="copilot-button" type="button" onClick={ask} disabled={loading}>
          <SendHorizontal size={15} aria-hidden="true" />
          {loading ? "Thinking" : "Ask"}
        </button>
      </div>
      <div className="panel copilot-output">
        <h3>Answer</h3>
        {response ? (
          <>
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
            Ask questions over current signals, risk alerts, backtest assumptions,
            and paper-trading state.
          </p>
        )}
      </div>
    </div>
  );
}
