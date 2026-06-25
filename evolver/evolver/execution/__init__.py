"""Execution layer — OKX **demo/testnet** order client. ZERO live-money capability.

This package is the Phase-4 executor: it places REAL orders against OKX's simulated-trading
environment (fake money, real matching engine) so we can measure true fills — spread, slippage,
partial fills, latency, funding — instead of paper marks. It is hard-wired to demo and has no
live code path; going live would be a separate, deliberately-written, human-authorized module.
"""
from evolver.execution.okx_executor import OKXDemoExecutor, OKXError

__all__ = ["OKXDemoExecutor", "OKXError"]
