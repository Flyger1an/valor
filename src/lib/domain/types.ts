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

export type MarketDataMode = "sample" | "coingecko" | "public";

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
  relativeValueHistorySource?: "live-klines" | "fixture";
}

export interface DataQualityIssue {
  code: string;
  severity: "info" | "warning" | "critical";
  scope: string;
  message: string;
}

export interface DataQualityReport {
  connectorId: string;
  connectorLabel: string;
  mode: MarketDataMode;
  status: "healthy" | "degraded" | "blocked";
  generatedAt: string;
  assessedAt: string;
  dataAgeMinutes: number;
  marketCount: number;
  issueCount: number;
  criticalIssueCount: number;
  fallbackUsed: boolean;
  fixtureBacked: boolean;
  blocksPaperTrading: boolean;
  summary: string;
  issues: DataQualityIssue[];
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
  zscore?: number;
  spreadValue?: number;
  expectedConvergenceHours?: number;
  spreadStationary?: boolean;
  adfTestStatistic?: number;
  edgePolicy?: {
    action: "watch_only";
    source: "edge_scoreboard";
    reason: string;
  };
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
  sortino: number | null; // null when there is no downside deviation (Sortino undefined) — never a magic sentinel
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
  signalKind?: SignalKind;
  assetPair: string;
  venue: string;
  direction: SignalDirection;
  notionalUsd: number;
  entryEdgeBps: number;
  currentEdgeBps?: number;
  entryReferencePrice?: number;
  currentReferencePrice?: number;
  markSource?: "market_price" | "edge_proxy";
  markPnlUsd: number;
  fundingAccruedUsd?: number;
  feesPaidUsd?: number;
  openedAt: string;
  lastMarkedAt?: string;
  holdingHours?: number;
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
  status: "filled" | "held" | "closed" | "rejected";
  reason: string;
  realizedPnlUsd?: number;
  fundingUsd?: number;
  markPnlUsd?: number;
}

export interface PaperEquityPoint {
  timestamp: string;
  equityUsd: number;
}

export interface PaperPortfolio {
  cashUsd: number;
  equityUsd: number;
  realizedPnlUsd: number;
  feesPaidUsd: number;
  dailyPnlUsd: number;
  weeklyPnlUsd: number;
  positions: PaperPosition[];
  trades: PaperTrade[];
  rejectedSignals: PaperTrade[];
  riskLimits: PaperRiskLimits;
  // timestamped equity samples so daily/weekly PnL are REAL time-windows (cadence-independent), not extrapolations
  equityHistory?: PaperEquityPoint[];
}

export interface PaperRiskLimits {
  maxPositionUsd: number;
  maxPortfolioNotionalPct: number;
  maxSignalRiskScore: number;
  minLiquidityScore: number;
  allowWhenRiskState: RiskState[];
  maxHoldingHours: number;
}

export type EdgeScoreboardStatus =
  | "proving"
  | "watch"
  | "underperforming"
  | "insufficient";

export interface EdgeScoreboardRow {
  kind: SignalKind;
  generatedCount: number;
  paperEligibleCount: number;
  openPositionCount: number;
  activeNotionalUsd: number;
  ledgerEventCount: number;
  filledCount: number;
  heldCount: number;
  closedCount: number;
  rejectedCount: number;
  averageExpectedEdgeBps: number;
  averageSignalRiskScore: number;
  markPnlUsd: number;
  realizedPnlUsd: number;
  totalPnlUsd: number;
  fundingUsd: number;
  winRatePct: number;
  acceptanceRatePct: number;
  averageHoldingHours: number;
  averageEdgeDecayBps: number;
  status: EdgeScoreboardStatus;
  recommendation: string;
}

export interface EdgeScoreboard {
  updatedAt: string;
  rows: EdgeScoreboardRow[];
  totals: {
    generatedCount: number;
    paperEligibleCount: number;
    openPositionCount: number;
    ledgerEventCount: number;
    totalPnlUsd: number;
    realizedPnlUsd: number;
    markPnlUsd: number;
    underperformingCount: number;
  };
}

export type SystemTrustStatus = "trusted" | "caution" | "blocked";

export interface SystemTrustIssue {
  code: string;
  severity: "info" | "warning" | "critical";
  scope: string;
  message: string;
  blocksPaperTrading: boolean;
  blocksLiveTrading: boolean;
}

