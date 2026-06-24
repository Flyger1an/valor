import type {
  MarketDataBundle,
  MarketSnapshot,
  PairSpreadPoint,
  RelativeValueSignal,
  SignalDirection,
  StablecoinSnapshot,
} from "@/lib/domain/types";
import {
  CROSS_VENUE_TRANSFER_BPS,
  PAIR_ENTRY_COST_BPS,
  netBasisEdgeBps,
  netCrossExchangeEdgeBps,
  netFundingCarryEdgeBps,
} from "@/lib/signals/costs";
import {
  augmentedDickeyFullerTest,
  clamp,
  mean,
  meanReversionHalfLifeHours,
  round,
  zScore,
} from "@/lib/utils/math";

/** Paper mean-reversion signals require rejecting a unit root at 5%. */
const STATIONARITY_SIGNIFICANCE = 0.05 as const;

// Calibration: ~1σ dislocation per carry/dislocation type (bps). These give a
// meaningful normalized z-score for signals that have no spread history of their
// own. Tunable by the optimizer's whitelist later.
const BASIS_SIGMA_BPS = 40;
const FUNDING_SIGMA_BPS = 60;
const XVENUE_SIGMA_BPS = 25;
const PEG_SIGMA_BPS = 30;

// Default convergence horizons (hours) where no spread history exists to fit a
// half-life. Carry/dislocation trades; the z-score signals derive theirs.
const CONVERGENCE_HOURS = { basis: 24, funding: 8, xvenue: 2, peg: 12 } as const;

// History points (btcEth/ethSol) are daily candles + a live point ≈ 24h spacing.
const HISTORY_PERIOD_HOURS = 24;

export function generateRelativeValueSignals(
  data: MarketDataBundle,
): RelativeValueSignal[] {
  const signals = [
    ...basisSignals(data.markets),
    ...fundingCarrySignals(data.markets),
    ...crossExchangeSignals(data.markets),
    ...btcEthRatioSignals(data.btcEthRatioHistory, data.generatedAt),
    ...pairSpreadSignals(data.ethSolSpreadHistory, "ETH/SOL", data.generatedAt),
    ...stablecoinSignals(data.stablecoins),
    volatilityRegimeSignal(data),
  ].filter(Boolean) as RelativeValueSignal[];

  return signals
    .map((signal) => ({
      ...signal,
      opportunityScore: opportunityScore(signal),
      eligibleForLiveTrading: false,
    }))
    .sort((a, b) => b.opportunityScore - a.opportunityScore);
}

function basisSignals(markets: MarketSnapshot[]): RelativeValueSignal[] {
  return markets
    .filter((market) => market.instrumentType === "perp")
    .map((perp) => {
      const spot = bestSpotFor(perp, markets);
      if (!spot) return null;

      const perpPrice = perp.markPrice ?? perp.price;
      const basisBps = ((perpPrice - spot.price) / spot.price) * 10_000;
      const sevenDayFundingBps = (perp.fundingRate8h ?? 0) * 10_000 * 21;
      const grossEdgeBps = basisBps + sevenDayFundingBps;
      const expectedEdgeBps = netBasisEdgeBps(grossEdgeBps);
      const liquidityScore = liquidityScoreFor([spot, perp]);
      const riskScore = riskScoreFor(perp, Math.abs(basisBps));

      return buildSignal({
        kind: "spot_perp_basis",
        assetPair: `${perp.base}/${perp.quote}`,
        venue: `${spot.venue} spot / ${perp.venue} perp`,
        direction:
          expectedEdgeBps > 0 ? "long_spot_short_perp" : "watch_only",
        confidence: clamp(0.48 + Math.abs(expectedEdgeBps) / 260, 0.35, 0.86),
        expectedEdgeBps,
        liquidityScore,
        riskScore,
        timestamp: perp.timestamp,
        zscore: basisBps / BASIS_SIGMA_BPS,
        spreadValue: basisBps / 10_000,
        expectedConvergenceHours: CONVERGENCE_HOURS.basis,
        eligibleForPaperTrading:
          expectedEdgeBps > 45 && liquidityScore > 55 && riskScore < 72,
        explanation: `${perp.base} perp trades ${round(
          basisBps,
          1,
        )} bps over best spot; 7-day funding carry ≈ ${round(
          sevenDayFundingBps,
          1,
        )} bps. Expected edge ${round(
          expectedEdgeBps,
          1,
        )} bps net of ~${PAIR_ENTRY_COST_BPS} bps taker fees.`,
      });
    })
    .filter(Boolean) as RelativeValueSignal[];
}

