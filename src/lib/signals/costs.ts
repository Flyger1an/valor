/**
 * Conservative execution-cost assumptions used to convert gross dislocations
 * into the edge a taker could realistically capture. Kept deliberately simple
 * and explicit so research ranking reflects net edge, not headline premium.
 */

/** Taker fee per leg on a major venue, in basis points. */
export const TAKER_FEE_BPS = 5;

/**
 * Extra haircut for a cross-venue move: withdrawal fee, transfer-time price
 * risk, and execution slippage on top of the two taker legs.
 */
export const CROSS_VENUE_TRANSFER_BPS = 10;

/** Round-trip cost to establish a delta-neutral pair (two taker legs). */
export const PAIR_ENTRY_COST_BPS = 2 * TAKER_FEE_BPS;

/** Net edge for a cross-exchange premium: two taker legs + transfer costs. */
export function netCrossExchangeEdgeBps(grossPremiumBps: number): number {
  return grossPremiumBps - PAIR_ENTRY_COST_BPS - CROSS_VENUE_TRANSFER_BPS;
}

/** Net edge for a spot/perp basis trade after the two-legged entry cost. */
export function netBasisEdgeBps(grossEdgeBps: number): number {
  return grossEdgeBps - PAIR_ENTRY_COST_BPS;
}

/** Net edge for a funding-carry trade (short perp hedged with spot). */
export function netFundingCarryEdgeBps(grossEdgeBps: number): number {
  return grossEdgeBps - PAIR_ENTRY_COST_BPS;
}
