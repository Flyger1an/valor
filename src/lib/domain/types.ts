export type Venue =
  | "binance"
  | "coinbase"
  | "kraken"
  | "okx"
  | "deribit"
  | "uniswap"
  | "manual";

export type Asset =
  | "BTC"
  | "ETH"
  | "SOL"
  | "USDC"
  | "USDT"
  | "DAI"
  | "USD";

export type RiskState = "Green" | "Yellow" | "Red" | "Black";

export type SignalKind =
  | "spot_perp_basis"
  | "funding_carry"
  | "cross_exchange_premium"
  | "btc_eth_ratio"
  | "stablecoin_depeg"
  | "pair_spread_zscore"
  | "volatility_regime";

export type SignalDirection =
  | "long_spot_short_perp"
  | "short_perp_receive_funding"
  | "buy_low_venue_sell_high_venue"
  | "long_first_short_second"
  | "short_first_long_second"
  | "watch_only"
  | "risk_off";

export interface OrderBookSummary {
  bid: number;
  ask: number;
  bidDepthUsd: number;
  askDepthUsd: number;
  spreadBps: number;
}

export interface MarketSnapshot {
  id: string;
  venue: Venue;
  base: Asset;
  quote: Asset;
  instrumentType: "spot" | "perp";
  price: number;
  markPrice?: number;
  indexPrice?: number;
  fundingRate8h?: number;
  openInterestUsd?: number;
  volume24hUsd: number;
  volatility30d: number;
  change24hPct: number;
  timestamp: string;
  orderBook: OrderBookSummary;
}

export interface StablecoinSnapshot {
  asset: Extract<Asset, "USDC" | "USDT" | "DAI">;
  venue: Venue;
  priceUsd: number;
  liquidityUsd: number;
  pegDeviationBps: number;
  timestamp: string;
}

export interface ExchangeHealthSignal {
  venue: Venue;
  withdrawals: "normal" | "delayed" | "paused";
  reserveStatus: "reported" | "stale" | "unavailable" | "flagged";
  message: string;
  updatedAt: string;
}

export interface ChainFeeSnapshot {
  chain: "bitcoin" | "ethereum";
  feeMetric: string;
  value: number;
  normalRangeHigh: number;
  timestamp: string;
}

export interface SecurityAdvisory {
  id: string;
  severity: "info" | "low" | "medium" | "high" | "critical";
  source: string;
  title: string;
  summary: string;
  affectedVenues: Venue[];
  affectedAssets: Asset[];
  publishedAt: string;
}

export interface EtfProxySnapshot {
  symbol: string;
  name: string;
  price: number;
  change24hPct: number;
  timestamp: string;
}

export interface HistoricalPoint {
  timestamp: string;
  spotPrice: number;
  perpPrice: number;
  fundingRate8h: number;
  volumeUsd: number;
  volatility30d: number;
}

export interface PairSpreadPoint {
  timestamp: string;
  firstPrice: number;
  secondPrice: number;
}

export interface MarketDataBundle {
  generatedAt: string;
  markets: MarketSnapshot[];
  stablecoins: StablecoinSnapshot[];
  exchangeHealth: ExchangeHealthSignal[];
  chainFees: ChainFeeSnapshot[];
  advisories: SecurityAdvisory[];
  etfProxies: EtfProxySnapshot[];
  btcEthRatioHistory: PairSpreadPoint[];
  ethSolSpreadHistory: PairSpreadPoint[];
  backtestHistory: HistoricalPoint[];
}

export interface RelativeValueSignal {
  id: string;
  kind: SignalKind;
  assetPair: string;
  venue: string;
  direction: SignalDirection;
  confidence: number;
  expectedEdgeBps: number;
  riskScore: number;
  liquidityScore: number;
  opportunityScore: number;
  explanation: string;
  timestamp: string;
  eligibleForPaperTrading: boolean;
  eligibleForLiveTrading: boolean;
}

export interface TradingRestriction {
  code: string;
  description: string;
  severity: RiskState;
}

export interface RiskAlert {
  id: string;
  severity: "info" | "low" | "medium" | "high" | "critical";
  category:
    | "exchange"
    | "stablecoin"
    | "security"
    | "liquidity"
    | "funding"
    | "dislocation"
    | "chain";
  title: string;
  explanation: string;
  source: string;
  timestamp: string;
  restrictions: TradingRestriction[];
}

export interface MarketRiskState {
  state: RiskState;
  score: number;
  explanation: string;
  activeAlerts: RiskAlert[];
  tradingRestrictions: TradingRestriction[];
  updatedAt: string;
}

export interface BacktestTrade {
  id: string;
  timestamp: string;
  action: "enter" | "exit" | "hold";
  side: "basis_short_perp" | "flat";
  notionalUsd: number;
  feesUsd: number;
  slippageUsd: number;
  fundingUsd: number;
  realizedPnlUsd: number;
  reason: string;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
  drawdownPct: number;
}

export interface BacktestReport {
  strategyName: string;
  startedAt: string;
  endedAt: string;
  startingCashUsd: number;
  endingEquityUsd: number;
  totalReturnPct: number;
  maxDrawdownPct: number;
  sharpe: number;
  sortino: number;
  winRatePct: number;
  exposureAvgPct: number;
  turnoverUsd: number;
  totalFeesUsd: number;
  trades: BacktestTrade[];
  equityCurve: EquityPoint[];
  assumptions: string[];
}

export interface PaperPosition {
  id: string;
  signalId: string;
  assetPair: string;
  venue: string;
  direction: SignalDirection;
  notionalUsd: number;
  entryEdgeBps: number;
  markPnlUsd: number;
  openedAt: string;
}

export interface PaperTrade {
  id: string;
  signalId: string;
  timestamp: string;
  assetPair: string;
  venue: string;
  direction: SignalDirection;
  notionalUsd: number;
  feesUsd: number;
  status: "filled" | "rejected";
  reason: string;
}

export interface PaperPortfolio {
  cashUsd: number;
  equityUsd: number;
  dailyPnlUsd: number;
  weeklyPnlUsd: number;
  positions: PaperPosition[];
  trades: PaperTrade[];
  rejectedSignals: PaperTrade[];
  riskLimits: PaperRiskLimits;
}

export interface PaperRiskLimits {
  maxPositionUsd: number;
  maxPortfolioNotionalPct: number;
  maxSignalRiskScore: number;
  minLiquidityScore: number;
  allowWhenRiskState: RiskState[];
}

export interface LiveTradingSettings {
  enabled: boolean;
  dryRun: boolean;
  manualConfirmationRequired: boolean;
  killSwitchActive: boolean;
  maxTradeUsd: number;
  dailyLossLimitUsd: number;
  maxLeverage: number;
  venueAllowlist: Venue[];
  assetAllowlist: Asset[];
}

export interface LiveTradeEvaluation {
  allowed: boolean;
  dryRun: boolean;
  reasons: string[];
  auditLabel: string;
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  actor: "system" | "user" | "paper_broker" | "live_guard";
  action:
    | "data.refresh"
    | "signal.generated"
    | "risk.alert"
    | "backtest.run"
    | "paper.trade"
    | "live.trade_attempt"
    | "settings.change"
    | "kill_switch.activated";
  summary: string;
  metadata: Record<string, string | number | boolean>;
}
