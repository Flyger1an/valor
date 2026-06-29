import { describe, expect, it } from "vitest";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import { askAnalyst } from "@/lib/llm/analyst";
import { extractRiskItemsLocally } from "@/lib/llm/extraction";

describe("LLM analyst guardrails", () => {
  it("runs in offline RAG mode without an API key", async () => {
    const state = await buildDashboardState();
    const response = await askAnalyst({
      question: "Should I live trade the top signal now?",
      state,
      settings: {
        enabled: false,
        provider: "openai-compatible",
        baseUrl: "https://example.invalid/v1",
        model: "test-model",
        timeoutMs: 100,
        maxContextChars: 4000,
      },
    });

    expect(response.mode).toBe("offline");
    expect(response.answer).toContain("cannot authorize");
    expect(response.guardrail.toLowerCase()).toContain("deterministic");
    expect(response.citations.length).toBeGreaterThan(0);
  });

  it("grounds readiness questions in runbook and tiny-live evidence", async () => {
    const state = await buildDashboardState();
    const response = await askAnalyst({
      question:
        "What is blocking tiny-live readiness, why is it no go, and which runbook step should I handle first?",
      state,
      settings: {
        enabled: false,
        provider: "openai-compatible",
        baseUrl: "https://example.invalid/v1",
        model: "test-model",
        timeoutMs: 100,
        maxContextChars: 6000,
      },
    });

    expect(response.mode).toBe("offline");
    expect(response.citations.some((citation) => citation.id === "ops:readiness")).toBe(true);
    expect(response.citations.some((citation) => citation.id === "ops:runbook")).toBe(true);
    expect(response.answer).toContain("[SOURCE ops:readiness]");
    expect(response.answer).toContain("[SOURCE ops:runbook]");
    expect(response.answer.toLowerCase()).toContain("no go");
  });

  it("extracts candidate risk items from unstructured text", () => {
    const items = extractRiskItemsLocally(
      "Kraken reports paused USDT withdrawals after a confirmed bridge exploit.",
    );

    expect(items.length).toBeGreaterThanOrEqual(2);
    expect(items.some((item) => item.severity === "CRITICAL")).toBe(true);
  });
});
