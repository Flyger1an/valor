import type {
  DataQualityReport,
  MarketDataBundle,
  MarketSnapshot,
  MarketRiskState,
  PaperEquityPoint,
  PaperPortfolio,
  PaperPosition,
  PaperRiskLimits,
  PaperTrade,
  RelativeValueSignal,
  RiskState,
  SystemTrustVerdict,
} from "@/lib/domain/types";
import { isBlockedByEdgePolicy } from "@/lib/edge/policy";
import { paperTrustBlockReason } from "@/lib/risk/system-trust";
import { round } from "@/lib/utils/math";

export const STARTING_CASH_USD = 100_000;
export const FEE_BPS = 8;

export const DEFAULT_PAPER_LIMITS: PaperRiskLimits = {
  maxPositionUsd: 12_500,
  maxPortfolioNotionalPct: 0.5,
  maxSignalRiskScore: 70,
  minLiquidityScore: 45,
  allowWhenRiskState: ["Green", "Yellow", "Red"],
  maxHoldingHours: 72,
};

export function simulatePaperPortfolio(input: {
  signals: RelativeValueSignal[];
  risk: MarketRiskState;
  startingCashUsd?: number;
  limits?: Partial<PaperRiskLimits>;
  dataQuality?: DataQualityReport;
  systemTrust?: SystemTrustVerdict;
  previousPortfolio?: PaperPortfolio;
  marketData?: MarketDataBundle;
  now?: Date;
}): PaperPortfolio {
  return stepPaperPortfolio(input);
}

