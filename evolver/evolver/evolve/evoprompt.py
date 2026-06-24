"""Evolutionary Prompt Engineering (EvoPrompt, Guo et al. ICLR 2024).

The genome is the ANALYST'S DECISION PROMPT (text). The LLM itself performs the genetic
operators — crossover ("merge the best of these two prompts") and mutation ("improve this
prompt") — which is EvoPrompt's key move: language is the search space, the LLM is the
variation operator. Fitness = realized P&L of the decisions the prompt produces on a held-out
set of labeled signals. Selection keeps the elite. Under-used in crypto RV; proven on NLP.

Honesty carries over: fitness is REALIZED forward P&L on held-out events, not self-report. A
prompt only wins by making decisions that actually made money — it cannot talk its way to a
high score. Degrades to a deterministic mock analyst when no OPENAI_API_KEY (offline/exact).
"""
from __future__ import annotations

import json
import os
import random

from evolver.evolve.mutate import _openai_chat

FAST = os.getenv("FAST_MODEL", "gpt-5-mini")
STRONG = os.getenv("STRONG_MODEL", "gpt-5.5")

SEED_PROMPTS = [
    "You decide whether to harvest a cross-venue funding dislocation. Reply JSON "
    "{\"action\":\"enter\"|\"neutral\"}. Enter when the opportunity is attractive.",
    "You are a risk-averse arbitrage desk. Given a funding dislocation signal, decide to "
    "enter only if the edge clearly beats costs and risk; else stay neutral. JSON "
    "{\"action\":\"enter\"|\"neutral\"}.",
    "Evaluate the signal and act. Output JSON {\"action\":\"enter\"|\"neutral\"}.",
]


def analyst_decide(prompt, signal, model=FAST):
    """Run the candidate prompt as the analyst on one signal -> 'enter'|'neutral'."""
    if not os.getenv("OPENAI_API_KEY"):
        return "enter" if signal.get("ann_diff", 0) >= 0.15 else "neutral"   # mock rule
    txt = _openai_chat(prompt, json.dumps(signal), model)
    if not txt:
        return "neutral"
    try:
        return "enter" if json.loads(txt).get("action") == "enter" else "neutral"
    except (ValueError, TypeError, AttributeError):
        return "neutral"


def fitness(prompt, eval_set, model=FAST):
    """Realized P&L captured: sum of forward pnl over events the prompt chose to enter.
    The best prompt learns to enter the profitable subset and skip the traps."""
    pnl = enters = 0.0
    for ex in eval_set:
        if analyst_decide(prompt, ex["signal"], model) == "enter":
            pnl += ex["pnl"]
            enters += 1
    return pnl, int(enters)


def _crossover(a, b, model=STRONG):
    sys_ = ("You breed trading-decision prompts. Given two parent prompts, write ONE child "
            "that combines their best instructions and is concise. Reply JSON {\"prompt\":\"...\"}.")
    txt = _openai_chat(sys_, f"PARENT A:\n{a}\n\nPARENT B:\n{b}", model)
    return _parse_prompt(txt)


def _mutate(p, model=STRONG):
    sys_ = ("You improve a trading-decision prompt so its decisions make more money while "
            "avoiding losing trades. Rephrase/sharpen it; keep the JSON output contract. Reply "
            "JSON {\"prompt\":\"...\"}.")
    txt = _openai_chat(sys_, p, model)
    return _parse_prompt(txt)


def _parse_prompt(txt):
    if not txt:
        return None
    try:
        p = json.loads(txt).get("prompt")
        return p if isinstance(p, str) and len(p) > 20 else None
    except (ValueError, TypeError, AttributeError):
        return None


def evolve_prompts(eval_set, seeds=None, generations=3, pop=4, model=FAST, seed=7, log=print):
    rng = random.Random(seed)
    pool = list(seeds or SEED_PROMPTS)
    scored = [(fitness(p, eval_set, model)[0], p) for p in pool]
    use_llm = bool(os.getenv("OPENAI_API_KEY"))
    log(f"seed prompts: best P&L {max(s for s, _ in scored):+.4f} | LLM operators: {use_llm}")
    for g in range(generations):
        scored.sort(key=lambda x: -x[0])
        parents = [p for _, p in scored[:max(2, len(scored) // 2)]]
        children = []
        for _ in range(pop):
            if use_llm and len(parents) >= 2:
                a, b = rng.sample(parents, 2)
                c = _crossover(a, b, STRONG) or a
                c = _mutate(c, STRONG) or c
            else:                                  # offline: lexical recombination
                c = rng.choice(parents)
            if c and c not in [p for _, p in scored]:
                children.append(c)
        scored += [(fitness(c, eval_set, model)[0], c) for c in children]
        scored.sort(key=lambda x: -x[0])
        scored = scored[:max(pop, 4)]
        log(f"gen {g+1}/{generations}: best P&L {scored[0][0]:+.4f} | pool {len(scored)}")
    best_pnl, best = max(scored, key=lambda x: x[0])
    return {"best_prompt": best, "best_pnl": round(best_pnl, 4),
            "enters": fitness(best, eval_set, model)[1], "pool": scored}
