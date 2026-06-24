"""Variation operators: LLM-as-mutation (OPRO/FunSearch/EvoPrompt) + algorithmic fallback.

The LLM operator (OPRO-style) conditions on the *trajectory* of (genome -> score) pairs and
proposes the next genome, explicitly steered toward OUT-OF-SAMPLE robustness and recent-period
survival rather than in-sample Sharpe — so it explores like a quant who's been burned, not a
curve-fitter. Falls back to gaussian/crossover mutation when no OPENAI_API_KEY (offline, exact).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _clamp(params, space):
    out = {}
    for name, (lo, hi, typ) in space.items():
        v = params.get(name, (lo + hi) / 2)
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = (lo + hi) / 2
        v = max(lo, min(hi, v))
        out[name] = int(round(v)) if typ is int else round(v, 4)
    return out


def algorithmic_mutate(params, space, rng):
    child = dict(params)
    for name, (lo, hi, _typ) in space.items():
        if rng.random() < 0.5:
            span = hi - lo
            child[name] = params.get(name, (lo + hi) / 2) + rng.gauss(0, 0.18 * span)
        if rng.random() < 0.08:                       # occasional full reset (exploration)
            child[name] = rng.uniform(lo, hi)
    return _clamp(child, space)


def crossover(a, b, space, rng):
    return _clamp({n: (a if rng.random() < 0.5 else b).get(n) for n in space}, space)


def _openai_chat(system, user, model, timeout=40):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    body = {"model": model, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}],
            "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {key}"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=timeout))
        return resp["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None


def llm_mutate(trajectory, space, family_desc, rng, model=None):
    """OPRO-style: propose the next genome from the (genome->score) trajectory.

    trajectory: list of dicts {params, oos_sharpe, dsr, recent_sharpe, consistency},
    best-first. Returns (params, used_llm: bool).
    """
    model = model or os.getenv("STRONG_MODEL", "gpt-5.5")
    space_desc = {n: [lo, hi, typ.__name__] for n, (lo, hi, typ) in space.items()}
    lines = "\n".join(
        f"  {json.dumps(t['params'])} -> oos_sharpe {t['oos_sharpe']}, dsr {t['dsr']}, "
        f"recent {t['recent_sharpe']}, consistency {t['consistency']}"
        for t in trajectory[:12])
    system = (
        "You are an optimizer proposing the next parameter set for a quantitative trading "
        "strategy. You are adversarially aware of overfitting: in-sample Sharpe is worthless; "
        "you maximize OUT-OF-SAMPLE consistency, a high Deflated Sharpe (dsr, want >0.95), and "
        "survival in the most RECENT period (recent_sharpe). Avoid edges that only worked in the "
        "past. Reply with strict JSON: {\"params\": {...}} using only the given keys, within bounds.")
    user = (f"Strategy family: {family_desc}\nParameter space [min,max,type]: "
            f"{json.dumps(space_desc)}\n\nTrajectory so far (best-first):\n{lines}\n\n"
            "Propose ONE new parameter set likely to raise out-of-sample/deflated/recent "
            "performance. JSON only.")
    txt = _openai_chat(system, user, model)
    if txt:
        try:
            obj = json.loads(txt)
            params = obj.get("params", obj)
            return _clamp(params, space), True
        except (ValueError, TypeError, AttributeError):
            pass
    # fallback: mutate the current best
    base = trajectory[0]["params"] if trajectory else {n: (lo + hi) / 2 for n, (lo, hi, _) in space.items()}
    return algorithmic_mutate(base, space, rng), False
