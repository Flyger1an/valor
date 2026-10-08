"""The desk's senior reviewer: an LLM that reads a finished thesis and may VETO it.

It can never create, resize, or re-time a trade. It sees the thesis, every analyst's notes and
(live) the current news snapshot, and answers in strict JSON. Vetoed theses are recorded so the
desk can measure whether the reviewer's vetoes would have lost money (that is its scoreboard).

Configuration (same as Valor's LLM analyst copilot):
  LLM_API_ENABLED=true  LLM_API_BASE_URL=https://api.openai.com/v1  LLM_API_KEY=...  LLM_MODEL=...

If the model is unavailable, slow, or answers badly, the reviewer abstains and the desk's own
decision stands; the abstention is logged. The reviewer is a check, not a single point of failure.
"""
from __future__ import annotations

import json
import os
import urllib.request

PROMPT = """You are the senior risk reviewer on a crypto trading desk. The quant desk has written a
trade thesis. Your only power is to APPROVE or VETO it. You cannot change size, levels or timing.

Veto only for a concrete reason you can point to, such as:
- the evidence contradicts the thesis (e.g. a long while the notes say longs are crowded AND
  the regime is weakening; a range trade while the structure note says the range is breaking)
- a news or macro event in the snapshot makes the setup unreliable right now
- the invalidation level is somewhere the market will obviously hunt, or the target sits past a
  level the notes identify as strong
- the thesis reasoning is internally inconsistent
Do not veto just because markets are uncertain; the desk already requires reward-to-risk,
cost coverage and conviction. Most sound theses should be approved.

Answer with JSON only: {"approve": true|false, "reason": "<one or two sentences citing the evidence>",
"concerns": ["<short>", ...]}"""


def _context(thesis, news=None):
    keep = ("symbol", "setup", "direction", "execution", "regime", "entry_ref", "stop", "target",
            "reward_to_risk", "expected_move_pct", "cost_pct", "conviction", "conviction_parts", "reason", "evidence")
    return {"thesis": {k: thesis.get(k) for k in keep}, "news": news or "no news snapshot available"}


class LLMReviewer:
    def __init__(self, timeout=20, opener=None, news_provider=None):
        self.enabled = os.getenv("LLM_API_ENABLED", "false").lower() == "true" and bool(os.getenv("LLM_API_KEY"))
        self.base = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "gpt-5-mini")
        self.timeout, self.opener = timeout, opener or urllib.request.urlopen
        self.news_provider = news_provider
        self.log = []

    def __call__(self, thesis):
        if not self.enabled:
            return self._abstain(thesis, "reviewer disabled")
        body = {"model": self.model, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": PROMPT},
                             {"role": "user", "content": json.dumps(_context(thesis, self.news_provider() if self.news_provider else None), default=str)}]}
        req = urllib.request.Request(self.base+"/chat/completions", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json",
                                              "authorization": "Bearer "+os.getenv("LLM_API_KEY", "")})
        try:
            raw = json.loads(self.opener(req, timeout=self.timeout).read())
            verdict = json.loads(raw["choices"][0]["message"]["content"])
            approve = verdict.get("approve")
            reason = str(verdict.get("reason", ""))[:500]
            if not isinstance(approve, bool) or not reason:
                return self._abstain(thesis, "malformed reviewer answer")
        except Exception as exc:  # network, timeout, quota, bad JSON: abstain, never block the desk
            return self._abstain(thesis, f"reviewer unavailable: {type(exc).__name__}")
        self.log.append({"t": thesis.get("t"), "symbol": thesis.get("symbol"), "setup": thesis.get("setup"),
                         "approve": approve, "reason": reason, "concerns": verdict.get("concerns", [])[:5]})
        return approve, reason

    def _abstain(self, thesis, why):
        self.log.append({"t": thesis.get("t"), "symbol": thesis.get("symbol"), "setup": thesis.get("setup"),
                         "approve": True, "reason": why, "abstained": True})
        return True, why
