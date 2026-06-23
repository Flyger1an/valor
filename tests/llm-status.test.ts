import { afterEach, describe, expect, it } from "vitest";
import { buildLlmRuntimeStatus } from "@/lib/llm/status";

const original = { ...process.env };

afterEach(() => {
  process.env = { ...original };
});

describe("buildLlmRuntimeStatus", () => {
  it("reports offline when enabled flag or api key is missing", () => {
    const offline = buildLlmRuntimeStatus({
      LLM_API_ENABLED: "false",
      LLM_API_KEY: "",
      LLM_MODEL: "gpt-4.1",
    });

    expect(offline.configured).toBe(false);
    expect(offline.mode).toBe("offline");
    expect(offline.model).toBeNull();
    expect(offline.setupHint).toContain("LLM_API_ENABLED=true");
  });

  it("reports live only when enabled and key are both present", () => {
    const live = buildLlmRuntimeStatus({
      LLM_API_ENABLED: "true",
      LLM_API_KEY: "test-key",
      LLM_MODEL: "gpt-4.1",
    });

    expect(live.configured).toBe(true);
    expect(live.mode).toBe("live");
    expect(live.model).toBe("gpt-4.1");
  });
});