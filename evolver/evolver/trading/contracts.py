"""Strict contracts at the strategy, supervisor, and execution boundaries."""
from __future__ import annotations

import hashlib
import json
import math
import datetime as dt
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation


def decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("booleans are not amounts")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid amount") from exc
    if not result.is_finite():
        raise ValueError("amount must be finite")
    return result


def encode(value) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utc_timestamp(value: str) -> float:
    # Exchanges publish nanosecond fractions; Python <3.11 accepts at most six digits.
    normalized = re.sub(r"(\.\d{6})\d+(?=[+-]\d\d:\d\d$)", r"\1", value.replace("Z", "+00:00"))
    stamp = dt.datetime.fromisoformat(normalized)
    if stamp.tzinfo is None:
        raise ValueError("market timestamp needs an explicit timezone")
    return stamp.timestamp()


@dataclass(frozen=True)
class Policy:
    mode: str
    quote_currency: str
    starting_cash: Decimal
    max_position_notional: Decimal
    max_total_notional: Decimal
    max_loss_per_trade: Decimal
    daily_loss_limit: Decimal
    max_drawdown: Decimal
    max_trades_per_day: int
    max_spread_bps: Decimal
    max_quote_age_seconds: int
    supervisor_ttl_seconds: int
    fee_bps: Decimal
    slippage_bps: Decimal
    allowed_instruments: tuple[str, ...]
    approved_strategies: tuple[str, ...]
    trading_hours_utc: tuple[int, ...]
    study_end_utc: str | None = "2027-01-01T06:00:00+00:00"
    max_study_days: int = 90
    max_model_calls_per_day: int = 60
    max_model_cost_per_day: Decimal = Decimal("0.25")
    trading_weekdays_utc: tuple[int, ...] = (0, 1, 2, 3, 4)
    study_start_event: str = "runtime_start"
    max_paper_days: int = 90

    def __post_init__(self):
        if self.mode not in {"paper", "demo", "live"}:
            raise ValueError("mode must be paper, demo, or live")
        for key in ("starting_cash", "max_position_notional", "max_total_notional",
                    "max_loss_per_trade", "daily_loss_limit", "max_drawdown", "max_spread_bps", "max_model_cost_per_day"):
            amount = decimal(getattr(self, key))
            if amount <= 0:
                raise ValueError(f"{key} must be positive")
            object.__setattr__(self, key, amount)
        for key in ("fee_bps", "slippage_bps"):
            amount = decimal(getattr(self, key))
            if amount < 0 or amount > 100:
                raise ValueError(f"{key} must be between 0 and 100")
            object.__setattr__(self, key, amount)
        if not self.max_position_notional <= self.max_total_notional <= self.starting_cash:
            raise ValueError("unleveraged runtime requires position <= total <= starting cash")
        if not self.max_loss_per_trade <= self.daily_loss_limit <= self.max_drawdown <= self.starting_cash:
            raise ValueError("require trade loss <= daily loss <= drawdown <= starting cash")
        for key in ("max_trades_per_day", "max_quote_age_seconds", "supervisor_ttl_seconds",
                    "max_study_days", "max_paper_days", "max_model_calls_per_day"):
            if type(getattr(self, key)) is not int or not 0 < getattr(self, key) <= 86400:
                raise ValueError(f"{key} must be a positive bounded integer")
        if not self.quote_currency or not re.fullmatch(r"[A-Z]{3,8}", self.quote_currency):
            raise ValueError("invalid quote currency")
        for key in ("allowed_instruments", "approved_strategies", "trading_hours_utc", "trading_weekdays_utc"):
            values = tuple(getattr(self, key))
            if not values or len(set(values)) != len(values):
                raise ValueError(f"{key} must be nonempty and unique")
            object.__setattr__(self, key, values)
        if any(type(h) is not int or h not in range(24) for h in self.trading_hours_utc):
            raise ValueError("trading hours must be UTC hours 0..23")
        if any(type(d) is not int or d not in range(7) for d in self.trading_weekdays_utc):
            raise ValueError("weekdays must be 0..6")
        if self.max_study_days > 90 or self.max_paper_days > 90:
            raise ValueError("study cannot exceed 90 days")
        if self.study_start_event not in {"runtime_start", "first_live_fill"}:
            raise ValueError("unsupported study start event")
        self.study_end_timestamp
        if any(not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+", x) or
               not x.endswith("-" + self.quote_currency) for x in self.allowed_instruments):
            raise ValueError("this runtime supports cash spot instruments in one quote currency")
        if any(not re.fullmatch(r"[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+", s) for s in self.approved_strategies):
            raise ValueError("strategies must identify an immutable name@version")

    @classmethod
    def from_dict(cls, value: dict) -> "Policy":
        return cls(**value)  # unknown and missing fields are errors, never silently ignored

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(encode(asdict(self)).encode()).hexdigest()

    @property
    def study_end_timestamp(self) -> float | None:
        if self.study_end_utc is None:
            return None
        end = dt.datetime.fromisoformat(self.study_end_utc.replace("Z", "+00:00"))
        if end.tzinfo is None:
            raise ValueError("study deadline must include a timezone")
        return end.timestamp()