export function stepPaperPortfolio(input: {
  signals: RelativeValueSignal[];
  risk: MarketRiskState;
  previousPortfolio?: PaperPortfolio;
  startingCashUsd?: number;
  limits?: Partial<PaperRiskLimits>;
  dataQuality?: DataQualityReport;
  systemTrust?: SystemTrustVerdict;
  marketData?: MarketDataBundle;
  now?: Date;
}): PaperPortfolio {
  const startingCashUsd =
    input.startingCashUsd ?? input.previousPortfolio?.cashUsd ?? STARTING_CASH_USD;
  const limits = {
    ...DEFAULT_PAPER_LIMITS,
    ...input.previousPortfolio?.riskLimits,
    ...input.limits,
  };
  const nowIso =
    input.now?.toISOString() ??
    input.signals[0]?.timestamp ??
    input.risk.updatedAt ??
    new Date().toISOString();
  const signalsById = new Map(input.signals.map((signal) => [signal.id, signal]));
  const positions: PaperPosition[] = [];
  const cycleTrades: PaperTrade[] = [];
  const rejectedSignals: PaperTrade[] = [];
  const closedSignalIds = new Set<string>();
  let usedNotional = 0;
  let cashUsd = input.previousPortfolio?.cashUsd ?? startingCashUsd;

  for (const position of input.previousPortfolio?.positions ?? []) {
    const signal = signalsById.get(position.signalId);
    const marked = markPosition(position, signal, nowIso, input.marketData);
    const closeReason = closeReasonForPosition({
      position: marked,
      signal,
      riskState: input.risk.state,
      limits,
      dataQuality: input.dataQuality,
      nowIso,
    });

    if (closeReason) {
      closedSignalIds.add(marked.signalId);
      cashUsd = round(cashUsd + marked.markPnlUsd, 2);
      cycleTrades.push({
        id: `paper-close-${marked.signalId}-${nowIso}`,
        signalId: marked.signalId,
        timestamp: nowIso,
        assetPair: marked.assetPair,
        venue: marked.venue,
        direction: marked.direction,
        notionalUsd: marked.notionalUsd,
        feesUsd: 0,
        status: "closed",
        reason: closeReason,
        realizedPnlUsd: marked.markPnlUsd,
        fundingUsd: marked.fundingAccruedUsd ?? 0,
        markPnlUsd: marked.markPnlUsd,
      });
      continue;
    }

    positions.push(marked);
    usedNotional += marked.notionalUsd;
    cycleTrades.push({
      id: `paper-hold-${marked.signalId}-${nowIso}`,
      signalId: marked.signalId,
      timestamp: nowIso,
      assetPair: marked.assetPair,
      venue: marked.venue,
      direction: marked.direction,
      notionalUsd: marked.notionalUsd,
      feesUsd: 0,
      status: "held",
      reason: signal
        ? marked.markSource === "market_price"
          ? "Position held and marked against current market reference price."
          : "Position held and marked against current signal edge."
        : "Position held without a refreshed matching signal.",
      fundingUsd: marked.fundingAccruedUsd ?? 0,
      markPnlUsd: marked.markPnlUsd,
    });
  }

  const openSignalIds = new Set(positions.map((position) => position.signalId));

  input.signals
    .filter((signal) => signal.expectedEdgeBps > 0)
    .slice(0, 8)
    .forEach((signal) => {
      if (openSignalIds.has(signal.id)) return;
      if (closedSignalIds.has(signal.id)) return;

      const rejection = rejectionReason(
        signal,
        input.risk.state,
        limits,
        usedNotional,
        startingCashUsd,
        input.dataQuality,
        input.systemTrust,
      );
      const notionalUsd = Math.min(
        limits.maxPositionUsd,
        startingCashUsd * 0.08,
        (signal.opportunityScore / 100) * limits.maxPositionUsd,
      );
      const timestamp = signal.timestamp;

      if (rejection) {
        rejectedSignals.push({
          id: `paper-reject-${signal.id}`,
          signalId: signal.id,
          timestamp,
          assetPair: signal.assetPair,
          venue: signal.venue,
          direction: signal.direction,
          notionalUsd: round(Math.max(notionalUsd, 0), 2),
          feesUsd: 0,
          status: "rejected",
          reason: rejection,
        });
        return;
      }

      const feesUsd = notionalUsd * (FEE_BPS / 10_000);
      const entryReference = marketReferenceForSignal(signal, input.marketData);
      const markPnlUsd = entryReference
        ? 0
        : notionalUsd * (signal.expectedEdgeBps / 10_000) * signal.confidence;
      cashUsd = round(cashUsd - feesUsd, 2);

      positions.push({
        id: `paper-pos-${signal.id}`,
        signalId: signal.id,
        signalKind: signal.kind,
        assetPair: signal.assetPair,
        venue: signal.venue,
        direction: signal.direction,
        notionalUsd: round(notionalUsd, 2),
        entryEdgeBps: signal.expectedEdgeBps,
        currentEdgeBps: signal.expectedEdgeBps,
        entryReferencePrice: entryReference?.value,
        currentReferencePrice: entryReference?.value,
        markSource: entryReference ? "market_price" : "edge_proxy",
        markPnlUsd: round(markPnlUsd - feesUsd, 2),
        fundingAccruedUsd: 0,
        feesPaidUsd: round(feesUsd, 2),
        openedAt: timestamp,
        lastMarkedAt: timestamp,
        holdingHours: 0,
      });

      cycleTrades.push({
        id: `paper-fill-${signal.id}`,
        signalId: signal.id,
        timestamp,
        assetPair: signal.assetPair,
        venue: signal.venue,
        direction: signal.direction,
        notionalUsd: round(notionalUsd, 2),
        feesUsd: round(feesUsd, 2),
        status: "filled",
        reason: "Signal passed paper-trading risk limits.",
        markPnlUsd: round(markPnlUsd - feesUsd, 2),
      });

      usedNotional += notionalUsd;
    });

  const markPnl = positions.reduce((sum, position) => sum + position.markPnlUsd, 0);
  const realizedPnl = cycleTrades.reduce(
    (sum, trade) => sum + (trade.realizedPnlUsd ?? 0),
    0,
  );
  const previousRealizedPnlUsd =
    input.previousPortfolio?.realizedPnlUsd ??
    (input.previousPortfolio?.trades ?? []).reduce(
      (sum, trade) => sum + (trade.realizedPnlUsd ?? 0),
      0,
    );
  const previousFeesPaidUsd =
    input.previousPortfolio?.feesPaidUsd ??
    (input.previousPortfolio?.trades ?? []).reduce(
      (sum, trade) => sum + trade.feesUsd,
      0,
    );
  const cycleFeesPaidUsd = cycleTrades.reduce(
    (sum, trade) => sum + trade.feesUsd,
    0,
  );
  const trades = [...cycleTrades, ...(input.previousPortfolio?.trades ?? [])].slice(
    0,
    100,
  );
  const rejected = [
    ...rejectedSignals,
    ...(input.previousPortfolio?.rejectedSignals ?? []),
  ].slice(0, 100);

  const equityUsd = round(cashUsd + markPnl, 2);
  // Real time-windowed PnL from an equity curve (cadence-independent) — replaces the old
  // dailyPnl = this-cycle-mark and weeklyPnl = daily × 2.4 fabrications. Keep ~8 days of samples so the
  // 24h / 7d windows are always covered; when history is younger than a window, it honestly reports the
  // change since inception.
  const nowMs = new Date(nowIso).getTime();
  const equityHistory: PaperEquityPoint[] = [
    ...(input.previousPortfolio?.equityHistory ?? []),
    { timestamp: nowIso, equityUsd },
  ]
    .filter((point) => nowMs - new Date(point.timestamp).getTime() <= 8 * 86_400_000)
    .slice(-1000);
  const equityAtWindowStart = (windowMs: number): number => {
    const cutoff = nowMs - windowMs;
    const start = equityHistory.find(
      (point) => new Date(point.timestamp).getTime() >= cutoff,
    );
    return start?.equityUsd ?? equityHistory[0]?.equityUsd ?? equityUsd;
  };

  return {
    cashUsd: round(cashUsd, 2),
    equityUsd,
    realizedPnlUsd: round(previousRealizedPnlUsd + realizedPnl, 2),
    feesPaidUsd: round(previousFeesPaidUsd + cycleFeesPaidUsd, 2),
    dailyPnlUsd: round(equityUsd - equityAtWindowStart(86_400_000), 2),
    weeklyPnlUsd: round(equityUsd - equityAtWindowStart(7 * 86_400_000), 2),
    positions,
    trades,
    rejectedSignals: rejected,
    riskLimits: limits,
    equityHistory,
  };
}

