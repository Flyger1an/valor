import { describe, expect, it } from "vitest";
import {
  CROSS_VENUE_TRANSFER_BPS,
  PAIR_ENTRY_COST_BPS,
  TAKER_FEE_BPS,
  netBasisEdgeBps,
  netCrossExchangeEdgeBps,
  netFundingCarryEdgeBps,
} from "@/lib/signals/costs";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("execution cost model", () => {
  it("nets two taker legs plus transfer from a cross-exchange premium", () => {
    expect(PAIR_ENTRY_COST_BPS).toBe(2 * TAKER_FEE_BPS);
    expect(netCrossExchangeEdgeBps(40)).toBe(
      40 - PAIR_ENTRY_COST_BPS - CROSS_VENUE_TRANSFER_BPS,
    );
  });

  it("nets a two-legged entry from basis and funding-carry edges", () => {
    expect(netBasisEdgeBps(100)).toBe(100 - PAIR_ENTRY_COST_BPS);
    expect(netFundingCarryEdgeBps(100)).toBe(100 - PAIR_ENTRY_COST_BPS);
  });
});

describe("signals rank on net-of-fee edges", () => {
  it("reports cross-exchange edge net of fees and not paper-eligible at sub-fee premia", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const cross = signals.find((s) => s.kind === "cross_exchange_premium");

    expect(cross).toBeDefined();
    // Fixture ETH premium is ~22.7 bps gross; net of ~20 bps fees+transfer is small.
    expect(cross!.expectedEdgeBps).toBeLessThan(15);
    expect(cross!.eligibleForPaperTrading).toBe(false);
  });

  it("keeps strong basis/funding signals eligible after fees", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);

    expect(
      signals.some(
        (s) =>
          (s.kind === "spot_perp_basis" || s.kind === "funding_carry") &&
          s.eligibleForPaperTrading,
      ),
    ).toBe(true);
  });
});
