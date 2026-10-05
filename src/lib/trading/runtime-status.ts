import { readFileSync, statSync } from "node:fs";

type Json = Record<string, unknown>;
export interface TradingRuntimeStatus {
  configured: boolean;
  status: "unconfigured" | "unavailable" | "stale" | "current";
  mode?: "paper" | "demo" | "live";
  timestamp?: string;
  equity?: number;
  cash?: number;
  startingCash?: number;
  realizedPnl?: number;
  unrealizedPnl?: number;
  modelCost?: number;
  modelConnection?: string;
  afterModelCosts?: number;
  operatingCosts?: number;
  afterOperatingCosts?: number;
  feesProvisional?: boolean;
  protectionComplete?: boolean;
  entryPause?: string;
  demoDrill?: string;
  strategyTrades?: number;
  activeSessions?: number;
  feePostingDays?: number;
  entrySessionOpen?: boolean;
  newsStatus?: string;
  newsCheckedAt?: string;
  newsHeadlines?: { title: string; url: string; source: string }[];
  halt?: string;
  activeStrategy?: string;
  studyEnd?: string;
  studyStarted?: string;
  learning?: string;
  supervisor?: string;
  positionCount?: number;
  pendingOrderCount?: number;
  policyHash?: string;
}

function amount(value: unknown): number {
  if (typeof value !== "number" && typeof value !== "string") throw new Error("invalid amount");
  if (typeof value === "string" && !value.trim()) throw new Error("empty amount");
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new Error("nonfinite amount");
  return parsed;
}

export function loadTradingRuntimeStatus(
  path = process.env.VALOR_TRADING_SNAPSHOT_PATH,
  now = Date.now(),
): TradingRuntimeStatus {
  if (!path) return { configured: false, status: "unconfigured" };
  try {
    if (statSync(path).size > 1_000_000) throw new Error("oversized snapshot");
    const raw = JSON.parse(readFileSync(path, "utf8")) as Json;
    if (raw.schema_version !== 1 || !["paper", "demo", "live"].includes(String(raw.mode)) ||
        !Array.isArray(raw.positions) || !Array.isArray(raw.pending_orders) ||
        !/^[a-f0-9]{64}$/.test(String(raw.policy_hash))) throw new Error("invalid snapshot");
    const timestamp = amount(raw.timestamp) * 1000;
    const reconciled = raw.broker_reconciled_at ? amount(raw.broker_reconciled_at) * 1000 : 0;
    const quotes = Object.values((raw.quotes ?? {}) as Record<string, Json>);
    const quotesFresh = quotes.length > 0 && quotes.every(q => {
      const age = now - amount(q.timestamp) * 1000;
      return age >= 0 && age <= 30_000;
    });
    const marketFresh = typeof raw.healthy === "boolean" ? raw.healthy : quotesFresh;
    const fresh = now >= timestamp && now - timestamp <= 30_000 &&
      now >= reconciled && now - reconciled <= 30_000 && raw.marks_fresh === true && marketFresh;
    const equity = amount(raw.equity), startingCash = amount(raw.starting_cash), realizedPnl = amount(raw.realized_pnl);
    const usage = raw.model_usage as Json | undefined;
    const modelCost = usage?.estimated_cost_usd === undefined ? undefined : amount(usage.estimated_cost_usd);
    const supervisor = raw.supervisor as Json;
    const costs = raw.costs as Json | undefined;
    const accounting = raw.accounting as Json | undefined;
    const readiness = raw.readiness as Json | undefined;
    const cashFlows = accounting?.net_cash_flows === undefined ? 0 : amount(accounting.net_cash_flows);
    return {
      configured: true, status: fresh ? "current" : "stale",
      mode: raw.mode as "paper" | "demo" | "live", timestamp: new Date(timestamp).toISOString(),
      equity, startingCash, cash: amount(raw.cash), realizedPnl,
      unrealizedPnl: equity - startingCash - cashFlows - realizedPnl,
      modelConnection: String((usage?.model_connection as Json)?.status ?? "unverified"),
      modelCost, afterModelCosts: modelCost === undefined ? undefined : equity - startingCash - cashFlows - modelCost,
      operatingCosts: costs?.total_operating_estimated_usd === undefined ? undefined : amount(costs.total_operating_estimated_usd),
      afterOperatingCosts: costs?.experiment_pnl_estimated_usd === undefined ? undefined : amount(costs.experiment_pnl_estimated_usd),
      feesProvisional: accounting?.fees_provisional === true,
      protectionComplete: (raw.protection as Json)?.complete === true,
      entryPause: String(raw.entry_pause ?? ""),
      demoDrill: String((readiness?.engineering_drill as Json)?.status ?? "not run"),
      strategyTrades: readiness?.strategy_round_trips === undefined ? undefined : amount(readiness.strategy_round_trips),
      activeSessions: readiness?.active_strategy_sessions === undefined ? undefined : amount(readiness.active_strategy_sessions),
      feePostingDays: Array.isArray(readiness?.fee_posting_days) ? readiness.fee_posting_days.length : undefined,
      entrySessionOpen: typeof raw.entry_session_open === "boolean" ? raw.entry_session_open : undefined,
      newsStatus: String((raw.news as Json)?.status ?? "not configured"),
      newsCheckedAt: (raw.news as Json)?.fetched_at ? new Date(amount((raw.news as Json).fetched_at) * 1000).toISOString() : undefined,
      newsHeadlines: Array.isArray((raw.news as Json)?.items) ? ((raw.news as Json).items as Json[]).slice(0, 5)
        .filter(item => { try { const url = new URL(String(item.url)); return url.protocol === "https:" && !url.username && !url.password; } catch { return false; } })
        .map(item => ({title: String(item.headline), url: String(item.url), source: String(item.source)})) : [],
      halt: String(raw.halt ?? ""), activeStrategy: String(raw.active_strategy ?? "unselected"),
      studyEnd: raw.study_deadline ? new Date(amount(raw.study_deadline) * 1000).toISOString() : undefined,
      studyStarted: raw.study_started_at ? new Date(amount(raw.study_started_at) * 1000).toISOString() : undefined,
      learning: String((raw.learning as Json)?.status ?? "collecting_data"),
      supervisor: amount(supervisor.expires_at) * 1000 > now ? String(supervisor.action) : "expired — entries paused",
      positionCount: raw.positions.length, pendingOrderCount: raw.pending_orders.length,
      policyHash: String(raw.policy_hash),
    };
  } catch {
    // Do not leak local paths or substitute the legacy $100k demo portfolio.
    return { configured: true, status: "unavailable" };
  }
}
