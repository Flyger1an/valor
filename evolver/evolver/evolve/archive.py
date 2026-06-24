"""MAP-Elites Quality-Diversity archive.

Single-objective optimization collapses to one in-sample peak — the classic overfit. QD
keeps the best genome found in each cell of a behavior grid (here: turnover x risk), so the
search returns a DIVERSE portfolio of robust strategies and explores instead of converging
prematurely. Mouret & Clune (2015); rare in crypto RV, strong track record in RL/robotics.
"""
from __future__ import annotations

import random


class MapElites:
    def __init__(self):
        self.cells = {}   # behavior tuple -> scorecard

    def add(self, card):
        cur = self.cells.get(card.behavior)
        if cur is None or card.fitness > cur.fitness:
            self.cells[card.behavior] = card
            return True
        return False

    def sample(self, rng: random.Random):
        return rng.choice(list(self.cells.values())) if self.cells else None

    def elites(self):
        return sorted(self.cells.values(), key=lambda c: -c.fitness)

    def coverage(self):
        return len(self.cells)
