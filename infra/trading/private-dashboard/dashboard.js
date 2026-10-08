const byId = id => document.getElementById(id);
const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
};
const usd = value => typeof value === "number" && Number.isFinite(value) ? new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: value !== 0 && Math.abs(value) < .01 ? 4 : 2,
}).format(value) : "Unverified";
const date = value => value ? new Date(value).toLocaleString(undefined, {year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"}) : "Unverified";
const percent = value => typeof value === "number" ? (value * 100).toFixed(2) + "%" : "Unverified";
const names = {baseline: "Baseline", kelly: "Kelly shadow", henry: "Henry chaos"};
const paragraph = (parent, text, className) => parent.append(element("p", text, className));
const rows = pairs => {
  const dl = element("dl");
  pairs.forEach(([label, value]) => { const row = element("div"); row.append(element("dt", label), element("dd", value)); dl.append(row); });
  return dl;
};
const pct = value => typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) + "%" : "n/a";
const price = value => typeof value === "number" && Number.isFinite(value) ? (Math.abs(value) < 1 ? value.toPrecision(4) : value.toFixed(2)) : "n/a";

function renderHenryV2(h) {
  const root = byId("henry-v2");
  if (!h || h.status === "not_deployed") { paragraph(root, "Henry v2 is not deployed yet.", "muted"); return; }
  if (h.status === "unavailable" || h.equity === undefined) { paragraph(root, "Henry v2 snapshot unavailable or invalid. Performance cannot be verified.", "notice"); return; }
  const stale = failed || !h.captureFresh || Date.now() - Date.parse(h.timestamp) > 30_000;
  paragraph(root, `Snapshot ${stale ? "STALE" : "current"} · ${h.frames} observations · ${h.rulesVersion} · started ${date(h.epoch)}`, stale ? "bad" : "ok");
  if (h.halt || h.floorBreached) paragraph(root, h.floorBreached ? `Equity floor breached (${usd(h.floor)}). Henry v2 has stopped permanently.` : `Henry v2 stopped: ${h.halt}`, "notice");
  const grid = element("div", undefined, "grid");
  const card = element("article", undefined, "card");
  card.append(element("h3", "Henry v2"), element("div", "Virtual equity", "label"), element("div", usd(h.equity), "equity"));
  card.append(rows([["Return", pct(h.returnPct)], ["Net P&L", usd(h.netPnl)], ["Peak equity", usd(h.peakEquity)],
    ["Maximum drawdown", pct(h.maxDrawdownPct)], ["Modeled fees", usd(h.fees)], ["Available cash", usd(h.cash)],
    ["Equity floor", usd(h.floor)]]));
  const stats = element("article", undefined, "card");
  stats.append(element("h3", "Trade quality"));
  stats.append(rows([["Closed / pressed", `${h.closedTrades ?? "n/a"} / ${h.pressedTrades ?? "n/a"}`],
    ["Win rate", pct(h.winRatePct)], ["Average win", usd(h.avgWin)], ["Average loss", usd(h.avgLoss)],
    ["Payoff ratio", typeof h.payoffRatio === "number" ? h.payoffRatio.toFixed(2) : "n/a"],
    ["Best / worst", `${usd(h.bestTrade)} / ${usd(h.worstTrade)}`]]));
  const live = element("article", undefined, "card");
  live.append(element("h3", "Position"));
  if (h.position) {
    live.append(rows([["Symbol", h.position.symbol], ["Strategy", h.position.strategy], ["Average price", price(h.position.avgPrice)],
      ["Trailing stop", price(h.position.stop)], ["High-water mark", price(h.position.highWater)],
      ["Pressed", h.position.pressed ? "yes" : "no"]]));
  } else paragraph(live, h.pending ? "Order pending at the next observed quote." : "Flat. Waiting for a signal.", "muted");
  if (h.lastDecision) paragraph(live, `Last decision: ${h.lastDecision}`, "muted");
  if (h.recentTrades?.length) {
    const list = element("ul");
    h.recentTrades.slice().reverse().forEach(t => list.append(element("li", `${t.symbol} · ${t.strategy} · ${usd(t.pnl)}${t.pressed ? " · pressed" : ""}`)));
    live.append(list);
  }
  grid.append(card, stats, live);
  root.append(grid);
}

let latest;
let failed = false;
let loading = false;

function render() {
  if (!latest) return;
  const expanded = new Set([...document.querySelectorAll("details[open]")].map(node => node.dataset.book));
  const e = latest.experiment, r = latest.runtime;
  const stale = failed || !e.timestamp || Date.now() - Date.parse(e.timestamp) > 30_000 || Date.parse(e.timestamp) > Date.now();
  for (const id of ["study-period", "freshness", "books", "evidence", "costs", "source", "henry-v2"]) byId(id).replaceChildren();
  renderHenryV2(latest.henryV2);
  if (!e.books) {
    paragraph(byId("freshness"), "The experiment snapshot is unavailable or invalid. Performance cannot be verified.", "notice");
  } else {
    paragraph(byId("study-period"), `Start ${date(e.epoch)} · End ${date(e.end)}`, "mono");
    paragraph(byId("study-period"), `Paper candidates: ${(e.symbols ?? []).join(", ")}. Each entry must pass the existing cost, freshness and risk checks.`);
    paragraph(byId("freshness"), `Snapshot: ${stale || !e.captureFresh ? "STALE" : "current"} · Portfolio valuation: ${e.marksFresh ? "known" : "STALE"} · ${e.frames ?? "Unknown"} observations`, stale || !e.marksFresh ? "bad" : "ok");
    if (e.quoteStatus) paragraph(byId("freshness"), e.quoteStatus.map(q => `${q.symbol}: ${q.fresh ? "fresh" : "stale / missing"}${q.historyAvailable === false ? " (signal history unavailable)" : ""}`).join(" · "));
    paragraph(byId("freshness"), `Observed ${date(e.timestamp)} · Source: ${e.source}`, "muted");
    if (stale) paragraph(byId("freshness"), "The last values below are stale. Current balances and health are unverified.", "notice");
    if (!e.marksFresh) paragraph(byId("freshness"), "Some provider quotes are stale and remain ineligible for execution.", "notice");
    if (e.halt) paragraph(byId("freshness"), `Experiment stopped: ${e.halt}`, "notice");
    for (const book of e.books) {
      const card = element("article", undefined, "card");
      card.append(element("h3", names[book.name]), element("div", "Virtual equity", "label"), element("div", usd(book.equity), "equity"));
      card.append(rows([["Net marked P&L", usd(book.netPnl)], ["Realized P&L", usd(book.realizedPnl)],
        ["Unrealized P&L", usd(book.unrealizedPnl)], ["Modeled trading fees", usd(book.fees)],
        ["Exposure", usd(book.exposure)], ["Maximum drawdown", percent(book.drawdown)],
        ["Closed / skipped", `${book.closedTrades} / ${book.skips}`],
        ["After allocated expense estimate", usd(book.afterOperatingEstimate)]]));
      const details = element("details");
      details.dataset.book = book.name; details.open = expanded.has(book.name);
      details.append(element("summary", book.halt || (book.dailyHalt ? "Daily loss pause" : "Policy details")));
      paragraph(details, `${book.policyVersion} · Available cash ${usd(book.cash)} · Fee escrow ${usd(book.feeEscrow)}`);
      paragraph(details, book.provisionalFees ? "Modeled fees await virtual settlement." : "No pending modeled fees.");
      if (book.name === "henry") paragraph(details, "One position may use nearly all of this fictional bankroll. No 20% drawdown halt or replenishment.");
      if (book.skipReasons.length) paragraph(details, `Skip reasons: ${book.skipReasons.join("; ")}`);
      if (book.safetyEvents.length) paragraph(details, `Safety events: ${book.safetyEvents.join("; ")}`);
      card.append(details); byId("books").append(card);
    }
    paragraph(byId("evidence"), `${e.evidenceBlocks} / 30 usable synchronized daily blocks. Missing or negative evidence means cash.`);
    const q = e.evidenceQuality;
    if (q) {
      paragraph(byId("evidence"), `${e.evidencePolicy} · ${q.day}: ${q.completeSoFar ? "eligible so far; awaiting full day and settlement" : "excluded so far — " + q.invalidReasons.join("; ")}`);
      paragraph(byId("evidence"), `First full UTC day: ${q.firstFullDay ? q.firstFullDay.slice(0, 10) : "Unverified"}. ${q.legacyBlocks} earlier blocks kept separately; ${q.missingDays} missing full days.`);
      paragraph(byId("evidence"), `${q.observations} observations today · all candidate quotes fresh ${percent(q.freshPairFraction)} · ${q.gapsOver60} gaps over 60 seconds.`);
      const list = element("ul");
      q.staleByAsset.forEach(s => list.append(element("li", `${s.symbol}: ${s.count} stale observations (${s.heldCount} with Baseline inventory); oldest quote ${s.maximumAge.toFixed(1)}s.`)));
      byId("evidence").append(list);
      paragraph(byId("evidence"), `Baseline valuation: ${q.valuationKnown ? "known under the evidence rules" : "missing a fresh held-inventory mark"}. Coverage is not proof of profitable trading.`, "muted");
    }
    paragraph(byId("costs"), `Shared estimate since the comparison began: ${usd(e.sharedOperatingEstimate)}. Verified actual: ${usd(e.actualSharedCost)}.`);
    paragraph(byId("costs"), "One third is attributed to each book for comparison. The expense is counted once across the experiment and does not change trading cash.", "muted");
  }
  if (r.equity === undefined) paragraph(byId("source"), "Broker demo snapshot unavailable or invalid.", "notice");
  else {
    paragraph(byId("source"), `DEMO equity ${usd(r.equity)} · Cash ${usd(r.cash)}. This account is separate from the three fictional books.`);
    const sourceStale = failed || !r.captureFresh || Date.now() - Date.parse(r.timestamp) > 30_000;
    paragraph(byId("source"), `Snapshot ${sourceStale ? "STALE" : "current"} · Overall runtime status ${r.status} · Updated ${date(r.timestamp)}`, sourceStale || r.status !== "current" ? "bad" : "ok");
    paragraph(byId("source"), `Realized P&L ${usd(r.realizedPnl)} · Unrealized P&L ${usd(r.unrealizedPnl)} · After estimated operating costs ${usd(r.afterOperatingCosts)}.`);
    paragraph(byId("source"), `Positions ${r.positionCount} · Pending orders ${r.pendingOrderCount} · Strategy round trips ${r.strategyTrades ?? "unverified"}.`);
    paragraph(byId("source"), `Supervisor: ${r.supervisor}. Entry window ${r.entrySessionOpen ? "open" : "closed"}.`);
    if (r.entryPause || r.halt) paragraph(byId("source"), r.entryPause || r.halt, "notice");
    if (r.feesProvisional) paragraph(byId("source"), "Broker fee accounting remains provisional.", "notice");
    paragraph(byId("source"), r.studyStarted ? `Broker study began ${date(r.studyStarted)}.` : "The broker study has not been started. The virtual comparison has its own fixed epoch.", "muted");
  }
}

async function refresh() {
  if (loading || document.hidden) return;
  loading = true;
  try {
    const response = await fetch("/api/status", {cache: "no-store", signal: AbortSignal.timeout(8000)});
    if (!response.ok) {
      if (response.status === 403) { latest = undefined; for (const id of ["study-period", "freshness", "books", "evidence", "costs", "source", "henry-v2"]) byId(id).replaceChildren(); }
      throw new Error("Unavailable");
    }
    latest = await response.json(); failed = false;
    byId("connection").textContent = `Private connection · Last refresh ${new Date().toLocaleTimeString()}`;
  } catch {
    failed = true;
    byId("connection").textContent = "Connection unavailable. Confirm Tailscale is connected; any retained values below are stale.";
  } finally { loading = false; render(); }
}
setInterval(refresh, 10_000);
setInterval(render, 5_000);
document.addEventListener("visibilitychange", () => { render(); if (!document.hidden) refresh(); });
refresh();