function fundingCarrySignals(markets: MarketSnapshot[]): RelativeValueSignal[] {
  return markets
    .filter(
      (market) =>
        market.instrumentType === "perp" &&
        Math.abs(market.fundingRate8h ?? 0) >= 0.00025,
    )
    .map((perp) => {
      const fundingRate = perp.fundingRate8h ?? 0;
      const sevenDayFundingBps = fundingRate * 10_000 * 21;
      const grossFundingBps = Math.abs(sevenDayFundingBps);
      const expectedEdgeBps = netFundingCarryEdgeBps(grossFundingBps);
      const riskScore = riskScoreFor(perp, grossFundingBps / 2);
      const liquidityScore = liquidityScoreFor([perp]);

      return buildSignal({
        kind: "funding_carry",
        assetPair: `${perp.base}/${perp.quote}`,
        venue: `${perp.venue} perp`,
        direction:
          fundingRate > 0 ? "short_perp_receive_funding" : "watch_only",
        confidence: clamp(0.5 + grossFundingBps / 320, 0.42, 0.88),
        expectedEdgeBps,
        liquidityScore,
        riskScore,
        timestamp: perp.timestamp,
        zscore: sevenDayFundingBps / FUNDING_SIGMA_BPS,
        spreadValue: fundingRate,
        expectedConvergenceHours: CONVERGENCE_HOURS.funding,
        eligibleForPaperTrading:
          fundingRate > 0 && expectedEdgeBps > 45 && riskScore < 76,
        explanation: `${perp.venue.toUpperCase()} ${perp.base} perpetual funding is ${round(
          fundingRate * 100,
          4,
        )}% per 8h (≈ ${round(
          sevenDayFundingBps,
          1,
        )} bps/7d); expected edge ${round(
          expectedEdgeBps,
          1,
        )} bps net of ~${PAIR_ENTRY_COST_BPS} bps hedge fees if rates persist.`,
      });
    });
}

function crossExchangeSignals(markets: MarketSnapshot[]): RelativeValueSignal[] {
  const spotGroups = new Map<string, MarketSnapshot[]>();
  markets
    .filter((market) => market.instrumentType === "spot")
    .forEach((market) => {
      const key = `${market.base}/${market.quote}`;
      spotGroups.set(key, [...(spotGroups.get(key) ?? []), market]);
    });

  const signals: RelativeValueSignal[] = [];

  for (const [pair, group] of spotGroups.entries()) {
    if (group.length < 2) continue;
    const sorted = [...group].sort((a, b) => a.price - b.price);
    const low = sorted[0];
    const high = sorted[sorted.length - 1];
    const premiumBps = ((high.price - low.price) / low.price) * 10_000;

    if (premiumBps < 15) continue;

    const liquidityScore = liquidityScoreFor([low, high]);
    const riskScore = clamp(28 + premiumBps / 2 + maxSpread([low, high]) * 2, 0, 100);
    const netEdgeBps = netCrossExchangeEdgeBps(premiumBps);

    signals.push(
      buildSignal({
        kind: "cross_exchange_premium",
        assetPair: pair,
        venue: `${low.venue} -> ${high.venue}`,
        direction:
          netEdgeBps > 0 ? "buy_low_venue_sell_high_venue" : "watch_only",
        confidence: clamp(0.42 + premiumBps / 180, 0.4, 0.78),
        expectedEdgeBps: netEdgeBps,
        liquidityScore,
        riskScore,
        timestamp: high.timestamp,
        zscore: premiumBps / XVENUE_SIGMA_BPS,
        spreadValue: premiumBps / 10_000,
        expectedConvergenceHours: CONVERGENCE_HOURS.xvenue,
        eligibleForPaperTrading:
          netEdgeBps > 15 && liquidityScore > 45 && riskScore < 70,
        explanation: `${pair} spot is ${round(
          premiumBps,
          1,
        )} bps higher on ${high.venue} than ${low.venue} (gross); ≈ ${round(
          netEdgeBps,
          1,
        )} bps net of ~${PAIR_ENTRY_COST_BPS + CROSS_VENUE_TRANSFER_BPS} bps fees + transfer. Verify withdrawal health before execution.`,
      }),
    );
  }

  return signals;
}

