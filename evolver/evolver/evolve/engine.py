"""The evolutionary loop: init -> evaluate (honest fitness) -> MAP-Elites -> LLM/algorithmic
mutate -> repeat, then a population-wide PBO and a promotion gate no fake edge survives.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

from evolver.evolve import fitness as F
from evolver.evolve.archive import MapElites
from evolver.evolve.mutate import algorithmic_mutate, crossover, llm_mutate


def _rand_genome(space, rng):
    return {n: (int(round(rng.uniform(lo, hi))) if typ is int else round(rng.uniform(lo, hi), 4))
            for n, (lo, hi, typ) in space.items()}


def evolve(backtest, space, family_desc, generations=8, pop=8, seed=7,
           use_llm=True, k_folds=5, log=print):
    rng = random.Random(seed)
    arch = MapElites()
    all_cards, full_srs, used_llm = [], [], 0

    def _score(params):
        # provisional deflation stats; authoritative DSR recomputed at the end
        card = F.evaluate(params, backtest, k_folds=k_folds,
                          n_trials=max(len(all_cards), 1),
                          var_trials_sr=(_var(full_srs) if len(full_srs) > 1 else 0.25))
        all_cards.append(card)
        if card.n_trades >= 3:
            full_srs.append(card.full_sharpe)
        arch.add(card)
        return card

    log(f"init: {pop} random genomes")
    for _ in range(pop):
        _score(_rand_genome(space, rng))

    for g in range(generations):
        elites = arch.elites()
        traj = [{"params": c.params, "oos_sharpe": c.oos_sharpe, "dsr": c.dsr,
                 "recent_sharpe": c.recent_sharpe, "consistency": c.consistency} for c in elites[:12]]
        children = []
        for _ in range(pop):
            if use_llm and traj:
                child, did = llm_mutate(traj, space, family_desc, rng)
                used_llm += int(did)
            else:
                a = arch.sample(rng)
                b = arch.sample(rng)
                child = crossover(a.params, b.params, space, rng) if a and b else _rand_genome(space, rng)
                child = algorithmic_mutate(child, space, rng)
            children.append(child)
        for c in children:
            _score(c)
        best = arch.elites()[0]
        log(f"gen {g+1}/{generations}: archive {arch.coverage()} cells | "
            f"best fitness {best.fitness:+.3f} (oos {best.oos_sharpe:+.2f}, recent {best.recent_sharpe:+.2f})")

    # authoritative deflation + PBO across the whole search
    n_trials = len(all_cards)                              # every evaluated genome IS a trial
    med_n = F._median([c.n_trades for c in all_cards if c.n_trades >= 3]) or 30
    # Floor trial-Sharpe variance at a single Sharpe's sampling variance (~1/T). A CONVERGED search
    # clusters its elites and would otherwise report ~0 spread -> under-deflate (a free pass); the
    # floor stops the optimizer from deflating away its own multiple-testing penalty.
    var_tr = max(_var(full_srs), 1.0 / max(med_n, 5))
    for c in arch.elites():
        rets = [r for _, r in c.trades]
        sk, ku = F._moments(rets)
        c.dsr = round(F.deflated_sharpe(c.full_sharpe, c.n_trades, n_trials, var_tr, sk, ku), 3)
    pbo = _population_pbo(all_cards)

    elites = arch.elites()
    promoted = [c for c in elites
                if c.dsr > 0.95 and c.recent_sharpe > 0 and c.consistency >= 0.6
                and c.n_trades >= 30 and (pbo is None or pbo < 0.5)]
    return SimpleNamespace(elites=elites, archive=arch, pbo=pbo, promoted=promoted,
                           n_evaluated=len(all_cards), n_trials=n_trials,
                           var_trials_sr=round(var_tr, 4), used_llm=used_llm)


def _var(xs):
    if len(xs) < 2:
        return 0.25
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _population_pbo(cards):
    """PBO across the population. Buckets trades into equal-width TIME bins (not calendar months) so
    it actually RUNS on the few-month holdouts the loop sees. The old 10-calendar-month floor made it
    silently return None — a vacuous gate pass — in exactly the short-sample regime where overfitting
    is worst."""
    usable = [c for c in cards if c.n_trades >= 10]
    if len(usable) < 4:
        return None
    tmin = min(t for c in usable for t, _ in c.trades)
    tmax = max(t for c in usable for t, _ in c.trades)
    if tmax <= tmin:
        return None
    n_bins = 10                                            # == s_blocks below (one block per bin)
    width = (tmax - tmin + 1) / n_bins
    matrix = [[0.0] * len(usable) for _ in range(n_bins)]
    for j, c in enumerate(usable):
        for t, r in c.trades:
            b = min(n_bins - 1, int((t - tmin) / width))
            matrix[b][j] += r
    return F.cscv_pbo(matrix, s_blocks=n_bins)