function markPosition(
  position: PaperPosition,
  signal: RelativeValueSignal | undefined,
  nowIso: string,
  marketData: MarketDataBundle | undefined,
): PaperPosition {
  const currentEdgeBps = signal?.expectedEdgeBps ?? position.currentEdgeBps ?? 0;
  const confidence = signal?.confidence ?? 0.35;
  const marketReference = signal
    ? marketReferenceForSignal(signal, marketData)
    : marketReferenceForPosition(position, marketData);
  const elapsedHours = Math.max(
    0,
    hoursBetween(position.lastMarkedAt ?? position.openedAt, nowIso),
  );
  const holdingHours = Math.max(0, hoursBetween(position.openedAt, nowIso));
  const fundingAccruedUsd = round(
    (position.fundingAccruedUsd ?? 0) + fundingAccrualUsd(position, signal, elapsedHours),
    2,
  );
  const feesPaidUsd = position.feesPaidUsd ?? 0;
  const marketPnlUsd =
    marketReference && position.entryReferencePrice !== undefined
      ? marketReferencePnlUsd({
          position,
          currentReference: marketReference,
          feesPaidUsd,
          fundingAccruedUsd,
        })
      : undefined;
  const markPnlUsd =
    marketPnlUsd ??
    round(
      position.notionalUsd * (currentEdgeBps / 10_000) * confidence -
        feesPaidUsd +
        fundingAccruedUsd,
      2,
    );

  return {
    ...position,
    signalKind: position.signalKind ?? signal?.kind,
    currentEdgeBps: round(currentEdgeBps, 2),
    currentReferencePrice: marketReference?.value ?? position.currentReferencePrice,
    markSource: marketPnlUsd !== undefined ? "market_price" : "edge_proxy",
    markPnlUsd,
    fundingAccruedUsd,
    feesPaidUsd,
    lastMarkedAt: nowIso,
    holdingHours: round(holdingHours, 2),
  };
}

