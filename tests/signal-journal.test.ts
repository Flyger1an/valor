import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { buildSignalJournal } from "@/lib/signals/signal-journal";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("buildSignalJournal", () => {
  it("increments sightings and tracks edge delta", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const first = buildSignalJournal({
      signals,
      timestamp: sampleMarketData.generatedAt,
    });

    const mutated = signals.map((signal, index) =>
      index === 0
        ? { ...signal, expectedEdgeBps: signal.expectedEdgeBps + 12 }
        : signal,
    );

    const second = buildSignalJournal({
      signals: mutated,
      previous: first.entries,
      timestamp: new Date(Date.parse(sampleMarketData.generatedAt) + 120_000).toISOString(),
    });

    expect(second.entries[0]?.sightings).toBe(2);
    expect(second.entries[0]?.edgeDeltaBps).toBe(12);
    expect(second.persistedSignals).toBeGreaterThan(0);
  });
});