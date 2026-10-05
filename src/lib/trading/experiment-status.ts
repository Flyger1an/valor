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
  halt?: string;
  strategy?: string;
  evidenceBlocks?: number;
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

export function loadExperimentStatus(path = process.env.VALOR_EXPERIMENT_SNAPSHOT_PATH, now = Date.now()): ExperimentStatus {
  if (!path) return { status: "unconfigured" };
  try {
    if (statSync(path).size > 1_000_000) throw new Error("oversized snapshot");
    const raw = JSON.parse(readFileSync(path, "utf8")) as Json;
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
    return {
      status: raw.marks_fresh === true && now >= timestamp && now - timestamp <= 30_000 ? "current" : "stale",
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
