"""Streamlit cockpit for the Evolver — live KPI tiles, equity path, signal cards.

Reads the append-only ledger (decoupled from the loop process). In cloud, point it
at RDS instead of the JSONL file.

Run: streamlit run evolver/dashboard/app.py
"""
from __future__ import annotations

import json
import os
import pathlib

import plotly.graph_objects as go
import streamlit as st

LEDGER = pathlib.Path(os.getenv("EVOLVER_LEDGER", ".evolver/ledger.jsonl"))


def load_rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    rows = []
    for line in LEDGER.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


st.set_page_config(page_title="Valor Evolver", layout="wide")
st.title("🧬 Valor Evolver — closed-loop cockpit")
if st.button("🔄 refresh"):
    st.rerun()

rows = [r for r in load_rows() if r.get("direction") != "neutral"]
equity, e = [], 100_000.0
for r in rows:
    e *= (1 + r.get("pnl_pct", 0.0))
    equity.append(e)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Paper equity", f"${equity[-1]:,.0f}" if equity else "$100,000")
c2.metric("Trades", len(rows))
wins = [r for r in rows if r.get("net_pnl_usd", 0) > 0]
c3.metric("Win rate", f"{(len(wins)/len(rows)*100):.0f}%" if rows else "—")
c4.metric("Net PnL", f"${sum(r.get('net_pnl_usd',0) for r in rows):,.0f}")

if equity:
    st.plotly_chart(go.Figure(go.Scatter(y=equity, mode="lines", name="paper equity")),
                    use_container_width=True)

st.subheader("Recent signal cards")
for r in rows[-12:][::-1]:
    with st.container(border=True):
        a, b = st.columns([3, 1])
        a.markdown(f"**{r.get('type','?')}** · {r.get('signal_id')} · "
                   f"{r.get('direction')} · conv={'✅' if r.get('converged') else '❌'}")
        b.markdown(f"`{r.get('net_pnl_usd',0):+,.2f}` USD")
