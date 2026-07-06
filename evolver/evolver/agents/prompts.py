"""Versioned analyst prompts — makes the analyst's system prompt a DEPLOYABLE, auditable artifact
instead of a hardcoded constant, so prompt evolution (evolve/evoprompt.py) has a path to production.

Deployment rules (load-bearing):
  * "v1" is the in-repo incumbent. Challenger variants live in a registry FILE
    (EVOLVER_PROMPT_VARIANTS) written by register_variant() — e.g. an EvoPrompt winner.
  * WHICH variant runs is selected by strategy["analyst_prompt_variant"], and that only changes
    through the existing human-gated pending→/approve flow (or a human editing state directly).
    Nothing in this module switches the live variant on its own.
  * A challenger EARNS promotion through forward shadow comparison (run a second shadow-analyst
    arm with EVOLVER_ANALYST_VARIANT=<challenger>), never through backtest fitness alone.
  * get_analyst_prompt() falls back to v1 on any problem — a missing/corrupt registry can never
    take the analyst down.

Every decision records the variant + prompt sha8 in its attribution metadata, so the ledger can
attribute performance to the exact prompt text that produced each decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]   # CWD-independent default: a host `register`
# and a container reader must resolve the SAME file, or the A/B silently runs incumbent-vs-incumbent
VARIANTS_PATH = pathlib.Path(os.getenv("EVOLVER_PROMPT_VARIANTS", str(_ROOT / ".evolver/prompt_variants.json")))

ANALYST_V1 = """You are Valor's RV Paper-Trade Analyst (fast inner-loop model).
Inputs: one relative-value signal, current REGIME, PORTFOLIO state, and SIMILAR past
signals with realized outcomes.
Hard rules (the harness also enforces these — do not fight them):
- A signal is a mean-reversion/convergence idea. zscore<0 => expect spread up (LONG spread);
  zscore>0 => expect spread down (SHORT spread).
- risk_score is 0..1 (higher = riskier). If risk_score > {max_risk_score} OR
  confidence < {min_confidence} OR |zscore| < {min_abs_zscore} OR regime is defensive,
  action MUST be "neutral".
- Never propose leverage above {max_leverage} or size above {max_position_pct:.0%} of capital.
Output ONLY valid JSON, EXACTLY these fields and allowed values (no prose, no markdown):
  "action": one of "long" | "short" | "neutral"   (NEVER "enter"/"exit"/"buy"/"sell")
  "direction": one of "long_spread" | "short_spread" | "neutral"
  "size_usd": number, "leverage": number,
  "entry": object, "exit": object, "confidence": number, "rationale": string"""

_BUILTIN = {"v1": ANALYST_V1}


def prompt_sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _file_variants() -> dict:
    try:
        doc = json.loads(VARIANTS_PATH.read_text())
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def get_analyst_prompt(variant: str | None = None) -> tuple[str, str, str]:
    """(prompt_text, resolved_variant_name, sha8). Unknown/broken variant -> v1 (never fails)."""
    name = variant or "v1"
    text = _BUILTIN.get(name)
    if text is None:
        text = _file_variants().get(name)
    if not isinstance(text, str) or not text.strip():
        name, text = "v1", ANALYST_V1
    return text, name, prompt_sha(text)


def register_variant(name: str, text: str) -> str:
    """Add/overwrite a challenger variant in the registry file (atomic). Registration is NOT
    deployment — the live variant still only changes via the human-gated approve flow.
    Builtin names are reserved. Returns the variant's sha8."""
    if name in _BUILTIN:
        raise ValueError(f"variant name {name!r} is reserved (builtin)")
    if not text or not text.strip():
        raise ValueError("empty prompt text")
    # absent file -> fresh registry; PRESENT-but-unreadable -> refuse, or we'd silently destroy
    # every registered challenger (including one mid-A/B) by overwriting with just this one
    if VARIANTS_PATH.exists():
        try:
            variants = json.loads(VARIANTS_PATH.read_text())
            assert isinstance(variants, dict)
        except Exception:
            raise ValueError(f"prompt registry {VARIANTS_PATH} exists but is unreadable — "
                             "refusing to overwrite; inspect it first")
    else:
        variants = {}
    variants[name] = text
    VARIANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = VARIANTS_PATH.with_name(f"{VARIANTS_PATH.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(variants, indent=1))
    os.replace(tmp, VARIANTS_PATH)
    return prompt_sha(text)


def list_variants() -> dict:
    """{name: sha8} across builtin + registered (for /status surfaces and audits)."""
    out = {k: prompt_sha(v) for k, v in _BUILTIN.items()}
    out.update({k: prompt_sha(v) for k, v in _file_variants().items() if isinstance(v, str)})
    return out
