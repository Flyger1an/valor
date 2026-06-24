"""DefiLlama — free, no-key, historical on-chain TVL (value locked in smart contracts).

Real on-chain fundamentals price can't see: chain TVL (capital committed to a network) and
protocol TVL (value in a DeFi app). Daily history back to 2017 (chains) / 2020 (protocols).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def _get(u):
    req = urllib.request.Request(u, headers={"User-Agent": "valor-research"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def _daily(pairs):
    """[(date_sec, tvl)] -> {utc_day_ms: tvl}, dropping non-positive."""
    out = {}
    for ds, tvl in pairs:
        if tvl and tvl > 0:
            out[(int(ds) * 1000 // 86_400_000) * 86_400_000] = float(tvl)
    return out


def chain_tvl(chain: str) -> dict:
    try:
        d = _get(f"https://api.llama.fi/v2/historicalChainTvl/{chain}")
        return _daily([(x["date"], x["tvl"]) for x in d])
    except (urllib.error.URLError, KeyError, ValueError, TypeError):
        return {}


def protocol_tvl(slug: str) -> dict:
    try:
        d = _get(f"https://api.llama.fi/protocol/{slug}")
        tvl = d.get("tvl", []) if isinstance(d, dict) else []
        return _daily([(x["date"], x.get("totalLiquidityUSD")) for x in tvl])
    except (urllib.error.URLError, KeyError, ValueError, TypeError):
        return {}


# token symbol -> ("chain"|"protocol", slug). Curated for coins with real TVL exposure.
TVL_MAP = {
    # L1 / L2 tokens -> chain TVL
    "ETH": ("chain", "Ethereum"), "SOL": ("chain", "Solana"), "AVAX": ("chain", "Avalanche"),
    "BNB": ("chain", "BSC"), "MATIC": ("chain", "Polygon"), "ARB": ("chain", "Arbitrum"),
    "OP": ("chain", "Optimism"), "NEAR": ("chain", "Near"), "APT": ("chain", "Aptos"),
    "SUI": ("chain", "Sui"), "SEI": ("chain", "Sei"), "INJ": ("chain", "Injective"),
    "ATOM": ("chain", "Cosmos"), "FTM": ("chain", "Fantom"), "KAVA": ("chain", "Kava"),
    "CELO": ("chain", "Celo"), "ALGO": ("chain", "Algorand"), "ROSE": ("chain", "Oasis"),
    "STX": ("chain", "Stacks"), "TIA": ("chain", "Celestia"), "TRX": ("chain", "Tron"),
    "FLOW": ("chain", "Flow"), "EGLD": ("chain", "MultiversX"), "MINA": ("chain", "Mina"),
    # DeFi tokens -> protocol TVL
    "AAVE": ("protocol", "aave"), "UNI": ("protocol", "uniswap"), "CRV": ("protocol", "curve-dex"),
    "MKR": ("protocol", "makerdao"), "COMP": ("protocol", "compound-finance"),
    "SNX": ("protocol", "synthetix"), "LDO": ("protocol", "lido"), "SUSHI": ("protocol", "sushi"),
    "PENDLE": ("protocol", "pendle"), "GMX": ("protocol", "gmx"), "DYDX": ("protocol", "dydx"),
    "RUNE": ("protocol", "thorchain"), "CAKE": ("protocol", "pancakeswap"),
    "BAL": ("protocol", "balancer"), "1INCH": ("protocol", "1inch-network"),
}


def tvl_for(symbol: str) -> dict:
    m = TVL_MAP.get(symbol)
    if not m:
        return {}
    return chain_tvl(m[1]) if m[0] == "chain" else protocol_tvl(m[1])