function btcEthRatioSignals(
  ratioHistory: PairSpreadPoint[],
  timestamp: string,
): RelativeValueSignal[] {
  if (ratioHistory.length < 6) return [];
  const ratios = ratioHistory.map((point) => point.firstPrice / point.secondPrice);
  const current = ratios[ratios.length - 1];
  const sample = ratios.slice(0, -1);
  const sampleMean = mean(sample);
  const score = zScore(current, sample);
  const expectedEdgeBps = Math.abs(score) * 32;
  const spreadValue = sampleMean !== 0 ? (current - sampleMean) / sampleMean : 0;
  const convergenceHours = meanReversionHalfLifeHours(ratios, HISTORY_PERIOD_HOURS, 24);
  const stationarity = assessSpreadStationarity(ratios);
  const direction = meanReversionDirection(score, 1.1, stationarity.stationary);

  return [
    buildSignal({
      kind: "btc_eth_ratio",
      assetPair: "BTC/ETH",
      venue: "cross-asset",
      direction,
      confidence: clamp(0.4 + Math.abs(score) / 4, 0.35, 0.75),
      expectedEdgeBps,
      liquidityScore: 86,
      riskScore: clamp(38 + Math.abs(score) * 8, 0, 100),
      timestamp,
      zscore: score,
      spreadValue,
      expectedConvergenceHours: convergenceHours,
      spreadStationary: stationarity.stationary,
      adfTestStatistic: stationarity.adf.testStatistic,
      eligibleForPaperTrading:
        stationarity.stationary && Math.abs(score) > 1.1,
      explanation: `BTC/ETH ratio z-score is ${round(score, 2)}. ${stationarity.note} Mean-reversion play is ${
        direction === "watch_only" ? "not active" : "active for paper research"
      }.`,
    }),
  ];
}

function pairSpreadSignals(
  history: PairSpreadPoint[],
  pair: string,
  timestamp: string,
): RelativeValueSignal[] {
  if (history.length < 6) return [];
  const spreads = history.map((point) => point.firstPrice / point.secondPrice);
  const current = spreads[spreads.length - 1];
  const sample = spreads.slice(0, -1);
  const sampleMean = mean(sample);
  const score = zScore(current, sample);
  const spreadValue = sampleMean !== 0 ? (current - sampleMean) / sampleMean : 0;
  const convergenceHours = meanReversionHalfLifeHours(spreads, HISTORY_PERIOD_HOURS, 12);
  const stationarity = assessSpreadStationarity(spreads);
  const direction = meanReversionDirection(score, 1.25, stationarity.stationary);

  return [
    buildSignal({
      kind: "pair_spread_zscore",
      assetPair: pair,
      venue: "cross-asset",
      direction,
      confidence: clamp(0.38 + Math.abs(score) / 4.5, 0.32, 0.72),
      expectedEdgeBps: Math.abs(score) * 28,
      liquidityScore: 64,
      riskScore: clamp(45 + Math.abs(score) * 9, 0, 100),
      timestamp,
      zscore: score,
      spreadValue,
      expectedConvergenceHours: convergenceHours,
      spreadStationary: stationarity.stationary,
      adfTestStatistic: stationarity.adf.testStatistic,
      eligibleForPaperTrading:
        stationarity.stationary && Math.abs(score) > 1.25,
      explanation: `${pair} normalized spread z-score is ${round(score, 2)}. ${stationarity.note}${
        pair === "ETH/SOL"
          ? " Use reduced size because SOL leg liquidity is thinner than BTC/ETH."
          : ""
      }`,
    }),
  ];
}

function stablecoinSignals(
  stablecoins: StablecoinSnapshot[],
): RelativeValueSignal[] {
  return stablecoins
    .filter((coin) => Math.abs(coin.pegDeviationBps) >= 20)
    .map((coin) => {
      const riskScore = clamp(50 + Math.abs(coin.pegDeviationBps), 0, 100);
      return buildSignal({
        kind: "stablecoin_depeg",
        assetPair: `${coin.asset}/USD`,
        venue: coin.venue,
        direction: "watch_only",
        confidence: clamp(0.42 + Math.abs(coin.pegDeviationBps) / 100, 0.4, 0.8),
        expectedEdgeBps: Math.abs(coin.pegDeviationBps),
        liquidityScore: clamp(coin.liquidityUsd / 1_000_000, 0, 100),
        riskScore,
        timestamp: coin.timestamp,
        zscore: coin.pegDeviationBps / PEG_SIGMA_BPS,
        spreadValue: coin.pegDeviationBps / 10_000,
        expectedConvergenceHours: CONVERGENCE_HOURS.peg,
        eligibleForPaperTrading: false,
        explanation: `${coin.asset} trades at $${coin.priceUsd.toFixed(
          4,
        )}, a ${round(
          coin.pegDeviationBps,
          1,
        )} bps peg deviation. Classified as watch-only until redemption and venue risk are verified.`,
      });
    });
}

