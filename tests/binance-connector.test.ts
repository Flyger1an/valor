import { describe, expect, it } from "vitest";
import { BinanceMarketConnector } from "@/lib/data/connectors";

const liveBinance = process.env.BINANCE_LIVE_TEST === "true";

describe("BinanceMarketConnector", () => {
  it("exposes a public binance adapter", () => {
    const connector = new BinanceMarketConnector();
    expect(connector.id).toBe("binance-public");
    expect(connector.needsApiKey).toBe(false);
  });

  it.skipIf(!liveBinance)("fetches live spot and perp markets", async () => {
    const connector = new BinanceMarketConnector();
    const data = await connector.fetchLatest();

    expect(data.markets.length).toBeGreaterThanOrEqual(6);
    expect(data.markets.some((market) => market.venue === "binance")).toBe(true);
    expect(
      data.markets.some(
        (market) => market.instrumentType === "perp" && market.fundingRate8h !== undefined,
      ),
    ).toBe(true);
  }, 20_000);
});