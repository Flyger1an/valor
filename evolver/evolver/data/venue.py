"""Pluggable crypto venue — picks the exchange connector by EVOLVER_VENUE (okx default, or gate) and
re-exports the uniform semantic interface the research crypto refreshes call. So the SAME engine +
families run on any venue as a separate parallel hunt (its own state / queue / datasets / multiplicity,
set per-service in compose). Adding a venue = a connector module exposing these names + a row here.
"""
from __future__ import annotations

import os

NAME = os.getenv("EVOLVER_VENUE", "okx").lower()

if NAME == "gate":
    from evolver.data import gate as _c
else:
    NAME = "okx"
    from evolver.data import okx as _c

UNIVERSE = _c.UNIVERSE
hourly_ohlc = _c.hourly_ohlc
recent_ohlc = _c.recent_ohlc
hourly_closes = _c.hourly_closes
daily_closes = _c.daily_closes
funding = _c.funding
oi_daily = _c.oi_daily
liquidations = _c.liquidations