@dataclass(frozen=True)
class Quote:
    instrument: str
    bid: Decimal
    ask: Decimal
    timestamp: float

    def __post_init__(self):
        object.__setattr__(self, "bid", decimal(self.bid))
        object.__setattr__(self, "ask", decimal(self.ask))
        object.__setattr__(self, "timestamp", float(decimal(self.timestamp)))
        if not 0 < self.bid <= self.ask or not math.isfinite(self.timestamp) or self.timestamp <= 0:
            raise ValueError("invalid quote")

    def fresh(self, now: float, max_age: int) -> bool:
        return 0 <= now - self.timestamp <= max_age


@dataclass(frozen=True)
class Intent:
    signal_id: str
    strategy: str
    instrument: str
    side: str
    quantity: Decimal
    stop_price: Decimal
    timestamp: float
    reason: str
    limit_price: Decimal = Decimal("0")

    def __post_init__(self):
        object.__setattr__(self, "quantity", decimal(self.quantity))
        object.__setattr__(self, "stop_price", decimal(self.stop_price))
        object.__setattr__(self, "limit_price", decimal(self.limit_price))
        object.__setattr__(self, "timestamp", float(decimal(self.timestamp)))
        if self.side not in {"buy", "sell"} or self.quantity <= 0 or self.stop_price < 0:
            raise ValueError("invalid intent")
        if not self.signal_id or len(self.signal_id) > 200 or not math.isfinite(self.timestamp) or self.timestamp <= 0:
            raise ValueError("invalid signal identity/time")
        if self.limit_price < 0 or (self.side == "buy" and self.limit_price <= 0):
            raise ValueError("entry must have a maximum execution price")

    @property
    def client_id(self) -> str:
        # A logical signal can be submitted at most once, even after a completed order.
        raw = encode([self.strategy, self.instrument, self.signal_id, self.side])
        return "vr" + hashlib.sha256(raw.encode()).hexdigest()[:30]


@dataclass(frozen=True)
class OrderReport:
    client_id: str
    status: str
    filled_quantity: Decimal
    filled_value: Decimal
    fees: Decimal
    base_fees: Decimal = Decimal("0")
    first_fill_timestamp: float | None = None

    def __post_init__(self):
        if self.status not in {"open", "partial", "filled", "cancelled", "rejected"}:
            raise ValueError("unrecognized broker order state")
        for field in ("filled_quantity", "filled_value", "fees", "base_fees"):
            object.__setattr__(self, field, decimal(getattr(self, field)))
            if getattr(self, field) < 0:
                raise ValueError("negative cumulative execution amount")
        if bool(self.filled_quantity) != bool(self.filled_value):
            raise ValueError("fill quantity and value must both be present")
        if self.base_fees > self.filled_quantity:
            raise ValueError("base fees exceed the acquired quantity")
        if self.first_fill_timestamp is not None:
            stamp = float(decimal(self.first_fill_timestamp))
            if stamp <= 0:
                raise ValueError("invalid first fill timestamp")
            object.__setattr__(self, "first_fill_timestamp", stamp)


TERMINAL = {"filled", "cancelled", "rejected"}
