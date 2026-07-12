"""Telegram Ops bot (python-telegram-bot v21, async).

Roles via env allowlists (safety.is_admin / is_observer):
  Ops Admin   : /approve /reject /tweak /kill /reset /restart  (mutating)
  Observer    : /status /kpis /feedback                        (read-only + ratings)

Run: python -m evolver.telegram.bot
"""
from __future__ import annotations

import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes)

from evolver.graph import runtime as rt
from evolver.research import queue as rq
from evolver.safety import is_admin, is_observer, kill_switch, audit

FX_RESEARCH = os.getenv("EVOLVER_FX_RESEARCH", "/data/fx_research_state.json")  # the FX hunt's queue


async def status(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_observer(u.effective_chat.id):
        return
    k = rt.current_kpis()
    await u.message.reply_text(
        f"📊 Valor Evolver\nstate: {k['loop_state']} | ver {k['active_version']}\n"
        f"trades {k.get('trades',0)} | win {k.get('win_rate','-')} | "
        f"sharpe/trade {k.get('sharpe_per_trade','-')}\n"
        f"equity ${k['equity']:,.0f} | dd {k['drawdown']*100:.1f}% | halt {k['halt']}"
    )


async def kpis(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_observer(u.effective_chat.id):
        return
    import json
    await u.message.reply_text("```\n" + json.dumps(rt.current_kpis(), indent=2) + "\n```",
                               parse_mode="Markdown")


async def shadow(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    """/shadow — the forward PAPER book (liquidation basket). Read-only, zero orders."""
    if not is_observer(u.effective_chat.id):
        return
    import json
    p = os.getenv("EVOLVER_SHADOW", "/data/shadow_state.json")
    if not os.path.exists(p):
        return await u.message.reply_text("🕊 shadow runner hasn't ticked yet")
    s = json.load(open(p))
    rets, cap = [t["ret"] for t in s.get("closed", [])], 100_000.0
    eq = s.get("equity", cap)
    sh = 0.0
    if len(rets) > 1:
        m = sum(rets) / len(rets)
        sd = (sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        sh = m / sd if sd else 0.0
    body = (f"win {sum(r > 0 for r in rets)/len(rets):.0%} | sharpe/trade {sh:+.3f}"
            if rets else "no closed trades yet (waiting on liquidation cascades)")
    await u.message.reply_text(
        f"🕊 Shadow book — paper, zero orders\n"
        f"equity ${eq:,.0f} ({(eq/cap-1)*100:+.2f}%)\n"
        f"open {len(s.get('open', []))} | closed {len(rets)}\n{body}\n"
        f"ticks {s.get('ticks', 0)} since {s.get('started', '?')}")


async def research(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    """/research — autonomous discovery loop status + recent cycles."""
    if not is_observer(u.effective_chat.id):
        return
    import json
    st = rq.load()
    out = [f"🔬 Research loop — {st.get('cycles', 0)} cycles, {len(st.get('approved', []))} approved"]
    led = os.getenv("EVOLVER_RESEARCH_LEDGER", "/data/research_ledger.jsonl")
    if os.path.exists(led):
        for t in open(led).read().splitlines()[-3:]:
            try:
                out.append("  · " + json.loads(t).get("summary", "")[:72])
            except Exception:
                pass
    pend = st.get("pending", [])
    out.append(f"\n{len(pend)} awaiting approval" + (" — /candidates to act:" if pend else
               " — hunting (most cycles find nothing, by design)"))
    for p in pend[:5]:
        out.append(f"  [{p['id']}] OOS {p['oos_sharpe']} p{p['oos_p']} stable {p['stable']}")
    await u.message.reply_text("\n".join(out))


async def analyst(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    """/analyst — shadow v2: the analyst loop's live decisions, sim estimate vs live reality."""
    if not is_observer(u.effective_chat.id):
        return
    import json
    p = os.getenv("EVOLVER_SHADOW2", "/data/shadow_analyst_state.json")
    if not os.path.exists(p):
        return await u.message.reply_text("📐 shadow-analyst hasn't ticked yet")
    s = json.load(open(p))
    cap, cl = 100_000.0, s.get("closed", [])
    div = s.get("equity", cap) - s.get("sim_equity", cap)
    tail = ""
    if cl:
        wins = sum(1 for x in cl if x.get("shadow_pnl_pct", 0) > 0)
        md = sum(x.get("divergence", 0) for x in cl) / len(cl) * 100
        tail = f"\nshadow win {wins/len(cl):.0%} | mean divergence {md:+.3f}%/trade"
    await u.message.reply_text(
        f"📐 Shadow-analyst (v2) — sim vs reality, zero orders\n"
        f"SHADOW (live) ${s.get('equity', cap):,.0f} ({(s.get('equity', cap)/cap-1)*100:+.2f}%)\n"
        f"SIM (heuristic) ${s.get('sim_equity', cap):,.0f} ({(s.get('sim_equity', cap)/cap-1)*100:+.2f}%)\n"
        f"divergence ${div:+,.0f}  ← the gap between the sim's estimate and live reality\n"
        f"open {len(s.get('open', []))} | closed {len(cl)}{tail}")


async def approve(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(u.effective_chat.id):
        return await u.message.reply_text("⛔ admin only")
    p = rt.get_pending()
    if not p:
        return await u.message.reply_text("nothing pending")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Promote", callback_data=f"promote:{p['thread_id']}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject:{p['thread_id']}"),
    ]])
    await u.message.reply_text(
        f"Proposal {p.get('version')}\nΔsharpe {p.get('oos_delta_sharpe')} p={p.get('pvalue')}\n"
        f"{p.get('proposals')}", reply_markup=kb)


async def candidates(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    """/candidates — research-loop genomes that cleared the honest gate, awaiting your approval."""
    if not is_admin(u.effective_chat.id):
        return await u.message.reply_text("⛔ admin only")
    pend = rq.list_pending()
    if not pend:
        return await u.message.reply_text("no research candidates awaiting approval 🔬")
    for cand in pend:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Promote to shadow", callback_data=f"rpromote:{cand['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"rreject:{cand['id']}"),
        ]])
        await u.message.reply_text(
            f"🔬 {cand['id']} — {cand['family']}\n{cand['genome']}\n"
            f"OOS {cand['oos_sharpe']} (p {cand['oos_p']}, n {cand['oos_n']}) | "
            f"2x-cost {cand['twox_cost_sharpe']} | stable {cand['stable']}", reply_markup=kb)


async def fxcandidates(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    """/fxcandidates — FX-hunt genomes that cleared the gate (separate queue from crypto /candidates)."""
    if not is_admin(u.effective_chat.id):
        return await u.message.reply_text("⛔ admin only")
    pend = rq.list_pending(FX_RESEARCH)
    if not pend:
        return await u.message.reply_text("no FX candidates awaiting approval 🌐")
    for cand in pend:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Promote to shadow", callback_data=f"rpromote_fx:{cand['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"rreject_fx:{cand['id']}"),
        ]])
        await u.message.reply_text(
            f"🌐 {cand['id']} — {cand['family']}\n{cand['genome']}\n"
            f"OOS {cand['oos_sharpe']} (p {cand['oos_p']}, n {cand['oos_n']}) | "
            f"2x-cost {cand['twox_cost_sharpe']} | stable {cand['stable']}", reply_markup=kb)


async def fxshadow(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    """/fxshadow — forward track record of PROMOTED FX candidates (zero orders)."""
    if not is_observer(u.effective_chat.id):
        return
    import json
    p = os.getenv("EVOLVER_FX_SHADOW", "/data/fx_shadow_state.json")
    if not os.path.exists(p):
        return await u.message.reply_text("💱 fx-shadow hasn't ticked yet")
    s = json.load(open(p))
    snap = s.get("snapshot", [])
    if not snap:
        return await u.message.reply_text("💱 fx-shadow: no promoted FX candidates yet — /fxcandidates to Promote")
    body = "\n".join(f"  {x['id']} {x['family']}: fwd n {x['fwd_n']} | sharpe {x['fwd_sharpe']:+.2f} | "
                     f"ret {x['fwd_ret']:+.3f}" for x in snap)
    await u.message.reply_text(
        f"💱 FX shadow — forward-only track (zero orders), {s.get('ticks', 0)} ticks\n{body}")


async def on_button(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    q = u.callback_query
    await q.answer()
    if not is_admin(q.message.chat.id):
        return await q.edit_message_text("⛔ admin only")
    action, tid = q.data.split(":", 1)
    if action in ("rpromote_fx", "rreject_fx"):                # FX-hunt candidate (separate queue)
        actor = f"tg:{q.message.chat.id}"
        if action == "rpromote_fx":
            hit = rq.approve(tid, actor=actor, path=FX_RESEARCH)
            audit("fx.research.promote", {"id": tid, "genome": hit.get("genome") if hit else None}, actor=actor)
            return await q.edit_message_text(
                f"🚀 {tid} approved (FX, audit-logged). Add its genome to the FX shadow basket."
                if hit else "already handled")
        rq.reject(tid, actor=actor, path=FX_RESEARCH)
        audit("fx.research.reject", {"id": tid}, actor=actor)
        return await q.edit_message_text(f"🗑 {tid} rejected (FX, audit-logged)")
    if action in ("rpromote", "rreject"):                      # research-loop candidate
        actor = f"tg:{q.message.chat.id}"
        if action == "rpromote":
            hit = rq.approve(tid, actor=actor)
            audit("research.promote", {"id": tid, "genome": hit.get("genome") if hit else None}, actor=actor)
            return await q.edit_message_text(
                f"🚀 {tid} approved (audit-logged). Recorded for shadow — add its genome to the basket to "
                f"go live in paper." if hit else "already handled")
        rq.reject(tid, actor=actor)
        audit("research.reject", {"id": tid}, actor=actor)
        return await q.edit_message_text(f"🗑 {tid} rejected (audit-logged)")
    approved = action == "promote"                             # original analyst-loop proposal
    # THE human gate: whitelist-checked apply of the pending proposal (graph/ LangGraph variant
    # was removed 2026-07-10 — it never ran; this direct path was always the live one).
    rt.apply_pending(tid, approved=approved)
    await q.edit_message_text(("🚀 promoted" if approved else "🗑 rejected") + " (audit-logged)")


async def tweak(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    """/tweak <param> <value> — manual override of a whitelisted param."""
    if not is_admin(u.effective_chat.id):
        return await u.message.reply_text("⛔ admin only")
    if len(c.args) != 2:
        return await u.message.reply_text("usage: /tweak <param> <value>")
    key, val = c.args
    if key not in rt.STRATEGY:
        return await u.message.reply_text(f"unknown param {key}")
    try:
        new = type(rt.STRATEGY[key])(val)
    except ValueError:
        return await u.message.reply_text("bad value type")
    old = rt.STRATEGY[key]; rt.STRATEGY[key] = new
    audit("tweak.manual", {"param": key, "from": old, "to": new}, actor=f"tg:{u.effective_chat.id}")
    await u.message.reply_text(f"set {key}: {old} → {new}")


async def feedback(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    """/feedback <up|down> [note] — human reward signal fed to the Critic."""
    if not is_observer(u.effective_chat.id):
        return
    rating = c.args[0] if c.args else "?"
    audit("human.feedback", {"rating": rating, "note": " ".join(c.args[1:])},
          actor=f"tg:{u.effective_chat.id}")
    await u.message.reply_text("thanks — logged as a human reward signal 👍")


async def kill(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(u.effective_chat.id):
        return
    kill_switch.activate(actor=f"tg:{u.effective_chat.id}", reason="manual /kill")
    await u.message.reply_text("🛑 KILL-SWITCH ON. Loop halted.")


async def reset(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(u.effective_chat.id):
        return
    kill_switch.reset(actor=f"tg:{u.effective_chat.id}")
    await u.message.reply_text("✅ kill-switch reset (loop may resume on next signal)")


def build_bot() -> Application:
    app = ApplicationBuilder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    for cmd, fn in [("status", status), ("kpis", kpis), ("shadow", shadow), ("analyst", analyst),
                    ("research", research), ("approve", approve), ("candidates", candidates),
                    ("fxcandidates", fxcandidates), ("fxshadow", fxshadow),
                    ("tweak", tweak), ("feedback", feedback), ("kill", kill), ("reset", reset)]:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    return app


def _clear_webhook() -> None:
    """Polling and an active webhook fight over the same token — commands arrive late."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    import urllib.request
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/deleteWebhook?drop_pending_updates=true",
            timeout=10,
        )
    except Exception:
        pass


if __name__ == "__main__":
    _clear_webhook()
    # Two pollers on the same token causes Conflict errors and multi-minute delays.
    build_bot().run_polling(drop_pending_updates=True)