function volatilityRegimeSignal(
  data: MarketDataBundle,
): RelativeValueSignal | null {
  const averageVol =
    data.markets.reduce((sum, market) => sum + market.volatility30d, 0) /
    data.markets.length;

  if (averageVol < 0.35) return null;

  return buildSignal({
    kind: "volatility_regime",
    assetPair: "portfolio",
    venue: "market-wide",
    direction: averageVol > 0.75 ? "risk_off" : "watch_only",
    confidence: clamp(averageVol, 0.35, 0.9),
    expectedEdgeBps: 0,
    liquidityScore: 100,
    riskScore: clamp(averageVol * 100, 0, 100),
    timestamp: data.generatedAt,
    zscore: 0,
    spreadValue: 0,
    expectedConvergenceHours: 1,
    eligibleForPaperTrading: false,
    explanation: `Average 30-day realized volatility proxy is ${round(
      averageVol * 100,
      1,
    )}%; use this as a portfolio sizing filter rather than a standalone trade.`,
  });
}

function assessSpreadStationarity(series: number[]) {
  const adf = augmentedDickeyFullerTest(series, STATIONARITY_SIGNIFICANCE);
  const stationary = adf.isStationary;
  const note = stationary
    ? `ADF(5%) t=${round(adf.testStatistic, 2)} rejects unit root (${adf.confidence}).`
    : series.length < 12
      ? `Only ${series.length} history points (need ≥12 for ADF); watch-only.`
      : `ADF(5%) t=${round(adf.testStatistic, 2)} does not reject unit root — mean-reversion thesis unsupported; watch-only.`;
  return { adf, stationary, note };
}

function meanReversionDirection(
  score: number,
  threshold: number,
  stationary: boolean,
): SignalDirection {
  if (!stationary) return "watch_only";
  if (score > threshold) return "short_first_long_second";
  if (score < -threshold) return "long_first_short_second";
  return "watch_only";
}

function bestSpotFor(
  perp: MarketSnapshot,
  markets: MarketSnapshot[],
): MarketSnapshot | undefined {
  return markets
    .filter(
      (market) =>
        market.instrumentType === "spot" &&
        market.base === perp.base &&
        market.quote === perp.quote,
    )
    .sort((a, b) => b.volume24hUsd - a.volume24hUsd)[0];
}

function buildSignal(
  input: Omit<RelativeValueSignal, "id" | "opportunityScore" | "eligibleForLiveTrading">,
): RelativeValueSignal {
  return {
    id: [
      input.kind,
      input.assetPair.replace("/", "-"),
      input.venue.replaceAll(" ", "-").replaceAll("/", "-"),
    ].join(":"),
    ...input,
    confidence: round(input.confidence, 3),
    expectedEdgeBps: round(input.expectedEdgeBps, 2),
    riskScore: round(input.riskScore, 1),
    liquidityScore: round(input.liquidityScore, 1),
    zscore: round(input.zscore ?? 0, 3),
    spreadValue: round(input.spreadValue ?? 0, 6),
    expectedConvergenceHours: round(input.expectedConvergenceHours ?? 1, 2),
    spreadStationary: input.spreadStationary,
    adfTestStatistic:
      input.adfTestStatistic === undefined
        ? undefined
        : round(input.adfTestStatistic, 3),
    opportunityScore: 0,
    eligibleForLiveTrading: false,
  };
}

function opportunityScore(signal: RelativeValueSignal): number {
  const edgeComponent = clamp(signal.expectedEdgeBps / 2, 0, 45);
  const confidenceComponent = signal.confidence * 30;
  const liquidityComponent = signal.liquidityScore * 0.25;
  const riskPenalty = signal.riskScore * 0.35;
  return round(
    clamp(edgeComponent + confidenceComponent + liquidityComponent - riskPenalty, 0, 100),
    1,
  );
}

function liquidityScoreFor(markets: MarketSnapshot[]): number {
  const depthUsd = markets.reduce(
    (sum, market) =>
      sum + market.orderBook.bidDepthUsd + market.orderBook.askDepthUsd,
    0,
  );
  const spreadPenalty = maxSpread(markets) * 2.5;
  return clamp(depthUsd / 1_000_000 - spreadPenalty, 0, 100);
}

function riskScoreFor(market: MarketSnapshot, dislocationBps: number): number {
  return clamp(
    24 +
      market.volatility30d * 38 +
      market.orderBook.spreadBps * 2 +
      dislocationBps / 6,
    0,
    100,
  );
}

function maxSpread(markets: MarketSnapshot[]): number {
  return Math.max(...markets.map((market) => market.orderBook.spreadBps));
}