function closeReasonForPosition(input: {
  position: PaperPosition;
  signal: RelativeValueSignal | undefined;
  riskState: RiskState;
  limits: PaperRiskLimits;
  dataQuality?: DataQualityReport;
  nowIso: string;
}): string | null {
  if (input.dataQuality?.blocksPaperTrading) {
    return `Closed because data quality ${input.dataQuality.status} blocks paper entries: ${input.dataQuality.summary}`;
  }

  if (!input.limits.allowWhenRiskState.includes(input.riskState)) {
    return `Closed because market risk state ${input.riskState} blocks paper trading.`;
  }

  if ((input.position.holdingHours ?? 0) >= input.limits.maxHoldingHours) {
    return `Closed after reaching max holding period of ${input.limits.maxHoldingHours} hours.`;
  }

  if (!input.signal) {
    return "Closed because the source signal disappeared from the refreshed signal set.";
  }

  if (isBlockedByEdgePolicy(input.signal)) {
    return `Closed because ${input.signal.edgePolicy!.reason}`;
  }

  if (input.signal.riskScore > input.limits.maxSignalRiskScore + 10) {
    return `Closed because signal risk score ${input.signal.riskScore} exceeded exit threshold.`;
  }

  if (
    input.signal.expectedEdgeBps <= Math.max(15, input.position.entryEdgeBps * 0.35)
  ) {
    return `Closed because edge decayed from ${input.position.entryEdgeBps.toFixed(
      1,
    )} bps to ${input.signal.expectedEdgeBps.toFixed(1)} bps.`;
  }

  return null;
}

function fundingAccrualUsd(
  position: PaperPosition,
  signal: RelativeValueSignal | undefined,
  elapsedHours: number,
): number {
  const kind = signal?.kind ?? position.signalKind;
  if (kind !== "funding_carry" && kind !== "spot_perp_basis") return 0;
  const currentEdgeBps = Math.max(signal?.expectedEdgeBps ?? position.currentEdgeBps ?? 0, 0);
  return position.notionalUsd * (currentEdgeBps / 10_000) * (Math.min(elapsedHours, 24) / 168);
}

interface MarketReference {
  value: number;
  unit: "price" | "ratio" | "spread_bps";
  directionMultiplier: 1 | -1;
}

function marketReferenceForPosition(
  position: PaperPosition,
  data: MarketDataBundle | undefined,
): MarketReference | undefined {
  if (!data || !position.signalKind) return undefined;
  return marketReferenceForSignal(
    {
      id: position.signalId,
      kind: position.signalKind,
      assetPair: position.assetPair,
      venue: position.venue,
      direction: position.direction,
      confidence: 0.35,
      expectedEdgeBps: position.currentEdgeBps ?? position.entryEdgeBps,
      riskScore: 0,
      liquidityScore: 0,
      opportunityScore: 0,
      eligibleForPaperTrading: true,
      eligibleForLiveTrading: false,
      timestamp: position.lastMarkedAt ?? position.openedAt,
      explanation: "Synthetic reference for paper mark.",
    },
    data,
  );
}

function marketReferenceForSignal(
  signal: RelativeValueSignal,
  data: MarketDataBundle | undefined,
): MarketReference | undefined {
  if (!data) return undefined;

  if (signal.kind === "spot_perp_basis") {
    const [base, quote] = parsePair(signal.assetPair);
    const spot = bestSpot(base, quote, data.markets, signal.venue);
    const perp = bestPerp(base, quote, data.markets, signal.venue);
    if (!spot || !perp) return undefined;
    const perpPrice = perp.markPrice ?? perp.price;
    return {
      value: ((perpPrice - spot.price) / spot.price) * 10_000,
      unit: "spread_bps",
      directionMultiplier: -1,
    };
  }

  if (signal.kind === "cross_exchange_premium") {
    const [base, quote] = parsePair(signal.assetPair);
    const spots = data.markets.filter(
      (market) =>
        market.instrumentType === "spot" &&
        market.base === base &&
        market.quote === quote,
    );
    if (spots.length < 2) return undefined;
    const [lowVenue, highVenue] = signal.venue.split("->").map((value) => value.trim());
    const low =
      spots.find((market) => market.venue === lowVenue) ??
      [...spots].sort((a, b) => a.price - b.price)[0];
    const high =
      spots.find((market) => market.venue === highVenue) ??
      [...spots].sort((a, b) => b.price - a.price)[0];
    return {
      value: ((high.price - low.price) / low.price) * 10_000,
      unit: "spread_bps",
      directionMultiplier: -1,
    };
  }

  if (
    signal.kind === "btc_eth_ratio" ||
    signal.kind === "pair_spread_zscore"
  ) {
    const [first, second] = parsePair(signal.assetPair);
    const firstPrice = bestUsdPrice(first, data.markets);
    const secondPrice = bestUsdPrice(second, data.markets);
    if (!firstPrice || !secondPrice) return undefined;
    return {
      value: firstPrice / secondPrice,
      unit: "ratio",
      directionMultiplier:
        signal.direction === "short_first_long_second" ? -1 : 1,
    };
  }

  const [base, quote] = parsePair(signal.assetPair);
  const market =
    signal.kind === "funding_carry"
      ? bestPerp(base, quote, data.markets, signal.venue)
      : bestSpot(base, quote, data.markets, signal.venue);
  if (!market) return undefined;

  return {
    value: market.markPrice ?? market.price,
    unit: "price",
    directionMultiplier:
      signal.direction === "short_perp_receive_funding" ||
      signal.direction === "short_first_long_second"
        ? -1
        : 1,
  };
}

