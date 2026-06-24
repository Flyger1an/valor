import { NextResponse } from "next/server";
import { LocalStateStore } from "@/lib/state/local-store";
import type { RelativeValueSignal, RiskState } from "@/lib/domain/types";

export const dynamic = "force-dynamic";

/**
 * Bridge: map Valor's RelativeValueSignal -> the locked Evolver contract
 * (shared/signal.schema.json) and best-effort forward to the Evolver's /ingest.
 *
 * HONEST GAPS — Valor does not yet compute these as first-class fields, so they
 * are derived/defaulted here and marked in metadata. Enrich Valor's signal engine
 * to emit them natively for full fidelity:
 *   - zscore                      -> currently 0 except where Valor computes it
 *   - spread_value                -> proxied from expectedEdgeBps (bps -> fraction)
 *   - expected_convergence_hours  -> defaulted per type
 */
const KIND_TO_TYPE: Record<string, string | null> = {
  btc_eth_ratio: "cointegration_spread",
  pair_spread_zscore: "stat_arb_pair",
  funding_carry: "funding_arb",
  spot_perp_basis: "basis_trade",
  cross_exchange_premium: "triangular",
  stablecoin_depeg: null, // watch-only, not a convergence trade — skip
  volatility_regime: null, // portfolio filter — skip
};

const DEFAULT_HOURS: Record<string, number> = {
  cointegration_spread: 6,
  stat_arb_pair: 6,
  funding_arb: 8,
  basis_trade: 24,
  triangular: 2,
};

const REGIME_BY_RISK: Record<RiskState, string> = {
  Green: "low_vol",
  Yellow: "contango",
  Red: "high_vol",
  Black: "black",
};

function toContract(sig: RelativeValueSignal, regime: string) {
  const type = KIND_TO_TYPE[sig.kind];
  if (!type) return null;
  // Fields are real now (engine-computed). Fall back only for pre-enrichment
  // rows persisted before the columns existed; flag whatever still falls back.
  const derived: string[] = [];
  if (sig.zscore === undefined) derived.push("zscore");
  if (sig.spreadValue === undefined) derived.push("spread_value");
  if (sig.expectedConvergenceHours === undefined) derived.push("expected_convergence_hours");
  return {
    signal_id: sig.id,
    timestamp: sig.timestamp,
    type,
    assets: sig.assetPair.split("/"),
    zscore: sig.zscore ?? 0,
    spread_value: sig.spreadValue ?? sig.expectedEdgeBps / 10_000,
    expected_convergence_hours:
      sig.expectedConvergenceHours ?? DEFAULT_HOURS[type] ?? 6,
    risk_score: Math.max(0, Math.min(1, sig.riskScore / 100)), // 0..100 -> 0..1
    confidence: sig.confidence,
    regime,
    metadata: {
      valor_kind: sig.kind,
      venue: sig.venue,
      expected_edge_bps_net: sig.expectedEdgeBps,
      liquidity_score: sig.liquidityScore,
      opportunity_score: sig.opportunityScore,
      eligible_for_paper: sig.eligibleForPaperTrading,
      derived_fields: derived,
    },
  };
}

export async function GET() {
  const state = new LocalStateStore().read();
  const regime = REGIME_BY_RISK[state.risk?.state ?? "Yellow"];
  const mapped = (state.signals ?? [])
    .map((s) => toContract(s, regime))
    .filter((s): s is NonNullable<typeof s> => s !== null);

  // Best-effort forward to the Evolver (no new npm dep — uses fetch).
  const ingestUrl = process.env.EVOLVER_INGEST_URL;
  if (ingestUrl) {
    await Promise.allSettled(
      mapped.map((sig) =>
        fetch(ingestUrl, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(sig),
        }),
      ),
    );
  }

  return NextResponse.json({ count: mapped.length, forwarded: Boolean(ingestUrl), signals: mapped });
}
