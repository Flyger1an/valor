import { describe, expect, it } from "vitest";
import {
  CoinGeckoSpotConnector,
  getDefaultConnector,
  PublicCryptoMarketConnector,
  SampleMarketDataConnector,
} from "@/lib/data/connectors";

describe("market data connector selection", () => {
  it("uses deterministic sample data when the env flag is absent", () => {
    const connector = getDefaultConnector({});

    expect(connector).toBeInstanceOf(SampleMarketDataConnector);
    expect(connector.mode).toBe("sample");
  });

  it("keeps false-like env values in sample mode", () => {
    for (const value of ["false", "0", "off", "sample"]) {
      const connector = getDefaultConnector({ ENABLE_PUBLIC_MARKET_FETCH: value });

      expect(connector).toBeInstanceOf(SampleMarketDataConnector);
    }
  });

  it("supports explicit public connector modes", () => {
    expect(
      getDefaultConnector({ ENABLE_PUBLIC_MARKET_FETCH: "coingecko" }),
    ).toBeInstanceOf(CoinGeckoSpotConnector);
    expect(
      getDefaultConnector({ ENABLE_PUBLIC_MARKET_FETCH: "true" }),
    ).toBeInstanceOf(PublicCryptoMarketConnector);
    expect(
      getDefaultConnector({ ENABLE_PUBLIC_MARKET_FETCH: "public" }),
    ).toBeInstanceOf(PublicCryptoMarketConnector);
  });

  it("falls back to sample mode for unknown env values", () => {
    const connector = getDefaultConnector({
      ENABLE_PUBLIC_MARKET_FETCH: "surprise-me",
    });

    expect(connector).toBeInstanceOf(SampleMarketDataConnector);
  });
});