function marketReferencePnlUsd(input: {
  position: PaperPosition;
  currentReference: MarketReference;
  feesPaidUsd: number;
  fundingAccruedUsd: number;
}): number | undefined {
  const entry = input.position.entryReferencePrice;
  if (entry === undefined || entry <= 0 || input.currentReference.value <= 0) {
    return undefined;
  }

  const rawReturn =
    input.currentReference.unit === "spread_bps"
      ? (input.currentReference.value - entry) / 10_000
      : (input.currentReference.value - entry) / entry;
  const directionalReturn = rawReturn * input.currentReference.directionMultiplier;

  return round(
    input.position.notionalUsd * directionalReturn -
      input.feesPaidUsd +
      input.fundingAccruedUsd,
    2,
  );
}

function parsePair(pair: string): [string, string] {
  const [base, quote] = pair.split("/");
  return [base ?? "", quote ?? "USD"];
}

function bestSpot(
  base: string,
  quote: string,
  markets: MarketSnapshot[],
  venueHint: string,
): MarketSnapshot | undefined {
  return bestMarket(base, quote, markets, venueHint, "spot");
}

function bestPerp(
  base: string,
  quote: string,
  markets: MarketSnapshot[],
  venueHint: string,
): MarketSnapshot | undefined {
  return bestMarket(base, quote, markets, venueHint, "perp");
}

function bestMarket(
  base: string,
  quote: string,
  markets: MarketSnapshot[],
  venueHint: string,
  instrumentType: MarketSnapshot["instrumentType"],
): MarketSnapshot | undefined {
  const matches = markets.filter(
    (market) =>
      market.instrumentType === instrumentType &&
      market.base === base &&
      market.quote === quote,
  );
  return (
    matches.find((market) => venueHint.includes(market.venue)) ??
    [...matches].sort((a, b) => b.volume24hUsd - a.volume24hUsd)[0]
  );
}

function bestUsdPrice(asset: string, markets: MarketSnapshot[]): number | undefined {
  const market = [...markets]
    .filter(
      (entry) =>
        entry.instrumentType === "spot" &&
        entry.base === asset &&
        entry.quote === "USD",
    )
    .sort((a, b) => b.volume24hUsd - a.volume24hUsd)[0];
  return market?.price;
}

function hoursBetween(earlierIso: string, laterIso: string): number {
  return (new Date(laterIso).getTime() - new Date(earlierIso).getTime()) / 3_600_000;
}

function rejectionReason(
  signal: RelativeValueSignal,
  riskState: RiskState,
  limits: PaperRiskLimits,
  usedNotional: number,
  startingCashUsd: number,
  dataQuality?: DataQualityReport,
  systemTrust?: SystemTrustVerdict,
): string | null {
  const systemTrustBlock = paperTrustBlockReason(systemTrust);
  if (systemTrustBlock) return systemTrustBlock;

  if (dataQuality?.blocksPaperTrading) {
    return `Data quality ${dataQuality.status} blocks new paper trades: ${dataQuality.summary}`;
  }

  if (!limits.allowWhenRiskState.includes(riskState)) {
    return `Market risk state ${riskState} blocks new paper trades.`;
  }

  if (!signal.eligibleForPaperTrading) {
    return "Signal is marked ineligible for paper trading.";
  }

  if (signal.riskScore > limits.maxSignalRiskScore) {
    return `Signal risk score ${signal.riskScore} exceeds limit ${limits.maxSignalRiskScore}.`;
  }

  if (signal.liquidityScore < limits.minLiquidityScore) {
    return `Liquidity score ${signal.liquidityScore} is below limit ${limits.minLiquidityScore}.`;
  }

  if (usedNotional >= startingCashUsd * limits.maxPortfolioNotionalPct) {
    return "Portfolio notional limit reached.";
  }

  return null;
}
