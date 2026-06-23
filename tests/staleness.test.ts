import { describe, expect, it } from "vitest";
import { dataAgeMs, formatDataAge, isDataStale } from "@/lib/data/staleness";

describe("data staleness", () => {
  it("flags old bundles as stale", () => {
    const generatedAt = new Date(Date.now() - 600_000).toISOString();
    expect(isDataStale(generatedAt, 300_000)).toBe(true);
    expect(dataAgeMs(generatedAt)).toBeGreaterThan(500_000);
    expect(formatDataAge(generatedAt)).toContain("m ago");
  });
});