import { readFileSync, statSync } from "node:fs";

type Json = Record<string, unknown>;
export type ExperimentBook = {
  name: "baseline" | "kelly" | "henry";
  policyVersion: string;
  equity: number;
  cash: number;
  feeEscrow: number;
  netPnl: number;
  realizedPnl: number;
  unrealizedPnl: number;
  fees: number;
  exposure: number;
  turnover: number;
  drawdown: number;
  closedTrades: number;
  skips: number;
  skipReasons: string[];
  safetyEvents: string[];
  halt: string;
  dailyHalt: boolean;
  provisionalFees: boolean;
  afterOperatingEstimate?: number;
  sizing: { symbol: string; raw: number; fractional: number; capped: number; reason: string }[];
};
export type ExperimentStatus = {
  status: "unconfigured" | "unavailable" | "stale" | "current";
  epoch?: string;
  end?: string;
  timestamp?: string;
  source?: string;
  symbols?: string[];
  quoteStatus?: { symbol: string; fresh: boolean; ageSeconds?: number }[];
  halt?: string;
  strategy?: string;
  evidenceBlocks?: number;
  captureFresh?: boolean;
  marksFresh?: boolean;
  frames?: number;
  evidencePolicy?: string;
  evidenceQuality?: {
    day: string;
    completeSoFar: boolean;
    invalidReasons: string[];
    firstFullDay?: string;
    observations: number;
    freshPairFraction?: number;
    staleByAsset: { symbol: string; count: number; heldCount: number; maximumAge: number }[];
    gapsOver60: number;
    maximumGap: number;
    valuationKnown: boolean;
    legacyBlocks: number;
    missingDays: number;
  };
  sharedOperatingEstimate?: number;
  actualSharedCost?: number;
  books?: ExperimentBook[];
};