export interface SystemTrustVerdict {
  status: SystemTrustStatus;
  generatedAt: string;
  summary: string;
  blocksPaperTrading: boolean;
  blocksLiveTrading: boolean;
  issueCount: number;
  criticalIssueCount: number;
  issues: SystemTrustIssue[];
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

export type ExecutionMode = "dry_run";
export type ExecutionOrderSide = "buy" | "sell" | "spread";
export type ExecutionOrderStatus =
  | "previewed"
  | "dry_run_recorded"
  | "blocked"
  | "cancelled";

export interface ExecutionBalance {
  venue: string;
  asset: Asset;
  available: number;
  reserved: number;
  mode: ExecutionMode;
  updatedAt: string;
}

export interface ExecutionOrderPreview {
  id: string;
  mode: ExecutionMode;
  signalId: string;
  signalKind: SignalKind;
  assetPair: string;
  venue: string;
  direction: SignalDirection;
  side: ExecutionOrderSide;
  requestedNotionalUsd: number;
  estimatedFeesUsd: number;
  estimatedSlippageUsd: number;
  estimatedTotalCostUsd: number;
  createdAt: string;
  notes: string[];
}

export interface ExecutionFill {
  id: string;
  orderIntentId: string;
  mode: ExecutionMode;
  status: "dry_run";
  assetPair: string;
  venue: string;
  notionalUsd: number;
  price: number;
  feesUsd: number;
  createdAt: string;
}

export interface LiveTradeAttempt {
  id: string;
  mode: ExecutionMode;
  signalId: string;
  signalKind: SignalKind;
  assetPair: string;
  venue: string;
  direction: SignalDirection;
  requestedNotionalUsd: number;
  allowed: boolean;
  dryRun: boolean;
  status: ExecutionOrderStatus;
  reasons: string[];
  evaluationAuditLabel: string;
  preview: ExecutionOrderPreview;
  fills: ExecutionFill[];
  createdAt: string;
}

export type ExecutionReconciliationStatus = "clean" | "attention" | "blocked";

export interface ExecutionReconciliationIssue {
  code: string;
  severity: "info" | "warning" | "critical";
  scope: string;
  message: string;
}

export interface ExecutionReconciliationReport {
  id: string;
  mode: ExecutionMode;
  generatedAt: string;
  status: ExecutionReconciliationStatus;
  attemptCount: number;
  allowedCount: number;
  blockedCount: number;
  dryRunFillCount: number;
  totalNotionalUsd: number;
  totalEstimatedCostUsd: number;
  issueCount: number;
  criticalIssueCount: number;
  issues: ExecutionReconciliationIssue[];
}

export type OperationalRunbookStatus = "ready" | "attention" | "blocked";
export type OperationalRunbookArea =
  | "stop_resume"
  | "data"
  | "alerts"
  | "paper"
  | "execution"
  | "scheduler";

export interface OperationalRunbookStep {
  id: string;
  area: OperationalRunbookArea;
  title: string;
  status: "ready" | "action_required" | "blocked";
  severity: "info" | "warning" | "critical";
  trigger: string;
  action: string;
  verification: string;
  evidence: string;
  blocksPaperTrading: boolean;
  blocksLiveTrading: boolean;
}

export interface OperationalRunbookReport {
  id: string;
  generatedAt: string;
  status: OperationalRunbookStatus;
  summary: string;
  stepCount: number;
  actionRequiredCount: number;
  blockedCount: number;
  criticalStepCount: number;
  steps: OperationalRunbookStep[];
}

export type TinyLiveReadinessStatus =
  | "no_go"
  | "watchlist"
  | "candidate_review";

export interface TinyLiveReadinessBlocker {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
  evidence: string;
}

export type EvolverEvidenceStatus =
  | "not_configured"
  | "empty"
  | "healthy"
  | "watch"
  | "blocked";

export interface EvolverEvidenceIssue {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
  evidence: string;
}

export interface EvolverRecoveryAction {
  code: string;
  severity: "info" | "warning" | "critical";
  title: string;
  current: string;
  target: string;
  gap: string;
  rationale: string;
}

export interface EvolverRecoveryPlan {
  status: "not_configured" | "empty" | "blocked" | "watch" | "clear";
  summary: string;
  minimumEvidenceDays: number;
  additionalEvidenceDays: number;
  minimumClosedTrades: number;
  additionalClosedTrades: number;
  minimumWinRatePct: number;
  winRateGapPct: number;
  minimumConvergenceRatePct: number;
  convergenceRateGapPct: number;
  requiredPnlRecoveryUsd: number;
  confidenceHaircutPct?: number;
  benchCandidates: string[];
  actions: EvolverRecoveryAction[];
}

export type EvolverRecoveryTrendPosture =
  | "unavailable"
  | "new"
  | "improving"
  | "flat"
  | "deteriorating"
  | "clear";

export interface EvolverRecoverySnapshot {
  id: string;
  generatedAt: string;
  sourceLabel: string;
  evidenceStatus: EvolverEvidenceStatus;
  recoveryStatus: EvolverRecoveryPlan["status"];
  requiredPnlRecoveryUsd: number;
  additionalEvidenceDays: number;
  additionalClosedTrades: number;
  winRateGapPct: number;
  convergenceRateGapPct: number;
  confidenceHaircutPct?: number;
  gapScore: number;
  benchCandidates: string[];
  actionCodes: string[];
  signature: string;
}

export interface EvolverRecoveryMetricTrend {
  key:
    | "gap_score"
    | "required_pnl_recovery_usd"
    | "additional_evidence_days"
    | "additional_closed_trades"
    | "win_rate_gap_pct"
    | "convergence_rate_gap_pct"
    | "confidence_haircut_pct";
  label: string;
  unit: "score" | "usd" | "days" | "trades" | "pp" | "pct";
  current: number;
  previous?: number;
  delta?: number;
  direction: "new" | "improved" | "flat" | "deteriorated";
}

export interface EvolverBenchGuardReport {
  active: boolean;
  benchedCandidates: string[];
  matchingSignalKinds: SignalKind[];
  summary: string;
}

export interface EvolverRecoveryWatchdogReport {
  id: string;
  generatedAt: string;
  posture: EvolverRecoveryTrendPosture;
  summary: string;
  snapshotCount: number;
  current?: EvolverRecoverySnapshot;
  previous?: EvolverRecoverySnapshot;
  metrics: EvolverRecoveryMetricTrend[];
  benchGuard: EvolverBenchGuardReport;
}

export interface EvolverResearchLoopSummary {
  name: string;
  cycleCount: number;
  surfacedCount: number;
  firstTimestamp?: string;
  lastTimestamp?: string;
  lastSummary?: string;
  lastSurfacedSummary?: string;
  familyCounts: Array<{
    family: string;
    count: number;
  }>;
}

export interface EvolverShadowSummary {
  eventCount: number;
  closedTradeCount: number;
  openPositionCount: number;
  equityUsd?: number;
  startingEquityUsd: number;
  reportedPnlUsd?: number;
  approximatedClosedPnlUsd: number;
  approximatedSimPnlUsd: number;
  winRatePct: number;
  convergenceRatePct: number;
  averageShadowPnlPct: number;
  medianShadowPnlPct: number;
  minimumShadowPnlPct: number;
  maximumShadowPnlPct: number;
  lastClosedAt?: string;
}

export interface EvolverCalibrationSummary {
  sampleSize: number;
  statedConfidenceMean?: number;
  realizedConvergenceRate?: number;
  meanDivergencePct?: number;
  convergenceScale?: number;
  version?: string;
  updatedAt?: string;
  status: "unknown" | "calibrated" | "overconfident";
}

export interface EvolverEvidenceReport {
  id: string;
  generatedAt: string;
  status: EvolverEvidenceStatus;
  configured: boolean;
  sourceLabel: string;
  summary: string;
  evidenceDays: number;
  firstTimestamp?: string;
  lastTimestamp?: string;
  totalResearchCycles: number;
  surfacedCandidateCount: number;
  shadow?: EvolverShadowSummary;
  calibration?: EvolverCalibrationSummary;
  researchLoops: EvolverResearchLoopSummary[];
  issues: EvolverEvidenceIssue[];
  recoveryPlan: EvolverRecoveryPlan;
}

export interface TinyLiveReadinessCandidate {
  kind: SignalKind;
  status: EdgeScoreboardStatus;
  closedCount: number;
  totalPnlUsd: number;
  winRatePct: number;
  acceptanceRatePct: number;
  averageExpectedEdgeBps: number;
  recommendation: string;
}

export interface TinyLiveReadinessReport {
  id: string;
  generatedAt: string;
  status: TinyLiveReadinessStatus;
  summary: string;
  candidate?: TinyLiveReadinessCandidate;
  evidenceDays: number;
  closedTradeCount: number;
  blockerCount: number;
  criticalBlockerCount: number;
  blockers: TinyLiveReadinessBlocker[];
  minimums: {
    evidenceDays: number;
    closedTradesPerFamily: number;
    winRatePct: number;
    totalPnlUsd: number;
  };
  memo: {
    conclusion: string;
    evidenceWindow: string;
    requiredNextEvidence: string;
  };
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
