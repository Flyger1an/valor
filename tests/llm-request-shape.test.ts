import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import { askAnalyst } from "@/lib/llm/analyst";
import type { LlmSettings } from "@/lib/llm/types";

type DashboardState = Awaited<ReturnType<typeof buildDashboardState>>;

function liveSettings(overrides: Partial<LlmSettings> = {}): LlmSettings {
  return {
    enabled: true,
    provider: "openai-compatible",
    apiKey: "test-key",
    baseUrl: "https://example.invalid/v1",
    model: "gpt-5.5",
    timeoutMs: 1000,
    maxContextChars: 4000,
    ...overrides,
  };
}

describe("LLM live request shape", () => {
  let state: DashboardState;

  beforeAll(async () => {
    // Build state with the real connector once, before fetch is stubbed.
    state = await buildDashboardState();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function captureRequestBody(settings: LlmSettings) {
    let captured: Record<string, unknown> | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: { body: string }) => {
        captured = JSON.parse(init.body);
        return new Response(
          JSON.stringify({ choices: [{ message: { content: "pong" } }] }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }),
    );
    const res = await askAnalyst({ question: "status?", state, settings });
    return { captured, res };
  }

  it("omits temperature by default so reasoning models accept the call", async () => {
    const { captured, res } = await captureRequestBody(liveSettings());

    expect(res.mode).toBe("llm");
    expect(captured).not.toBeNull();
    expect(captured && "temperature" in captured).toBe(false);
    expect(captured!.model).toBe("gpt-5.5");
  });

  it("includes temperature only when explicitly configured", async () => {
    const { captured } = await captureRequestBody(liveSettings({ temperature: 0.2 }));

    expect(captured!.temperature).toBe(0.2);
  });
});