function amount(value: unknown): number {
  if ((typeof value !== "number" && typeof value !== "string") || String(value).trim() === "") {
    throw new Error("invalid amount");
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new Error("nonfinite amount");
  return parsed;
}
const optional = (v: unknown) => v === null || v === undefined ? undefined : amount(v);
const words = (v: unknown) => String(v ?? "").slice(0, 200).replaceAll("_", " ");
const count = (v: unknown) => {
  const n = amount(v);
  if (!Number.isSafeInteger(n) || n < 0) throw new Error("invalid count");
  return n;
};

function evidenceQuality(raw: Json): ExperimentStatus["evidenceQuality"] {
  const value = raw.evidence_quality as Json | undefined;
  if (!value?.coverage) return undefined;
  const coverage = value.coverage as Json;
  const fraction = optional(value.fresh_pair_observation_fraction);
  if (fraction !== undefined && (fraction < 0 || fraction > 1)) throw new Error("invalid coverage fraction");
  const first = optional(value.first_full_utc_day_start);
  const stale = coverage.stale_by_asset as Json, held = coverage.held_stale_by_asset as Json;
  const ages = coverage.maximum_quote_age_seconds as Json;
  const observations = count(coverage.observations);
  const symbols = Object.keys(stale).sort();
  if (!symbols.length || symbols.length > 64 || symbols.some(s => !/^[A-Z0-9]+-USD$/.test(s))) throw new Error("invalid coverage universe");
  const staleByAsset = symbols.map(symbol => ({
    symbol, count: count(stale[symbol]), heldCount: count(held[symbol]), maximumAge: amount(ages[symbol]),
  }));
  if (staleByAsset.some(s => s.count > observations || s.heldCount > s.count || s.maximumAge < 0)) {
    throw new Error("inconsistent quote coverage");
  }
  return { day: words(value.day), completeSoFar: value.complete_so_far === true,
    invalidReasons: Array.isArray(value.invalid_reasons) ? value.invalid_reasons.map(words) : [],
    firstFullDay: first === undefined ? undefined : new Date(first * 1000).toISOString(),
    observations, freshPairFraction: fraction, staleByAsset,
    gapsOver60: count(coverage.gaps_over_60_seconds), maximumGap: amount(coverage.max_gap_seconds),
    valuationKnown: (value.baseline_valuation_now as Json)?.valid === true,
    legacyBlocks: count(value.legacy_blocks_retained), missingDays: count(value.unobserved_full_days),
  };
}

export function loadExperimentStatus(path = process.env.VALOR_EXPERIMENT_SNAPSHOT_PATH, now = Date.now()): ExperimentStatus {
  if (!path) return { status: "unconfigured" };
  try {
    if (statSync(path).size > 1_000_000) throw new Error("oversized snapshot");
    return parseExperimentStatus(JSON.parse(readFileSync(path, "utf8")), now);
  } catch {
    return { status: "unavailable" };
  }
}

/** Also used by the private dashboard after checking the pinned study identity. */
export function parseExperimentStatus(value: unknown, now = Date.now()): ExperimentStatus {
  try {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid snapshot");
    const raw = value as Json;
    if (raw.schema_version !== 1 || raw.mode !== "virtual_only" || !Array.isArray(raw.books) || raw.books.length !== 3 ||
        !/^[a-f0-9]{64}$/.test(String(raw.identity_hash)) || raw.incremental_model_api_calls !== 0) {
      throw new Error("not an isolated virtual experiment");
    }
    const epoch = amount(raw.epoch) * 1000, timestamp = amount(raw.timestamp) * 1000, end = amount(raw.evaluation_end) * 1000;
    if (timestamp < epoch || end !== epoch + 90 * 86400_000) throw new Error("invalid common epoch");
    const books = raw.books.map((b: Json): ExperimentBook => {
      if (!["baseline", "kelly", "henry"].includes(String(b.name)) || amount(b.starting_cash) !== 500) throw new Error("invalid book");
      const equity = amount(b.equity), cash = amount(b.cash), realizedPnl = amount(b.realized_pnl), unrealizedPnl = amount(b.unrealized_pnl);
      if (cash < 0 || equity < 0 || Math.abs(equity - 500 - realizedPnl - unrealizedPnl) > 0.00001 ||
          Math.abs(amount(b.net_pnl) - equity + 500) > 0.00001) throw new Error("unbalanced book");
      const skips = b.skips as Json;
      return {
        name: b.name as ExperimentBook["name"], policyVersion: String(b.policy_version).slice(0, 100),
        equity, cash, feeEscrow: amount(b.fee_escrow), netPnl: amount(b.net_pnl), realizedPnl, unrealizedPnl,
        fees: amount(b.fees), exposure: amount(b.exposure), turnover: amount(b.turnover), drawdown: amount(b.drawdown),
        closedTrades: amount(b.closed_trades), skips: Object.values(skips).reduce<number>((sum, v) => sum + amount(v), 0),
        skipReasons: Object.keys(skips).map(words), halt: words(b.halt), dailyHalt: b.daily_halt === true,
        provisionalFees: b.provisional_fees === true, afterOperatingEstimate: optional(b.after_allocated_operating_estimate),
        safetyEvents: Array.isArray(b.safety_events) ? (b.safety_events as Json[]).slice(-5).map(v => words(v.reason)) : [],
        sizing: Object.entries((b.sizing ?? {}) as Record<string, Json>).map(([symbol, value]) => ({
          symbol, raw: amount(value.raw_notional), fractional: amount(value.fractional_notional),
          capped: amount(value.capped_notional), reason: words(value.reason),
        })),
      };
    });
    if (new Set(books.map(b => b.name)).size !== 3) throw new Error("duplicate book");
    const captureFresh = now >= timestamp && now - timestamp <= 30_000;
    const symbols = raw.symbols === undefined ? ["BTC-USD", "ETH-USD"] : raw.symbols;
    if (!Array.isArray(symbols) || !symbols.length || symbols.length > 64 || new Set(symbols).size !== symbols.length ||
        symbols.some(s => typeof s !== "string" || !/^[A-Z0-9]+-USD$/.test(s))) throw new Error("invalid symbol universe");
    const quoteStatus = raw.quote_status === undefined ? undefined : symbols.map(symbol => {
      const q = (raw.quote_status as Record<string, Json>)[symbol];
      if (!q || typeof q.fresh !== "boolean") throw new Error("missing symbol freshness");
      const ageSeconds = optional(q.age_seconds);
      if (q.fresh && (ageSeconds === undefined || ageSeconds < 0 || ageSeconds > 30)) throw new Error("invalid symbol freshness");
      return {symbol, fresh: q.fresh, ageSeconds};
    });
    return {
      status: raw.marks_fresh === true && captureFresh ? "current" : "stale",
      captureFresh, marksFresh: raw.marks_fresh === true,
      symbols, quoteStatus,
      frames: raw.frames === undefined ? undefined : count(raw.frames),
      evidencePolicy: (raw.active_evidence_policy as Json)?.version === undefined ? undefined :
        String((raw.active_evidence_policy as Json).version).slice(0, 100),
      evidenceQuality: evidenceQuality(raw),
      epoch: new Date(epoch).toISOString(), end: new Date(end).toISOString(), timestamp: new Date(timestamp).toISOString(),
      source: String(raw.market_source), halt: words(raw.halt), strategy: String(raw.strategy),
      evidenceBlocks: amount(raw.usable_evidence_blocks), sharedOperatingEstimate: optional(raw.shared_operating_estimate),
      actualSharedCost: optional(raw.actual_shared_operating_cost),
      books: ["baseline", "kelly", "henry"].map(name => books.find(b => b.name === name)!),
    };
  } catch {
    return { status: "unavailable" };
  }
}
