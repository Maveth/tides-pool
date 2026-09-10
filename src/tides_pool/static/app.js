function quarantineBadge(c) {
  if (!c || !c.quarantined) return "";
  const tip = (c.quarantine_reason || "coinbase mismatch / reject-27")
    .replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  return ` <span class="badge-quarantine" title="${tip}">⚠ quarantined</span>`;
}

/** Coinbase size-class health (hourly): ⚠ if type-2 share of multi-out accepts ≥ threshold %. */
function cbTypeBadge(c) {
  if (!c) return "";
  const st = (c.cb_type_status || "").toLowerCase();
  // data-tip + tip-pin: click to keep tip open for screenshots (native title vanishes on key/click).
  if (!st || st === "unknown") {
    const tip = escapeHtml(
      c.cb_type_tip ||
        "No recent accepted multi-out share type yet — Gateway coinbase class unknown."
    );
    return ` <span class="cb-type-ico cb-type-unk tip-pin" data-tip="${tip}" role="button" tabindex="0" aria-label="${tip}">?</span>`;
  }
  if (st === "ok") {
    const tip = escapeHtml(
      c.cb_type_tip ||
        "Type-2 share of multi-out accepts is below the warn threshold — OK."
    );
    return ` <span class="cb-type-ico cb-type-ok tip-pin" data-tip="${tip}" role="button" tabindex="0" aria-label="${tip}">✓</span>`;
  }
  if (st === "warn") {
    const tip = escapeHtml(
      c.cb_type_tip ||
        "Type-2 (truncate-class) is a high % of multi-out accepts — DATUM may truncate the pool split."
    );
    return ` <span class="cb-type-ico cb-type-warn tip-pin" data-tip="${tip}" role="button" tabindex="0" aria-label="${tip}">⚠</span>`;
  }
  return "";
}

function fmtSnapAge(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

/** Snapshot freshness (lab / cached dash). Live :8088 has no snapshot_as_of. */
async function refreshSnapFreshness(health) {
  const header = document.getElementById("snapFreshness");
  const inline = document.getElementById("snapFreshnessInline");
  let asOf = health && health.snapshot_as_of;
  const isLab = !!(health && health.lab_website);
  if (!asOf && isLab) {
    try {
      const meta = await jget("/api/meta");
      asOf = meta && meta.as_of;
    } catch (_) {
      /* ignore */
    }
  }
  if (!asOf) {
    if (header) header.hidden = true;
    if (inline) inline.textContent = "";
    return;
  }
  const t = Date.parse(asOf);
  const ageSec = Number.isFinite(t) ? (Date.now() - t) / 1000 : null;
  const ageTxt = ageSec != null ? fmtSnapAge(ageSec) : "unknown age";
  const tip =
    "Top cards, blocks, coinbaser: snap-first (instant), live cache refreshes in background ~10s. " +
    "Gateway class: live overlay. Contributors / charts / miner pages: snapshot (~5 min, or right after a new pool find). " +
    "How-to / connect: always current. " +
    `Last contrib snap: ${asOf}`;
  const short = `snap ${ageTxt} · cards cached`;
  if (header) {
    header.hidden = false;
    header.textContent = short;
    header.title = tip;
    header.setAttribute("data-asof", asOf);
  }
  if (inline) {
    inline.innerHTML = ` · <span title="${tip.replace(/"/g, "&quot;")}">${short}</span>`;
  }
}

/** Header status chip from /health (Prime / coinbaser / RPC). */
async function refreshHealthStrip() {
  const el = document.getElementById("healthStrip");
  if (!el) return;
  try {
    const h = await jget("/api/health");
    const st = (h && h.status) || "ok";
    const cb = (h && h.checks && h.checks.coinbaser) || {};
    const gw = (h && h.checks && h.checks.gateway_sessions) || 0;
    const outs = cb.last_outs;
    const age = cb.cache_age_s;
    const manual = (h && h.checks && h.checks.manual_payouts_pending) || 0;
    const warn = (h && h.warnings) || [];
    let label = "🟢 ok";
    if (st === "degraded") label = "🟡 degraded";
    else if (st === "down") label = "🔴 down";
    const bits = [];
    bits.push(`GW ${gw}`);
    if (outs != null) bits.push(`outs ${outs}`);
    if (age != null) bits.push(`cache ${age}s`);
    if (manual) bits.push(`manual ${manual}`);
    el.textContent = `${label} · ${bits.join(" · ")}`;
    el.className =
      "health-strip " +
      (st === "ok"
        ? "health-ok"
        : st === "degraded"
          ? "health-degraded"
          : st === "down"
            ? "health-down"
            : "health-unknown");
    const tip = warn.length
      ? warn.join("; ")
      : "Prime / coinbaser / RPC health — click for JSON";
    el.title = tip;
    refreshSnapFreshness(h).catch((e) => console.error("snap freshness", e));
  } catch (e) {
    el.textContent = "🔴 health unreachable";
    el.className = "health-strip health-down";
    el.title = String(e && e.message ? e.message : e);
  }
}

/**
 * Green = hashing now (~10m HR).
 * Yellow = work on this unfinished block, but quiet lately.
 * Red = in payout window, but no work on this block (older finds only).
 */
function activityDot(c) {
  const live =
    (c && c.activity === "live") ||
    (c && Number(c.hashrate_hs || 0) > 0);
  const thisBlock = Number((c && c.work_current) || 0) > 0;
  // data-tip + tip-pin: click to keep tip open for screenshots (native title vanishes on key/click).
  if (live) {
    const tip = "Hashing now (shares in the last ~10 minutes)";
    return `<span class="activity-dot live tip-pin" data-tip="${tip}" role="button" tabindex="0" aria-label="${tip}"></span>`;
  }
  if (thisBlock) {
    const tip = "Work on this block, but no shares in the last ~10 minutes";
    return `<span class="activity-dot idle tip-pin" data-tip="${tip}" role="button" tabindex="0" aria-label="${tip}"></span>`;
  }
  const tip = "In the payout window, but no work on this block";
  return `<span class="activity-dot offline tip-pin" data-tip="${tip}" role="button" tabindex="0" aria-label="${tip}"></span>`;
}

/** Last pool-find era with shares: CURRENT or "N ago" (dilution ages out). */
function lastShareLabel(c) {
  const ago = Number(c && c.last_share_blocks_ago);
  if (!Number.isFinite(ago) || ago <= 0) return "CURRENT";
  return ago === 1 ? "1 ago" : `${ago} ago`;
}

function lastShareTitle(c) {
  const ago = Number(c && c.last_share_blocks_ago);
  const h = c && c.last_share_block_height;
  if (!Number.isFinite(ago) || ago <= 0) {
    return "Still has shares on the unfinished current block";
  }
  const heightBit = h != null ? ` (find #${h})` : "";
  return (
    `Last shares were during a pool find ${ago} confirmed block(s) ago${heightBit}. ` +
    `Older work drops out of the payout window as new finds confirm — less dilution for active miners.`
  );
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString();
}

/** User-facing amounts are always BTC. Optional sats in title= via fmtBtcTitle. */
function fmtBtc(sats) {
  const n = Number(sats || 0);
  if (!Number.isFinite(n)) return "—";
  let text = (n / 1e8).toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
  if (text === "-0") text = "0";
  return text + " BTC";
}

function fmtBtcTitle(sats) {
  return fmtInt(sats) + " sats";
}

/** @deprecated use fmtBtc — kept as alias so stray callers stay BTC-only */
function fmtSats(sats) {
  return fmtBtc(sats);
}

function fmtAge(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 45) return "<1m";
  if (n < 3600) return Math.max(1, Math.round(n / 60)) + "m";
  if (n < 86400) {
    const h = n / 3600;
    return (h < 10 ? h.toFixed(1) : String(Math.round(h))) + "h";
  }
  const d = n / 86400;
  return (d < 10 ? d.toFixed(1) : String(Math.round(d))) + "d";
}

/** Short timezone label for the visitor (e.g. MDT, EDT, GMT+2). */
function localTzLabel(d = new Date()) {
  try {
    const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(d);
    const tz = parts.find((p) => p.type === "timeZoneName");
    return (tz && tz.value) || "";
  } catch (_) {
    return "";
  }
}

/**
 * Format an ISO/API timestamp in the visitor's local timezone.
 * Example: "Sep 2, 5:23:16 PM MDT"
 */
function fmtLocalTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "—";
  const core = d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
  const tz = localTzLabel(d);
  return tz ? `${core} ${tz}` : core;
}

function fmtHashrate(hs) {
  const n = Number(hs || 0);
  if (n <= 0) return "—";
  if (n >= 1e18) return (n / 1e18).toFixed(2) + " EH/s";
  if (n >= 1e15) return (n / 1e15).toFixed(2) + " PH/s";
  if (n >= 1e12) return (n / 1e12).toFixed(2) + " TH/s";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GH/s";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + " MH/s";
  if (n >= 1e3) return (n / 1e3).toFixed(2) + " kH/s";
  return n.toFixed(0) + " H/s";
}

/** Network tooltip value in PH/s. */
function fmtHashratePH(hs) {
  const n = Number(hs || 0);
  if (n <= 0) return "—";
  const ph = n / 1e15;
  const s = ph >= 10 ? ph.toFixed(1) : ph.toFixed(2);
  return s.replace(/\.?0+$/, "") + " PH/s";
}

/** Miner tooltip value in TH/s. */
function fmtHashrateTH(hs) {
  const n = Number(hs || 0);
  if (n <= 0) return "—";
  const th = n / 1e12;
  if (th >= 100) return Math.round(th) + " TH/s";
  const s = th >= 10 ? th.toFixed(1) : th.toFixed(2);
  return s.replace(/\.?0+$/, "") + " TH/s";
}

/** Y-axis tick: TH number only (unit lives in axis title). */
function fmtAxisTH(hs) {
  const th = Number(hs || 0) / 1e12;
  if (!Number.isFinite(th) || th <= 0) return "0";
  if (th >= 100) return String(Math.round(th));
  const s = th >= 10 ? th.toFixed(1) : th.toFixed(2);
  return s.replace(/\.?0+$/, "");
}

/** Y-axis tick: PH number only (unit lives in axis title). Prefer whole PH. */
function fmtAxisPH(hs) {
  const ph = Number(hs || 0) / 1e15;
  if (!Number.isFinite(ph) || ph <= 0) return "0";
  return String(Math.round(ph));
}

/** Expected duration (e.g. est. time to find a block). */
function fmtDuration(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n < 60) return Math.max(1, Math.round(n)) + "s";
  if (n < 3600) return (n / 60).toFixed(n < 600 ? 1 : 0) + "m";
  if (n < 86400) return (n / 3600).toFixed(n < 36000 ? 1 : 0) + "h";
  if (n < 86400 * 90) return (n / 86400).toFixed(1) + "d";
  return (n / (86400 * 30)).toFixed(1) + "mo";
}

function fmtSharePct(pct) {
  const n = Number(pct);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n >= 1) return n.toFixed(2) + "%";
  if (n >= 0.01) return n.toFixed(3) + "%";
  if (n >= 0.0001) return n.toFixed(4) + "%";
  return n.toExponential(2) + "%";
}

function shortAddr(a) {
  if (!a) return "—";
  if (a.length <= 16) return a;
  return a.slice(0, 8) + "…" + a.slice(-6);
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Truncated table cell; full text on hover via title. */
function clipCell(text, { title = "", wide = false, mono = false } = {}) {
  const raw = (text == null ? "" : String(text)).trim();
  if (!raw || raw === "—") {
    return `<td class="${wide ? "clip-wide" : "clip"}"><span class="muted">—</span></td>`;
  }
  const tip = title || raw;
  const cls = [wide ? "clip-wide" : "clip", mono ? "mono" : ""].filter(Boolean).join(" ");
  // Inner span is required: td max-width is ignored under table-layout:auto
  // when content is a long unbroken string (nicknames / workers).
  return `<td class="${cls}" title="${escapeHtml(tip)}"><span class="clip-text">${escapeHtml(raw)}</span></td>`;
}

function mempoolBase(info) {
  let u =
    (info && info.mempool_explorer_url) ||
    window.MEMPOOL_URL ||
    "https://mempool.guide";
  u = String(u);
  if (u.includes("mempool.maveth.ca")) u = "https://mempool.guide";
  return u.replace(/\/$/, "");
}

function mempoolBlockHref(b, info) {
  const base = mempoolBase(info);
  const hash = b && b.block_hash ? String(b.block_hash) : "";
  if (hash && !hash.startsWith("pool-") && /^[0-9a-fA-F]{64}$/.test(hash)) {
    return base + "/block/" + hash;
  }
  return base + "/block/" + b.height;
}


function qs(name) {
  return new URLSearchParams(location.search).get(name);
}

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}

async function jgetRes(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " " + r.status);
  return { data: await r.json(), headers: r.headers };
}

/** Lab/prod improvement: contributors page size (was 50). */
// Snapshot is cheap — load the full window and collapse idle rows (no pager).
const CONTRIB_PAGE_SIZE = 500;
let contribPage = 0; // unused (kept for compat)
let contribTotal = 0;

function fmtLuckPct(v) {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  const s = n >= 10 ? n.toFixed(0) : n.toFixed(1);
  return `${s}% luck`;
}

function card(label, value, mono, note) {
  const noteHtml = note
    ? `<div class="card-note">${note}</div>`
    : "";
  return `<div class="card"><div class="label">${label}</div><div class="value${mono ? " mono" : ""}">${value}</div>${noteHtml}</div>`;
}

/** Compact multi-stat card value: pairs of (n, k) then optional title.
 *  e.g. cardSplitValue(a,"24h", b,"1wk", c,"all", "tooltip")
 */
function cardSplitValue(...args) {
  let title = "";
  const vals = args.slice();
  // Trailing title when arg count is odd (2*N pairs + title)
  if (vals.length >= 3 && vals.length % 2 === 1) {
    title = String(vals.pop() ?? "");
  }
  const cells = [];
  for (let i = 0; i + 1 < vals.length; i += 2) {
    cells.push([vals[i], vals[i + 1]]);
  }
  const tip = title ? ` title="${String(title).replace(/"/g, "&quot;")}"` : "";
  const parts = [];
  cells.forEach(([n, k], idx) => {
    if (idx) parts.push(`<div class="split-sep" aria-hidden="true"></div>`);
    parts.push(
      `<div class="split-cell"><span class="split-n">${n}</span><span class="split-k">${k}</span></div>`
    );
  });
  return `<div class="card-split"${tip}>${parts.join("")}</div>`;
}

function bpsPct(bps) {
  const n = Number(bps || 0);
  // Show one decimal only when needed (e.g. 12.5%)
  const pct = n / 100;
  return (Number.isInteger(pct) ? String(pct) : pct.toFixed(1)) + "%";
}

function sv1WorkFeePct(stats) {
  // Prefer live meta.runtime_fees (via /api/stats); fallback 1%.
  const bps = Number(
    stats && (stats.local_work_fee_bps ?? stats.local_work_fee_pct * 100)
  );
  if (Number.isFinite(bps) && bps >= 0) return bps / 100;
  return 1;
}

function renderSv1FeeCopy(stats) {
  const pct = sv1WorkFeePct(stats);
  const pctTxt = Number.isInteger(pct) ? String(pct) : pct.toFixed(1);
  const label = `variable (currently ${pctTxt}%)`;
  const labelStrong = `variable work fee (currently ${pctTxt}%)`;
  // Header promo + howto — filled from meta.runtime_fees via /api/stats.
  document.querySelectorAll("[data-sv1-fee-label]").forEach((el) => {
    el.textContent = label;
  });
  document.querySelectorAll("[data-sv1-fee-pct]").forEach((el) => {
    el.textContent = `${pctTxt}%`;
  });
  document.querySelectorAll("[data-sv1-fee-strong]").forEach((el) => {
    el.innerHTML = `<strong>${labelStrong}</strong>`;
  });
  document.querySelectorAll("[data-sv1-fee-short]").forEach((el) => {
    el.textContent = `fee currently ${pctTxt}%`;
  });
  const promo = document.querySelector(".fee-promo");
  if (promo) {
    promo.title =
      `DATUM / own Gateway: 0% coinbaser fee. Pool SV1 stratum: variable work fee (currently ${pctTxt}%); ` +
      `half of that skim goes to live miners as STRATUM FEE.`;
  }
}

function renderFeeFootnote(stats) {
  // Single location for fee / finder / window copy (coinbaser panel stays short).
  const el = document.getElementById("feeFootnote");
  if (!el) return;
  renderSv1FeeCopy(stats);
  const fee = Number(stats.fee_bps ?? 0);
  const sv1Pct = sv1WorkFeePct(stats);
  const sv1Txt = Number.isInteger(sv1Pct) ? String(sv1Pct) : sv1Pct.toFixed(1);
  const windowBlocks = Number(stats.window_blocks ?? 8);
  const paidInWindow = Math.max(windowBlocks - 1, 0);
  const mode = stats.window_mode || "pool_finds";
  const confFinds = Number(stats.window_confirmed_finds ?? 0);
  const windowLabel =
    mode === "pool_finds"
      ? `Payout window = <strong>${paidInWindow} confirmed finds + current</strong>` +
        ` (cutoff = ${windowBlocks}th-last confirmed; orphans excluded` +
        (confFinds ? `; have ${confFinds}` : "") +
        `)`
      : `Payout window ≈ <strong>${windowBlocks}×</strong> network difficulty`;
  if (fee <= 0) {
    el.innerHTML =
      `<strong>Fees:</strong> <strong>0%</strong> for DATUM / own-Gateway miners — coinbase pays window work only (no ops cut).<br />` +
      `<strong>Pool SV1</strong> (<code>sv1.riptide.maveth.ca:23337</code>, <strong>strongly discouraged</strong>): ` +
      `<span style="color:#e85d5d;font-weight:700">variable work fee (currently ${sv1Txt}%)</span> on that path only — ` +
      `half of the skim → live miners as <code>STRATUM FEE</code>, half → ops as <code>OPERATION FEE</code> (not a coinbaser cut).<br />` +
      `${windowLabel}. <strong>Payout weight</strong> = sum of share difficulties (work), not share count. ` +
      `<strong>~H/s</strong> is estimated from recent work.`;
    return;
  }
  const finderShare = Number(stats.finder_fee_share_bps ?? 8000);
  const finderOfBlock = Math.floor((fee * finderShare) / 10000);
  const opsOfBlock = fee - finderOfBlock;
  el.innerHTML =
    `<strong>Fees:</strong> <strong>${bpsPct(fee)}</strong> of each block · ` +
    `<strong>${bpsPct(finderShare)}</strong> of that fee → previous finder on the <em>next</em> block · ` +
    `ops keep <strong>${bpsPct(opsOfBlock)}</strong> of the block.<br />` +
    `${windowLabel}. <strong>Payout weight</strong> = sum of share difficulties (work), not share count. ` +
    `<strong>~H/s</strong> is estimated from recent work.`;
}

function blockStatusBadge(b, info) {
  const st = (b && b.status) || "confirmed";
  if (st === "orphaned" || st === "misattributed") {
    const why = b.orphan_reason ? ` — ${b.orphan_reason}` : "";
    return `<span class="badge badge-orphan" title="Not on tip / no payout${why}">orphaned</span>`;
  }
  // Chain-pending must not hide payout/review badges — show both when ops_manual / adj exists.
  const confirming =
    st === "pending"
      ? `<span class="badge badge-pending" title="Waiting for chain confirmations">pending</span> `
      : "";
  const mode = (b && b.payout_mode) || "onchain_split";
  if (mode === "needs_review") {
    const done = !!(b && b.manual_payout_done);
    const note =
      (b && b.manual_payout_note) ||
      "Intended payout ≠ on-chain coinbase (not drift) — ops should review";
    const nOut = Array.isArray(b && b.intended_payout) ? b.intended_payout.length : 0;
    const extra = nOut ? ` · snapshot ${nOut} line(s)` : "";
    if (done) {
      return `${confirming}<span class="badge badge-review-done" title="${escapeHtml(note)}${extra}">review done</span>`;
    }
    return `${confirming}<span class="badge badge-review" title="${escapeHtml(note)}${extra}">review</span>`;
  }
  if (mode === "ops_manual" || (b && b.manual_adjustment)) {
    const done = !!(b && b.manual_payout_done);
    const adj = b && b.manual_adjustment;
    const note =
      (b && b.manual_payout_note) ||
      "Coinbase was ops-only; ops will pay miners manually";
    const nOut = Array.isArray(b && b.intended_payout) ? b.intended_payout.length : 0;
    const extra = nOut ? ` · snapshot ${nOut} line(s)` : "";
    // LISTED_ONLY / cross-user top-up: expandable table when adj present (pending OR paid)
    if (adj && Array.isArray(adj.pays) && adj.pays.length) {
      return confirming + manualAdjustmentBadge(adj, note, info);
    }
    if (done) {
      return `${confirming}<span class="badge badge-manual-done" title="${escapeHtml(note)}${extra}">manual paid</span>`;
    }
    if (mode === "ops_manual") {
      return `${confirming}<span class="badge badge-manual" title="${escapeHtml(note)}${extra}">manual adjustment</span>`;
    }
  }
  if (st === "pending") {
    return `<span class="badge badge-pending" title="Waiting for chain confirmations">pending</span>`;
  }
  return `<span class="badge badge-ok">confirmed</span>`;
}

function manualAdjustmentBadge(adj, note, info) {
  // Click-to-expand (not hover) — keeps page light; table only built when opened.
  const n = (adj.pays || []).length;
  const owed = Number(adj.total_owed_sats || 0);
  const h = adj.height != null ? String(adj.height) : "x";
  const id = `adj-${h}-${n}`;
  const paid = !!(adj && (adj.txid || adj.total_paid_sats));
  const st = String((adj && adj.status) || "").toLowerCase();
  const matureH = Number(adj && adj.mature_height);
  const maturityHold =
    !paid &&
    (st === "pending_maturity" ||
      st === "hold_maturity" ||
      (Number.isFinite(matureH) && matureH > 0 && !adj.txid));
  let label = "manual adjustment";
  if (paid) label = "manual paid";
  else if (maturityHold) label = "manual adj · maturity";
  else if (st === "pending" || !paid) label = "manual adjustment";
  const tip = escapeHtml(
    (adj.title || "Ops adjustment") +
      (owed ? ` · ${owed.toLocaleString()} sats · ${n} payees` : ` · ${n} payees`) +
      (maturityHold && Number.isFinite(matureH)
        ? ` · HOLD until coinbase maturity @ ${matureH}`
        : paid
          ? ""
          : " · PENDING") +
      " — click to show"
  );
  return `<button type="button" class="badge badge-manual-adj adj-btn${maturityHold ? " adj-maturity" : ""}" data-adj-id="${escapeHtml(id)}" data-adj-height="${escapeHtml(h)}" title="${tip}">${label}</button>`;
}

function bindManualAdjustmentClicks(blocks, info) {
  const byH = {};
  (blocks || []).forEach((b) => {
    if (b && b.manual_adjustment) byH[String(b.height)] = b;
  });
  document.querySelectorAll(".adj-btn[data-adj-id]").forEach((btn) => {
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const tr = btn.closest("tr");
      if (!tr) return;
      const existing = tr.parentElement && tr.parentElement.querySelector(
        `tr.adj-detail[data-for="${btn.getAttribute("data-adj-id")}"]`
      );
      if (existing) {
        existing.remove();
        btn.classList.remove("open");
        return;
      }
      // close other open adj rows in same tbody
      const tbody = tr.parentElement;
      if (tbody) {
        tbody.querySelectorAll("tr.adj-detail").forEach((r) => r.remove());
        tbody.querySelectorAll(".adj-btn.open").forEach((b) => b.classList.remove("open"));
      }
      const h = btn.getAttribute("data-adj-height") || "";
      const b = byH[h];
      const adj = (b && b.manual_adjustment) || null;
      if (!adj || !Array.isArray(adj.pays)) return;
      const pays = adj.pays;
      const title = adj.title || "Ops adjustment";
      const owed = Number(adj.total_owed_sats || 0);
      const paid = Number(adj.total_paid_sats || 0);
      const matureH = Number(adj.mature_height);
      const st = String(adj.status || "").toLowerCase();
      const holdMaturity =
        !(adj.txid || adj.total_paid_sats) &&
        (st === "pending_maturity" ||
          st === "hold_maturity" ||
          (Number.isFinite(matureH) && matureH > 0));
      const maturityLine = holdMaturity
        ? `<div class="adj-pop-maturity">⏳ Hold send until coinbase maturity` +
          (Number.isFinite(matureH) ? ` @ <strong>${matureH}</strong>` : "") +
          ` (same unlock as this find’s coinbase / OPS leftover).` +
          (adj.send_policy ? ` ${escapeHtml(String(adj.send_policy))}` : "") +
          `</div>`
        : "";
      // Ops CORRECTION txs only (sendmany or per-payee) — never the find's coinbase.
      const payTxids = [];
      const seenTx = Object.create(null);
      for (const p of pays) {
        const t = String((p && p.txid) || "").trim();
        if (t && !seenTx[t]) {
          seenTx[t] = true;
          payTxids.push(t);
        }
      }
      const topTx = String(adj.txid || "").trim();
      if (topTx && !seenTx[topTx]) payTxids.unshift(topTx);
      const singleTx = payTxids.length === 1 ? payTxids[0] : "";
      const txLink = (txid) => {
        const short =
          txid.length > 20 ? `${txid.slice(0, 12)}…${txid.slice(-8)}` : txid;
        return `<a class="mono" href="${mempoolTxHref(txid, info)}" target="_blank" rel="noopener" title="${escapeHtml(txid)}">${escapeHtml(short)}</a>`;
      };
      let paidFooter = "";
      if (payTxids.length === 1) {
        paidFooter = `<div class="adj-pop-paid">✅ Ops correction · ${txLink(payTxids[0])} <span class="muted">(mempool · not coinbase)</span></div>`;
      } else if (payTxids.length > 1) {
        const links = payTxids
          .map((t, i) => `<div class="adj-pop-txline">${i + 1}. ${txLink(t)}</div>`)
          .join("");
        paidFooter = `<div class="adj-pop-paid">✅ Ops corrections · <strong>${payTxids.length}</strong> separate payments <span class="muted">(not coinbase)</span>${links}</div>`;
      } else if (!holdMaturity) {
        paidFooter = `<div class="adj-pop-pending">⏳ Ops top-up not broadcast yet</div>`;
      }
      // Panel: title + owed/paid/payees + payout table only (no long ops note / RCA prose).
      const rows = pays
        .map((p) => {
          const addr = String(p.address || "");
          const short = addr.length > 14 ? `${addr.slice(0, 8)}…${addr.slice(-6)}` : addr;
          const tx = String(p.txid || singleTx || "");
          const txShort = tx.length > 16 ? `${tx.slice(0, 10)}…${tx.slice(-6)}` : tx;
          const tip = p.note ? ` title="${escapeHtml(p.note)}"` : "";
          const txCell = tx
            ? `<a class="mono" href="${mempoolTxHref(tx, info)}" target="_blank" rel="noopener" title="Ops correction ${escapeHtml(tx)}">${escapeHtml(txShort)}</a>`
            : holdMaturity
              ? `<span class="muted">locked</span>`
              : "—";
          return `<tr${tip}>
            <td class="num">${Number(p.sats || p.pay_sats || 0).toLocaleString()}</td>
            <td class="mono"><a href="/address?a=${encodeURIComponent(addr)}" title="${escapeHtml(addr)}">${escapeHtml(short)}</a></td>
            <td>${txCell}</td>
          </tr>`;
        })
        .join("");
      const detail = document.createElement("tr");
      detail.className = "adj-detail";
      detail.setAttribute("data-for", btn.getAttribute("data-adj-id") || "");
      const colSpan = Math.max(tr.children.length || 0, 5);
      detail.innerHTML = `<td colspan="${colSpan}">
        <div class="adj-panel">
          <div class="adj-pop-title">${escapeHtml(title)}</div>
          <div class="adj-pop-sum">owed ${owed.toLocaleString()} sats · paid ${paid.toLocaleString()} sats · ${pays.length} payees</div>
          ${maturityLine}
          <table class="adj-pop-table">
            <thead><tr><th>Sats</th><th>Address</th><th>Ops correction</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
          ${paidFooter}
        </div>
      </td>`;
      tr.after(detail);
      btn.classList.add("open");
    });
  });
}

function mempoolTxHref(txid, info) {
  const base = mempoolBase(info);
  return `${base}/tx/${encodeURIComponent(txid)}`;
}

function finderBonusSats(rewardEst) {
  // 4% of block = 80% of the 5% fee (matches fee_bps=500, finder_fee_share_bps=8000)
  return Math.floor(Number(rewardEst || 0) * 0.04);
}

function kindCell(o, rewardEst) {
  const k = (o && o.kind) || "—";
  // tides+finder is shown as plain mining share — finder bonuses are off-coinbase (ops manual).
  if (k === "ops") {
    return `<span class="kind-ico kind-ops" title="Pool ops fee keep (see fee footnote)" aria-label="Ops fee">${KIND_ICO.ops}</span>`;
  }
  return `<span class="kind-ico kind-tides" title="TIDES window work share" aria-label="Mining share">${KIND_ICO.pickaxe}</span>`;
}

/** Kind column icons (emoji; title= carries the detail). */
const KIND_ICO = {
  pickaxe: "⛏️",
  trophy: "🏆",
  ops: "⚙️",
};

const COINBASER_TOP_N = 12;
/** Named pie slices: keep anyone ≥ this % (mempool-like); tiny remainder → Other. */
const COINBASER_PIE_MIN_PCT = 0.5;
/** Hard cap so leader-line labels stay readable. */
const COINBASER_PIE_MAX = 18;
const COINBASER_TABLE_KEY = "tides_coinbaser_table_open";
let coinbaserExpanded = false;
let coinbaserLast = null;
let coinbaserPieObj = null;
/** Last pie fingerprint — skip Chart.js destroy/recreate on soft refresh when split is unchanged. */
let coinbaserPieSig = "";
let coinbaserOutlabelsRegistered = false;
let coinbaserTableOpen = (() => {
  try {
    return sessionStorage.getItem(COINBASER_TABLE_KEY) === "1";
  } catch {
    return false;
  }
})();
function saveCoinbaserTableOpen(on) {
  try {
    sessionStorage.setItem(COINBASER_TABLE_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

const COINBASER_PIE_COLORS = [
  "#3dd6c6",
  "#5b8def",
  "#f0a202",
  "#e4572e",
  "#a06cd5",
  "#7bdff2",
  "#f4d35e",
  "#90be6d",
  "#f9844a",
  "#577590",
  "#43aa8b",
  "#f94144",
  "#277da1",
  "#f8961e",
  "#90e0ef",
  "#b5179e",
  "#80ed99",
  "#ff6b6b",
  "#4cc9f0",
  "#ffd166",
];

/** Fee / ops synthetic workers — never use as a display nickname. */
function isFeeOrOpsWorkerName(w) {
  const s = String(w || "")
    .trim()
    .toUpperCase();
  if (!s) return true;
  if (s === "STRATUM FEE" || s === "OPERATION FEE" || s === "OPS") return true;
  if (s.startsWith("OPS_ADJ_") || s.startsWith("OPS ")) return true;
  return false;
}

/** Frontend-only labels (backend worker names unchanged). */
function displayWorkerName(w) {
  const raw = String(w || "").trim();
  const u = raw.toUpperCase();
  if (u === "OPERATION FEE" || u === "OPS") return "Ops fee";
  if (u === "STRATUM FEE") return "Stratum fee share";
  return raw;
}

/** address\\0worker keys for machines that found a non-orphan pool block (DATUM + SV1). */
let finderWorkerKeys = new Set();

function rememberFinderWorkers(blocks) {
  const next = new Set();
  for (const b of blocks || []) {
    const st = String((b && b.status) || "confirmed");
    if (st === "orphaned" || st === "misattributed") continue;
    const addr = String((b && b.finder_address) || "").trim();
    const worker = String((b && b.finder_worker) || "").trim();
    if (!addr || !worker) continue;
    // Skip fee placeholders — not a real mining machine.
    if (isFeeOrOpsWorkerName(worker)) continue;
    next.add(`${addr}\0${worker}`);
  }
  finderWorkerKeys = next;
}

function isFinderWorker(address, worker) {
  const addr = String(address || "").trim();
  const w = String(worker || "").trim();
  if (!addr || !w || isFeeOrOpsWorkerName(w)) return false;
  return finderWorkerKeys.has(`${addr}\0${w}`);
}

/** ★ badge for the stratum machine that submitted the winning share. */
function finderWorkerStarHtml(worker, tip) {
  const wn = displayWorkerName(worker) || worker || "worker";
  const t = tip || `Block finder machine: ${wn}`;
  return `<span class="finder-star tip-pin" data-tip="${escapeHtml(t)}" title="${escapeHtml(t)}" role="img" aria-label="${escapeHtml(t)}">★</span>`;
}

/** Blocks-table cell: always ★ + worker (this row is the find). */
function blocksFinderWorkerCell(b) {
  const raw = String((b && b.finder_worker) || "").trim();
  if (!raw) {
    return `<td class="clip"><span class="muted">—</span></td>`;
  }
  const label = displayWorkerName(raw);
  const h = b && b.height != null ? `#${b.height}` : "a pool block";
  const tip = `Block finder machine: ${raw} found ${h} (DATUM or SV1 stratum worker)`;
  return `<td class="clip mono" title="${escapeHtml(tip)}"><span class="finder-worker-cell">${finderWorkerStarHtml(
    raw,
    tip
  )}<span class="clip-text">${escapeHtml(label)}</span></span></td>`;
}

/** Inline ★ before a worker name when that machine found a recent pool block. */
function workerFinderMark(address, worker) {
  if (!isFinderWorker(address, worker)) return "";
  const raw = String(worker || "").trim();
  return finderWorkerStarHtml(
    raw,
    `Block finder machine: ${raw} found a pool block (recent finds)`
  );
}

/** Dominant mining worker (by work, else shares) — skips STRATUM FEE etc. */
function primaryMiningWorker(workers) {
  const list = (Array.isArray(workers) ? workers : []).filter(
    (w) => w && !isFeeOrOpsWorkerName(w.worker)
  );
  if (!list.length) return "";
  list.sort(
    (a, b) =>
      Number(b.work || 0) - Number(a.work || 0) ||
      Number(b.shares || 0) - Number(a.shares || 0)
  );
  return String(list[0].worker || "").trim();
}

/**
 * Display nick: coinbase secondary tag if set; else primary stratum worker.
 * Avoids empty nick + "MIISSBLUEE · STRATUM FEE" looking like a double name.
 */
function displayNick(o) {
  const nick = String((o && o.nickname) || "").trim();
  if (nick) return nick;
  const primary = primaryMiningWorker(o && o.workers);
  if (primary) return primary;
  // Legacy compound name — take first segment if it isn't a fee worker.
  const name = String((o && o.name) || "").trim();
  if (name) {
    const first = name.split("·")[0].trim();
    if (first && !isFeeOrOpsWorkerName(first)) return first;
  }
  return "";
}

/**
 * Small icons after the display name:
 *  ?  = no nickname (and no worker fallback)
 *  W  = showing stratum worker (no coinbase nick on file)
 *  🏷 = stored nickname (coinbase tag / set — we don't store source yet)
 *  🏆N = pool finds by this address in the current payout window
 * Two icons (e.g. tag + finds) is intentional.
 */
function nickMetaBadges(c) {
  const stored = String((c && c.nickname) || "").trim();
  const display = displayNick(c);
  const finds = Math.max(0, Number((c && c.luck_finds) || 0));
  const bits = [];
  if (!stored) {
    if (!display) {
      bits.push(
        `<span class="nick-src nick-src-none tip-pin" data-tip="No nickname" title="No nickname" role="img" aria-label="No nickname">?</span>`
      );
    } else {
      const wtip = `No coinbase nickname — showing worker ${display}`;
      bits.push(
        `<span class="nick-src nick-src-worker tip-pin" data-tip="${escapeHtml(wtip)}" title="${escapeHtml(wtip)}" role="img" aria-label="Worker name">W</span>`
      );
    }
  } else if (!isAutoNickGroupName(stored)) {
    const ttip = `Nickname on file (coinbase secondary tag): ${stored}`;
    bits.push(
      `<span class="nick-src nick-src-tag tip-pin" data-tip="${escapeHtml(ttip)}" title="${escapeHtml(ttip)}" role="img" aria-label="Nickname">🏷</span>`
    );
  }
  if (finds > 0) {
    const tip = `${finds} pool block${finds === 1 ? "" : "s"} found in this payout window`;
    bits.push(
      `<span class="nick-src nick-src-finds tip-pin" data-tip="${escapeHtml(tip)}" title="${escapeHtml(tip)}" role="img" aria-label="${escapeHtml(tip)}">🏆${finds}</span>`
    );
  }
  return bits.length ? `<span class="nick-src-wrap">${bits.join("")}</span>` : "";
}

function coinbaserSliceLabel(o) {
  // Stratum Endpoint = shared auto-nick for many SV1 addrs — never label the pie
  // with that string (looks like one person). Use primary mining worker instead.
  if (isAutoNickGroupName(o && o.nickname)) {
    return (
      primaryMiningWorker(o.workers) ||
      shortAddr(o.address || "") ||
      "Stratum"
    );
  }
  const label = displayNick(o);
  if (label) return label;
  return shortAddr(o.address || "");
}

/** Shared blue family for Stratum Endpoint payout addresses (not fee workers). */
const STRATUM_PIE_COLOR = "#5b8def";
/** Within the Stratum Endpoint group, only members ≥ this % of that group get own wedge. */
const STRATUM_PIE_MEMBER_MIN_PCT = 15;

function shadeHex(hex, delta) {
  // delta −1..+1 — lighten/darken for sibling wedges of the same family.
  const h = String(hex || "").replace("#", "");
  if (h.length !== 6) return hex;
  const clamp = (n) => Math.max(0, Math.min(255, Math.round(n)));
  const parts = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  const out = parts.map((c) => {
    if (delta >= 0) return clamp(c + (255 - c) * delta);
    return clamp(c * (1 + delta));
  });
  return `#${out.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

function buildStratumEndpointSegments(stratum) {
  // One pie slice total; internal bands for members ≥15% of the SE group
  // (mining worker labels — no STRATUM FEE). Smaller addrs fold into "+n".
  const tot = stratum.reduce((s, o) => s + Number(o.sats || 0), 0) || 1;
  const big = [];
  const small = [];
  for (const o of stratum) {
    const sats = Number(o.sats || 0);
    if ((100 * sats) / tot >= STRATUM_PIE_MEMBER_MIN_PCT && big.length < 4) {
      big.push(o);
    } else {
      small.push(o);
    }
  }
  if (!big.length && stratum.length) {
    big.push(stratum[0]);
    small.length = 0;
    for (let i = 1; i < stratum.length; i++) small.push(stratum[i]);
  }
  const seShades = [0.12, -0.08, 0.22, -0.18];
  const segments = big.map((o, i) => ({
    label:
      primaryMiningWorker(o.workers) ||
      shortAddr(o.address || "") ||
      "miner",
    sats: Number(o.sats || 0),
    address: o.address || "",
    color: shadeHex(STRATUM_PIE_COLOR, seShades[i % seShades.length]),
  }));
  if (small.length) {
    const restSats = small.reduce((s, o) => s + Number(o.sats || 0), 0);
    if (restSats > 0) {
      segments.push({
        label: `+${small.length} more`,
        sats: restSats,
        address: "",
        color: shadeHex(STRATUM_PIE_COLOR, -0.28),
      });
    }
  }
  return segments;
}

function buildCoinbaserPieSlices(outs) {
  const miners = [];
  let ops = null;
  for (const o of outs || []) {
    if ((o.kind || "") === "ops") ops = o;
    else miners.push(o);
  }
  miners.sort((a, b) => Number(b.sats || 0) - Number(a.sats || 0));
  const minerTot = miners.reduce((s, o) => s + Number(o.sats || 0), 0) || 1;

  // Stratum Endpoint stays ONE pie slice; subdivided inside (see segments).
  const stratum = [];
  const others = [];
  for (const o of miners) {
    if (isAutoNickGroupName(o.nickname)) stratum.push(o);
    else others.push(o);
  }

  const named = [];
  const rest = [];
  for (const o of others) {
    const sats = Number(o.sats || 0);
    const pct = (100 * sats) / minerTot;
    if (named.length < COINBASER_PIE_MAX && pct >= COINBASER_PIE_MIN_PCT) named.push(o);
    else rest.push(o);
  }
  if (named.length < 12 && others.length > named.length) {
    const keep = Math.min(COINBASER_PIE_MAX, 16);
    named.length = 0;
    rest.length = 0;
    for (let i = 0; i < others.length; i++) {
      if (i < keep) named.push(others[i]);
      else rest.push(others[i]);
    }
  }

  const slices = named.map((o, i) => ({
    label: coinbaserSliceLabel(o),
    sats: Number(o.sats || 0),
    address: o.address || "",
    kind: o.kind || "tides",
    color: COINBASER_PIE_COLORS[i % COINBASER_PIE_COLORS.length],
    family: "",
    segments: null,
  }));

  const stratumSats = stratum.reduce((s, o) => s + Number(o.sats || 0), 0);
  if (stratumSats > 0) {
    const segments = buildStratumEndpointSegments(stratum);
    slices.push({
      // Short pie label — avoid "Stratum Endpoint (N) (P%)" double-parens.
      label: stratum.length > 1 ? `Stratum×${stratum.length}` : "Stratum",
      sats: stratumSats,
      address: "",
      kind: "tides",
      color: STRATUM_PIE_COLOR,
      family: "stratum_endpoint",
      // Internal bands drawn by plugin — one wedge, multiple payees visible.
      segments,
    });
  }

  if (rest.length) {
    slices.push({
      label: `Other×${rest.length}`,
      sats: rest.reduce((s, o) => s + Number(o.sats || 0), 0),
      address: "",
      kind: "other",
      color: "#6b7280",
      family: "",
      segments: null,
    });
  }
  if (ops && Number(ops.sats || 0) > 0) {
    slices.push({
      label: "Ops",
      sats: Number(ops.sats || 0),
      address: ops.address || "",
      kind: "ops",
      color: "#9ca3af",
      family: "",
      segments: null,
    });
  }
  slices.sort((a, b) => b.sats - a.sats);
  return slices.filter((s) => s.sats > 0);
}

function coinbaserPieSignature(slices) {
  // Stable against tiny template reward wobble: compare who + share of pie (0.01% units).
  const total = slices.reduce((s, x) => s + x.sats, 0) || 1;
  return slices
    .map((s) => {
      const bp = Math.round((10000 * s.sats) / total);
      const seg = (s.segments || [])
        .map((g) => `${g.label}:${Math.round((10000 * g.sats) / (s.sats || 1))}`)
        .join(",");
      return `${s.kind}:${s.address}:${s.label}:${bp}:${seg}`;
    })
    .join("|");
}

/** Paint Stratum Endpoint internal bands (one wedge, subdivided by payee). */
function paintSliceSegments(ctx, arc, slice) {
  const segs = slice && slice.segments;
  if (!arc || !segs || segs.length < 2) return;
  const tot = segs.reduce((s, g) => s + Number(g.sats || 0), 0) || 1;
  const span = arc.endAngle - arc.startAngle;
  let a0 = arc.startAngle;
  ctx.save();
  for (let i = 0; i < segs.length; i++) {
    const g = segs[i];
    const a1 = a0 + span * (Number(g.sats || 0) / tot);
    ctx.beginPath();
    ctx.arc(arc.x, arc.y, arc.outerRadius, a0, a1);
    ctx.arc(arc.x, arc.y, arc.innerRadius, a1, a0, true);
    ctx.closePath();
    ctx.fillStyle = g.color || slice.color;
    ctx.fill();
    // Thin radial divider between bands (skip first edge — chart border covers it).
    if (i > 0) {
      ctx.strokeStyle = "rgba(11, 18, 32, 0.85)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(
        arc.x + Math.cos(a0) * arc.innerRadius,
        arc.y + Math.sin(a0) * arc.innerRadius
      );
      ctx.lineTo(
        arc.x + Math.cos(a0) * arc.outerRadius,
        arc.y + Math.sin(a0) * arc.outerRadius
      );
      ctx.stroke();
    }
    a0 = a1;
  }
  ctx.restore();
}

/** Mempool-style edge labels + leader lines (left/right), no side legend. */
function ensureCoinbaserOutlabelsPlugin() {
  if (coinbaserOutlabelsRegistered || typeof Chart === "undefined") return;
  Chart.register({
    id: "coinbaserOutlabels",
    afterDatasetsDraw(chart) {
      const cfg = chart.options.plugins && chart.options.plugins.coinbaserOutlabels;
      if (!cfg || !cfg.slices || !cfg.slices.length) return;
      const meta = chart.getDatasetMeta(0);
      if (!meta || !meta.data || !meta.data.length) return;
      const { ctx, chartArea } = chart;
      const slices = cfg.slices;
      const total = cfg.total || 1;
      // Subdivide Stratum Endpoint (and any slice with segments) inside one wedge.
      for (let i = 0; i < meta.data.length; i++) {
        paintSliceSegments(ctx, meta.data[i], slices[i]);
      }
      const gap = 16;
      const leftFinal = [];
      const rightFinal = [];
      for (let i = 0; i < meta.data.length; i++) {
        const arc = meta.data[i];
        const s = slices[i];
        if (!arc || !s || !(arc.outerRadius > 0)) continue;
        const mid = (arc.startAngle + arc.endAngle) / 2;
        const pct = (100 * s.sats) / total;
        // Skip hairline slices for labels (still in tooltip / color).
        if (pct < 0.35 && s.kind === "other") continue;
        const cos = Math.cos(mid);
        const sin = Math.sin(mid);
        // Strict hemisphere: never cross the pie with a leader line.
        const onRight = cos >= 0;
        const ax = arc.x + cos * arc.outerRadius;
        const ay = arc.y + sin * arc.outerRadius;
        // Very short radial stub — labels sit close to the rim (short leader lines).
        const elbowR = arc.outerRadius + 6;
        const ex = arc.x + cos * elbowR;
        const ey = arc.y + sin * elbowR;
        const pctTxt = pct >= 1 ? pct.toFixed(1) : pct.toFixed(2);
        // Soft-cap long nicks before layout so leaders stay short; % always kept by fitLabel.
        let name = String(s.label || "").trim();
        if (name.length > 14) name = name.slice(0, 13) + "…";
        const item = {
          ax,
          ay,
          ex,
          ey,
          y: ey,
          name,
          pctTxt,
          color: s.color,
          onRight,
        };
        (onRight ? rightFinal : leftFinal).push(item);
      }
      const resolve = (arr, top, bottom) => {
        if (!arr.length) return;
        arr.sort((a, b) => a.y - b.y);
        const minY = top;
        const maxY = bottom;
        arr[0].y = Math.max(arr[0].y, minY);
        for (let i = 1; i < arr.length; i++) {
          arr[i].y = Math.max(arr[i].y, arr[i - 1].y + gap);
        }
        if (arr[arr.length - 1].y > maxY) {
          const overflow = arr[arr.length - 1].y - maxY;
          for (const it of arr) it.y -= overflow;
        }
        if (arr[0].y < minY || arr[arr.length - 1].y > maxY) {
          // Evenly pack into available column height so nothing clips.
          if (arr.length === 1) {
            arr[0].y = Math.min(maxY, Math.max(minY, arr[0].y));
          } else {
            const span = Math.max(maxY - minY, gap * (arr.length - 1));
            for (let i = 0; i < arr.length; i++) {
              arr[i].y = minY + (span * i) / (arr.length - 1);
            }
          }
        }
      };
      // Use full canvas height — labels sit in side padding, not only chartArea.
      const yTop = 10;
      const yBot = chart.height - 10;
      resolve(leftFinal, yTop, yBot);
      resolve(rightFinal, yTop, yBot);
      ctx.save();
      ctx.font = "600 12px system-ui, Segoe UI, sans-serif";
      ctx.lineWidth = 1.5;
      ctx.lineJoin = "round";
      // Labels hug the pie: short rim→stub→text leaders. Truncate NAME only — always keep "(N%)".
      const labelGap = 4;
      const rimGap = 8; // text starts just outside the doughnut
      const edgePad = 8; // keep trailing % inside canvas
      const fitLabel = (name, pctTxt, maxTw) => {
        const suffix = ` (${pctTxt}%)`;
        const sufW = ctx.measureText(suffix).width;
        const budget = Math.max(20, maxTw - sufW);
        let n = String(name || "").trim() || "?";
        if (ctx.measureText(n).width <= budget) return n + suffix;
        while (n.length > 2 && ctx.measureText(n + "…").width > budget) {
          n = n.slice(0, -1);
        }
        return `${n}…${suffix}`;
      };
      const drawSide = (arr, onRight) => {
        const colW = onRight ? chart.width - chartArea.right : chartArea.left;
        const maxTw = Math.max(56, colW - rimGap - edgePad - 2);
        for (const it of arr) {
          const text = fitLabel(it.name, it.pctTxt, maxTw);
          const tw = ctx.measureText(text).width;
          let tx;
          let lineEndX;
          if (onRight) {
            // Left-align just outside rim → short leader; name grows toward canvas edge.
            tx = chartArea.right + rimGap;
            // Cap so text doesn't run off the canvas (preserve %).
            const maxTx = chart.width - edgePad - tw;
            if (tx > maxTx) tx = Math.max(chartArea.right + 4, maxTx);
            lineEndX = Math.max(it.ex, tx - labelGap);
            ctx.textAlign = "left";
          } else {
            // Right-align just outside rim.
            tx = chartArea.left - rimGap;
            const minTx = edgePad + tw;
            if (tx < minTx) tx = Math.min(chartArea.left - 4, minTx);
            lineEndX = Math.min(it.ex, tx + labelGap);
            ctx.textAlign = "right";
          }
          ctx.strokeStyle = it.color || "rgba(180,180,180,0.75)";
          ctx.beginPath();
          // Rim → short radial stub → short angled leg to label.
          ctx.moveTo(it.ax, it.ay);
          ctx.lineTo(it.ex, it.ey);
          ctx.lineTo(lineEndX, it.y);
          ctx.stroke();
          ctx.fillStyle = "#d5dbe6";
          ctx.textBaseline = "middle";
          ctx.fillText(text, tx, it.y);
        }
      };
      drawSide(leftFinal, false);
      drawSide(rightFinal, true);
      ctx.restore();
    },
  });
  coinbaserOutlabelsRegistered = true;
}

function paintCoinbaserPie(outs, rewardEst) {
  const canvas = document.getElementById("coinbaserPie");
  if (!canvas) return;
  if (!chartReady()) return;
  ensureCoinbaserOutlabelsPlugin();
  const slices = buildCoinbaserPieSlices(outs);
  const wrap = canvas.parentElement;
  if (!slices.length) {
    if (coinbaserPieObj) coinbaserPieObj = destroyChart(coinbaserPieObj);
    coinbaserPieSig = "";
    if (wrap) wrap.hidden = true;
    return;
  }
  if (wrap) wrap.hidden = false;
  const sig = coinbaserPieSignature(slices);
  // Soft refresh (and table expand re-render) must not thrash Chart.js.
  if (coinbaserPieObj && sig === coinbaserPieSig) return;
  coinbaserPieSig = sig;
  const total = slices.reduce((s, x) => s + x.sats, 0) || Number(rewardEst || 0) || 1;
  const labels = slices.map((s) => s.label);
  coinbaserPieObj = destroyChart(coinbaserPieObj);
  coinbaserPieObj = new Chart(canvas.getContext("2d"), {
    type: "doughnut",
    data: {
      labels,
      datasets: [
        {
          data: slices.map((s) => s.sats),
          backgroundColor: slices.map((s) => s.color),
          borderColor: "rgba(11, 18, 32, 0.95)",
          borderWidth: 1.5,
          hoverOffset: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "48%",
      animation: false,
      layout: {
        // Side columns for near-rim labels (short leaders). Narrow needs more
        // name room or everything collapses to "Xx… (N%)".
        padding: (() => {
          const narrow = typeof window !== "undefined" && window.innerWidth < 700;
          const side = narrow ? 122 : 165;
          return { top: 28, bottom: 28, left: side, right: side };
        })(),
      },
      plugins: {
        legend: { display: false },
        coinbaserOutlabels: { slices, total },
        tooltip: {
          callbacks: {
            label(ctx) {
              const s = slices[ctx.dataIndex];
              if (!s) return "";
              const pct = (100 * s.sats) / total;
              const lines = [
                ` ${s.label}: ${fmtBtc(s.sats)} (${pct.toFixed(2)}%)`,
              ];
              // Stratum Endpoint: show internal payee/worker breakup (no fee workers).
              if (Array.isArray(s.segments) && s.segments.length) {
                const st = s.sats || 1;
                for (const g of s.segments) {
                  const gp = (100 * Number(g.sats || 0)) / st;
                  lines.push(
                    `   · ${g.label}: ${fmtBtc(g.sats)} (${gp.toFixed(0)}% of group)`
                  );
                }
              }
              return lines;
            },
          },
        },
      },
      onClick(_ev, els) {
        if (!els || !els.length) return;
        const s = slices[els[0].index];
        // Grouped Stratum Endpoint has no single address — open first big member.
        if (s && s.segments && s.segments.length) {
          const hit = s.segments.find((g) => g.address);
          if (hit && hit.address) {
            window.location.href = `/address?a=${encodeURIComponent(hit.address)}`;
            return;
          }
        }
        if (s && s.address) {
          window.location.href = `/address?a=${encodeURIComponent(s.address)}`;
        }
      },
    },
  });
}
const CONTRIB_OPEN_KEY = "tides_contrib_worker_open";
const CONTRIB_SORT_KEY = "tides_contrib_sort";
const CONTRIB_GROUP_KEY = "tides_contrib_group";
const CONTRIB_SHOW_ALL_KEY = "tides_contrib_show_all";
const CONTRIB_SHOW_ALL_SV1_KEY = "tides_contrib_show_all_sv1";
const CONTRIB_NICK_OPEN_KEY = "tides_contrib_nick_open";
const CONTRIB_SORT_OPTS = new Set([
  "work",
  "address",
  "nickname",
  "shares",
  "this_block",
  "hashrate",
  "pct",
]);
const CONTRIB_GROUP_OPTS = new Set(["", "nickname"]);
/** Only these nicknames auto-group in default (address) view — not a global nick mode. */
const AUTO_NICK_GROUP_NAMES = new Set(["stratum endpoint"]);
const CONTRIB_NICK_CLOSED_KEY = "tides_contrib_nick_closed";
function isAutoNickGroupName(nick) {
  return AUTO_NICK_GROUP_NAMES.has(((nick || "").trim().toLowerCase()));
}
function loadContribNickClosed() {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(CONTRIB_NICK_CLOSED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}
function saveContribNickClosed(set) {
  try {
    sessionStorage.setItem(CONTRIB_NICK_CLOSED_KEY, JSON.stringify([...set]));
  } catch {
    /* ignore */
  }
}
function loadContribWorkerOpen() {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(CONTRIB_OPEN_KEY) || "[]"));
  } catch {
    return new Set();
  }
}
function saveContribWorkerOpen(set) {
  try {
    sessionStorage.setItem(CONTRIB_OPEN_KEY, JSON.stringify([...set]));
  } catch {
    /* ignore quota / private mode */
  }
}
function loadContribNickOpen() {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(CONTRIB_NICK_OPEN_KEY) || "[]"));
  } catch {
    return new Set();
  }
}
function saveContribNickOpen(set) {
  try {
    sessionStorage.setItem(CONTRIB_NICK_OPEN_KEY, JSON.stringify([...set]));
  } catch {
    /* ignore */
  }
}
function loadContribSort() {
  try {
    const raw = sessionStorage.getItem(CONTRIB_SORT_KEY) || "work";
    // migrate old multi-key JSON → primary only
    if (raw[0] === "[") {
      const arr = JSON.parse(raw);
      const first = Array.isArray(arr) ? arr.find((v) => CONTRIB_SORT_OPTS.has(v)) : null;
      return first || "work";
    }
    return CONTRIB_SORT_OPTS.has(raw) ? raw : "work";
  } catch {
    return "work";
  }
}
function saveContribSort(v) {
  try {
    sessionStorage.setItem(CONTRIB_SORT_KEY, v);
  } catch {
    /* ignore */
  }
}
function loadContribGroup() {
  try {
    // Prefer new key; migrate "nickname primary sort" from old multi-sort → group
    const g = sessionStorage.getItem(CONTRIB_GROUP_KEY);
    if (g !== null) return CONTRIB_GROUP_OPTS.has(g) ? g : "";
    const raw = sessionStorage.getItem(CONTRIB_SORT_KEY) || "";
    if (raw[0] === "[") {
      try {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr) && arr[0] === "nickname") return "nickname";
      } catch {
        /* ignore */
      }
    }
    return "";
  } catch {
    return "";
  }
}
function saveContribGroup(v) {
  try {
    sessionStorage.setItem(CONTRIB_GROUP_KEY, v || "");
  } catch {
    /* ignore */
  }
}
function loadContribShowAll() {
  try {
    return sessionStorage.getItem(CONTRIB_SHOW_ALL_KEY) === "1";
  } catch {
    return false;
  }
}
function saveContribShowAll(on) {
  try {
    sessionStorage.setItem(CONTRIB_SHOW_ALL_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}
function loadContribShowAllSv1() {
  try {
    return sessionStorage.getItem(CONTRIB_SHOW_ALL_SV1_KEY) === "1";
  } catch {
    return false;
  }
}
function saveContribShowAllSv1(on) {
  try {
    sessionStorage.setItem(CONTRIB_SHOW_ALL_SV1_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}
let contribShowAll = loadContribShowAll();
let contribShowAllSv1 = loadContribShowAllSv1();
let contribLast = null;
let contribSort = loadContribSort();
let contribGroup = loadContribGroup();
let contribWorkerOpen = loadContribWorkerOpen();
let contribNickOpen = loadContribNickOpen();
let contribNickClosed = loadContribNickClosed();

function nickKey(c) {
  const n = ((c && c.nickname) || "").trim();
  return n || "\0"; // empty nick sorts / groups last
}

function autoNickGroupIsOpen(gid, label) {
  if (contribNickClosed.has(gid)) return false;
  if (contribNickOpen.has(gid)) return true;
  // Stratum Endpoint (and other AUTO_NICK names): default expanded
  return isAutoNickGroupName(label);
}

/** Shared HTML for a nickname parent row + member address rows. */
function contribNickGroupBlock(g, displayIdx, { forceGroup = false } = {}) {
  const parts = [];
  let idx = displayIdx;
  if ((!forceGroup && g.members.length === 1) || g.key === "\0") {
    if (g.key === "\0") {
      for (const c of g.members) {
        idx += 1;
        parts.push(contribAddressRowHtml(c, idx));
      }
      return { html: parts.join(""), displayIdx: idx };
    }
    if (!forceGroup && !isAutoNickGroupName(g.key)) {
      idx += 1;
      parts.push(contribAddressRowHtml(g.members[0], idx));
      return { html: parts.join(""), displayIdx: idx };
    }
  }
  const gid = `cn-${encodeURIComponent(g.key)}`;
  const label = g.key;
  const isOpen = autoNickGroupIsOpen(gid, label);
  const totShares = g.members.reduce((s, c) => s + Number(c.shares || 0), 0);
  const totWork = g.members.reduce((s, c) => s + Number(c.work || 0), 0);
  const totCur = g.members.reduce((s, c) => s + Number(c.work_current || 0), 0);
  const totHs = g.members.reduce((s, c) => s + Number(c.hashrate_hs || 0), 0);
  const totPct = g.members.reduce((s, c) => s + Number(c.share_pct || 0), 0);
  // Work-weighted % Blocks w Shares (same columns as address rows — no Last share).
  let erasNum = 0;
  let erasDen = 0;
  for (const c of g.members) {
    const w = Number(c.work || 0);
    const p = Number(c.eras_with_work_pct);
    if (w > 0 && Number.isFinite(p)) {
      erasNum += p * w;
      erasDen += w;
    }
  }
  const erasPct = erasDen > 0 ? erasNum / erasDen : null;
  const erasCell =
    erasPct != null
      ? `<td title="Work-weighted % Blocks w Shares across ${g.members.length} addresses">${erasPct.toFixed(0)}%</td>`
      : `<td class="muted" title="% Blocks w Shares">—</td>`;
  const anyLive = g.members.some(isContribLive);
  const plus = `<button type="button" class="worker-plus nick-plus" data-nick-expand="${gid}" data-open="${
    isOpen ? "1" : "0"
  }" title="${g.members.length} payout addresses — click to expand">${isOpen ? "−" : "+"}</button>`;
  idx += 1;
  // Columns: activity | # | Address | Nickname | Shares | % Blocks w Shares | This block | Total work | ~H/s | Payout %
  parts.push(`<tr class="nick-group" data-nick-group="${gid}">
        <td class="activity-cell">${anyLive ? activityDot({ activity: "live", hashrate_hs: totHs }) : activityDot({ activity: "offline", hashrate_hs: 0 })}</td>
        <td>${idx} ${plus}</td>
        <td class="muted" title="Nickname group — payouts stay on child addresses">—</td>
        ${clipCell(label, { title: `Nickname group: ${label}`, wide: true })}
        <td title="Sum of member shares">${fmtInt(totShares)}</td>
        ${erasCell}
        <td title="Sum of member this-block work">${fmtInt(totCur)}</td>
        <td title="Sum of member window work">${fmtInt(totWork)}</td>
        <td title="Sum of member hashrates">${fmtHashrate(totHs)}</td>
        <td title="Sum of member payout %">${totPct.toFixed(2)}%</td>
      </tr>`);
  for (const c of g.members) {
    parts.push(
      contribAddressRowHtml(c, "", {
        indentNick: true,
        nickParent: gid,
        nickHidden: !isOpen,
      })
    );
  }
  return { html: parts.join(""), displayIdx: idx };
}

function isStratumEndpointMember(c) {
  // Prefer share connection_type split; fall back to Stratum Endpoint nick.
  const sv1 = Number((c && c.sv1_work) || 0);
  const datum = Number((c && c.datum_work) || 0);
  if (sv1 > 0) return true;
  if (datum > 0) return false;
  return isAutoNickGroupName(c && c.nickname);
}

function isDatumPathMember(c) {
  const sv1 = Number((c && c.sv1_work) || 0);
  const datum = Number((c && c.datum_work) || 0);
  if (datum > 0) return true;
  if (sv1 > 0) return false;
  return !isAutoNickGroupName(c && c.nickname);
}

/** Path hashrate for totals: prefer filtered workers when dual-path; else parent. */
function contribPathHs(c) {
  const parent = Number((c && c.hashrate_hs) || 0);
  const ws = Array.isArray(c && c.workers) ? c.workers : [];
  if (!ws.length) return parent;
  const wHs = ws.reduce((s, w) => s + Number(w.hashrate_hs || 0), 0);
  const dual =
    Number((c && c.sv1_work) || 0) > 0 && Number((c && c.datum_work) || 0) > 0;
  if (dual && wHs > 0) return wHs;
  return parent || wHs;
}

/** Sum metrics across all path contributors (full window list, not collapsed view). */
function sumContribTotals(rows) {
  let shares = 0;
  let work = 0;
  let workCur = 0;
  let hs = 0;
  let pct = 0;
  let erasNum = 0;
  let erasDen = 0;
  for (const c of rows || []) {
    shares += Number(c.shares || 0);
    work += Number(c.work || 0);
    workCur += Number(c.work_current || 0);
    hs += contribPathHs(c);
    pct += Number(c.share_pct || 0);
    const w = Number(c.work || 0);
    const p = Number(c.eras_with_work_pct);
    if (w > 0 && Number.isFinite(p)) {
      erasNum += p * w;
      erasDen += w;
    }
  }
  return {
    n: (rows || []).length,
    shares,
    work,
    workCur,
    hs,
    pct,
    erasPct: erasDen > 0 ? erasNum / erasDen : null,
  };
}

function contribTotalsRowHtml(tot, { hideNick = false, label = "Total" } = {}) {
  const erasCell =
    tot.erasPct != null
      ? `<td title="Work-weighted % Blocks w Shares">${tot.erasPct.toFixed(0)}%</td>`
      : `<td class="muted">—</td>`;
  const nickCell = hideNick ? "" : `<td class="muted">—</td>`;
  const tip = `All ${tot.n} contributor${tot.n === 1 ? "" : "s"} in this path (full window — not just hashing-now view)`;
  return `<tr class="contrib-totals" title="${escapeHtml(tip)}">
        <td class="activity-cell"></td>
        <td class="muted">Σ</td>
        <td><strong>${escapeHtml(label)}</strong> <span class="muted">(${fmtInt(tot.n)})</span></td>
        ${nickCell}
        <td title="Sum of shares">${fmtInt(tot.shares)}</td>
        ${erasCell}
        <td title="Sum of this-block work">${fmtInt(tot.workCur)}</td>
        <td title="Sum of window work">${fmtInt(tot.work)}</td>
        <td title="Sum of ~H/s on this path"><strong>${fmtHashrate(tot.hs)}</strong></td>
        <td title="Sum of payout %">${Number(tot.pct || 0).toFixed(2)}%</td>
      </tr>`;
}

/** Filter workers for a section; dual-path addresses can appear in both. */
function filterContribForPath(c, path) {
  const fee = (w) => {
    const s = String((w && w.worker) || "").trim().toUpperCase();
    return s === "STRATUM FEE" || s === "OPERATION FEE" || s === "OPS" || s.startsWith("OPS");
  };
  const workers = Array.isArray(c.workers) ? c.workers.slice() : [];
  const want = path === "sv1" ? "sv1" : "datum";
  const nickSv1 = isAutoNickGroupName(c.nickname);
  const filtered = workers.filter((w) => {
    if (fee(w)) return path === "sv1" && String(w.worker || "").toUpperCase() === "OPERATION FEE";
    const ct = String((w && w.connection_type) || "").toLowerCase();
    if (ct === "sv1" || ct === "datum") return ct === want;
    // legacy untyped
    return want === "sv1" ? nickSv1 : !nickSv1;
  });
  const out = Object.assign({}, c, { workers: filtered });
  if (path === "sv1" && Number(c.sv1_work || 0) > 0) {
    out.work = Number(c.sv1_work);
  } else if (path === "datum" && Number(c.datum_work || 0) > 0) {
    out.work = Number(c.datum_work);
  }
  return out;
}

function metricContrib(c, mode) {
  switch (mode) {
    case "shares":
      return Number(c.shares || 0);
    case "this_block":
      return Number(c.work_current || 0);
    case "hashrate":
      return Number(c.hashrate_hs || 0);
    case "pct":
      return Number(c.share_pct || 0);
    case "work":
    default:
      return Number(c.work || 0);
  }
}

function cmpContribByMode(a, b, mode) {
  const cmpStr = (x, y) => x.localeCompare(y, undefined, { sensitivity: "base" });
  switch (mode) {
    case "address":
      return cmpStr(String(a.address || ""), String(b.address || ""));
    case "nickname": {
      const ka = nickKey(a);
      const kb = nickKey(b);
      if (ka === "\0" && kb !== "\0") return 1;
      if (kb === "\0" && ka !== "\0") return -1;
      return cmpStr(ka === "\0" ? "" : ka, kb === "\0" ? "" : kb);
    }
    case "shares":
    case "this_block":
    case "hashrate":
    case "pct":
    case "work":
      return metricContrib(b, mode) - metricContrib(a, mode);
    default:
      return metricContrib(b, "work") - metricContrib(a, "work");
  }
}

function sortContributors(list, mode) {
  const m = CONTRIB_SORT_OPTS.has(mode) ? mode : "work";
  const rows = list.slice();
  rows.sort((a, b) => {
    const c = cmpContribByMode(a, b, m);
    if (c !== 0) return c;
    return cmpContribByMode(a, b, "address");
  });
  return rows;
}

function groupMetric(members, mode) {
  if (mode === "address" || mode === "nickname") {
    return nickKey(members[0]);
  }
  return members.reduce((s, c) => s + metricContrib(c, mode), 0);
}

function sortNickGroups(groups, mode) {
  const m = CONTRIB_SORT_OPTS.has(mode) ? mode : "work";
  const cmpStr = (x, y) => x.localeCompare(y, undefined, { sensitivity: "base" });
  groups.sort((ga, gb) => {
    if (m === "nickname" || m === "address") {
      const ka = ga.key === "\0" ? "" : ga.key;
      const kb = gb.key === "\0" ? "" : gb.key;
      if (ga.key === "\0" && gb.key !== "\0") return 1;
      if (gb.key === "\0" && ga.key !== "\0") return -1;
      const c = cmpStr(ka, kb);
      if (c !== 0) return c;
    } else {
      const d = groupMetric(gb.members, m) - groupMetric(ga.members, m);
      if (d !== 0) return d;
    }
    return cmpStr(ga.key === "\0" ? "" : ga.key, gb.key === "\0" ? "" : gb.key);
  });
  for (const g of groups) {
    g.members = sortContributors(g.members, m);
  }
  return groups;
}

function wireContribSortBar() {
  const gSel = document.getElementById("contribGroup");
  const sSel = document.getElementById("contribSort");
  if (!gSel || !sSel || gSel.dataset.wired) return;
  gSel.value = contribGroup || "";
  sSel.value = CONTRIB_SORT_OPTS.has(contribSort) ? contribSort : "work";
  const onChange = () => {
    contribGroup = gSel.value === "nickname" ? "nickname" : "";
    contribSort = CONTRIB_SORT_OPTS.has(sSel.value) ? sSel.value : "work";
    saveContribGroup(contribGroup);
    saveContribSort(contribSort);
    renderContributors(contribLast);
  };
  gSel.addEventListener("change", onChange);
  sSel.addEventListener("change", onChange);
  gSel.dataset.wired = "1";
}

function isContribLive(c) {
  return (
    (c && c.activity === "live") ||
    (c && Number(c.hashrate_hs || 0) > 0)
  );
}

/** Pool ops fee payee (OPERATION FEE worker) — pin unranked at top of SV1. */
function isOpsFeeContrib(c) {
  const workers = Array.isArray(c && c.workers) ? c.workers : [];
  return workers.some((w) => {
    const s = String((w && w.worker) || "")
      .trim()
      .toUpperCase();
    return s === "OPERATION FEE" || s === "OPS";
  });
}

function renderCoinbaser(coinbaser) {
  const cbBody = document.getElementById("coinbaserBody");
  const cbNote = document.getElementById("coinbaserNote");
  const more = document.getElementById("coinbaserMore");
  const tablePanel = document.getElementById("coinbaserTablePanel");
  const tableToggle = document.getElementById("coinbaserTableToggle");
  if (!cbBody) return;
  if (!coinbaser) {
    cbBody.innerHTML = `<tr><td colspan="5" class="muted">Failed to load /api/coinbaser</td></tr>`;
    if (cbNote) cbNote.textContent = "Coinbaser unavailable";
    if (more) {
      more.hidden = true;
      more.innerHTML = "";
    }
    paintCoinbaserPie([], 0);
    return;
  }
  coinbaserLast = coinbaser;
  const outs = coinbaser.outputs || [];
  if (cbNote) {
    const note = outs.length
      ? `~${fmtBtc(coinbaser.reward_sats_estimate)} total · ${outs.length} line(s) · window work ${fmtInt(coinbaser.window_work)}`
      : `No miner lines yet (~${fmtBtc(coinbaser.reward_sats_estimate)}) — empty window`;
    cbNote.textContent = note;
  }
  paintCoinbaserPie(outs, coinbaser.reward_sats_estimate);

  if (tablePanel) tablePanel.hidden = !coinbaserTableOpen;
  if (tableToggle) {
    tableToggle.textContent = coinbaserTableOpen
      ? "Hide payout lines"
      : `Show payout lines${outs.length ? ` (${outs.length})` : ""}`;
    if (!tableToggle.dataset.wired) {
      tableToggle.dataset.wired = "1";
      tableToggle.addEventListener("click", () => {
        coinbaserTableOpen = !coinbaserTableOpen;
        saveCoinbaserTableOpen(coinbaserTableOpen);
        if (coinbaserLast) renderCoinbaser(coinbaserLast);
      });
    }
  }

  if (!outs.length) {
    cbBody.innerHTML = `<tr><td colspan="5" class="muted">No coinbaser outputs (empty window)</td></tr>`;
    if (more) {
      more.hidden = true;
      more.innerHTML = "";
    }
    return;
  }
  const hidden = Math.max(0, outs.length - COINBASER_TOP_N);
  const show = coinbaserExpanded || hidden === 0 ? outs : outs.slice(0, COINBASER_TOP_N);
  cbBody.innerHTML = show
    .map((o) => {
      let rowClass = "";
      if (o.kind === "ops") rowClass = ' class="row-ops"';
      const wlist = Array.isArray(o.workers) ? o.workers : [];
      // Worker column: mining workers only (hide STRATUM FEE noise in the cell).
      const mineWs = wlist.filter((w) => w && !isFeeOrOpsWorkerName(w.worker));
      let worker = "";
      if (mineWs.length > 1) {
        worker = mineWs.map((w) => w.worker).join(" · ");
      } else if (mineWs.length === 1) {
        worker = String(mineWs[0].worker || "").trim();
      } else {
        worker = primaryMiningWorker(wlist) || "";
      }
      const nick = displayNick(o);
      const nickFromWorker = nick && !(o.nickname || "").trim();
      const tip =
        wlist.length > 1
          ? wlist
              .map(
                (w) =>
                  `${w.worker}: ${fmtInt(w.shares)} sh · work ${fmtInt(w.work)}` +
                  (w.sats != null ? ` · ~${fmtBtc(w.sats)}` : "")
              )
              .join("\n")
          : worker
            ? `Stratum worker: ${worker}`
            : "";
      return `<tr${rowClass}>
      <td>${kindCell(o, coinbaser.reward_sats_estimate)}</td>
      ${clipCell(worker, { title: tip })}
      ${clipCell(nick, {
        title: nick
          ? nickFromWorker
            ? `No coinbase secondary tag — showing primary worker ${nick}`
            : `Nickname: ${nick}`
          : "",
        wide: true,
      })}
      <td class="mono"><a href="/address?a=${encodeURIComponent(o.address)}" title="${o.address}">${shortAddr(o.address)}</a></td>
      <td title="${fmtBtcTitle(o.sats)}">${fmtBtc(o.sats)}</td>
    </tr>`;
    })
    .join("");
  if (more) {
    if (hidden === 0) {
      more.hidden = true;
      more.innerHTML = "";
    } else {
      more.hidden = false;
      more.innerHTML = coinbaserExpanded
        ? `<button type="button" id="coinbaserToggle">Show top ${COINBASER_TOP_N} only</button>`
        : `<button type="button" id="coinbaserToggle">Show ${hidden} more line${hidden === 1 ? "" : "s"}</button>`;
      const btn = document.getElementById("coinbaserToggle");
      if (btn) {
        btn.onclick = () => {
          coinbaserExpanded = !coinbaserExpanded;
          if (coinbaserLast) renderCoinbaser(coinbaserLast);
        };
      }
    }
  }
}

function contribAddressRowHtml(
  c,
  rankNum,
  {
    indentNick = false,
    nickParent = null,
    nickHidden = false,
    hideNick = false,
    unranked = false,
  } = {}
) {
  const wlist = Array.isArray(c.workers) ? c.workers : [];
  const multi = wlist.length > 1;
  const expandId = `cw-${c.address}`;
  const isOpen = multi && contribWorkerOpen.has(expandId);
  const plus = multi
    ? `<button type="button" class="worker-plus" data-expand="${expandId}" data-open="${
        isOpen ? "1" : "0"
      }" title="${wlist.length} workers — click to expand">${isOpen ? "−" : "+"}</button>`
    : "";
  const nick = displayNick(c);
  const nickFromWorker = nick && !(c.nickname || "").trim();
  const nickBadges = nickMetaBadges(c);
  // Under Stratum Endpoint group: nickname column shows worker name(s), not the group nick.
  let nickCell = "";
  if (!hideNick) {
    if (indentNick && nickParent) {
      if (!multi && wlist.length === 1) {
        const wraw = wlist[0].worker;
        const wn = displayWorkerName(wraw) || "—";
        const star = workerFinderMark(c.address, wraw);
        nickCell = `<td class="mono" title="Worker ${escapeHtml(wn)}">${star}${escapeHtml(wn)}</td>`;
      } else if (multi) {
        nickCell = `<td class="muted" title="${wlist.length} workers — expand">↳ workers</td>`;
      } else {
        nickCell = `<td class="muted">↳</td>`;
      }
    } else if (!nick) {
      nickCell = `<td class="clip-wide"><span class="muted">—</span>${nickBadges}</td>`;
    } else {
      const tip = nickFromWorker
        ? `No coinbase secondary tag — showing primary worker ${nick}`
        : `Nickname: ${nick}`;
      nickCell = `<td class="clip-wide" title="${escapeHtml(tip)}"><span class="clip-text">${escapeHtml(nick)}</span>${nickBadges}</td>`;
    }
  }
  const nickAttrs = nickParent
    ? ` class="nick-sub" data-nick-parent="${nickParent}"${nickHidden ? " hidden" : ""}`
    : "";
  const opsRankTip = "Operations share of fee (skim) — not ranked with SV1 miners";
  const rankLabel = unranked
    ? `<span class="kind-ico kind-ops" title="${opsRankTip}" aria-label="${opsRankTip}">${KIND_ICO.ops}</span>`
    : String(rankNum);
  const rankTitle = unranked ? ` title="${opsRankTip}"` : "";
  // SV1 table omits nickname col — keep finds/?/W badges next to address.
  const addrBadges = hideNick ? nickBadges : "";
  const main = `<tr${nickAttrs}${unranked ? ' class="row-ops"' : ""} data-addr="${escapeHtml(c.address)}">
        <td class="activity-cell">${activityDot(c)}</td>
        <td${rankTitle}>${rankLabel}${plus ? " " + plus : ""}</td>
        <td class="mono"><a href="/address?a=${encodeURIComponent(c.address)}" title="${c.address}">${shortAddr(c.address)}</a>${cbTypeBadge(c)}${quarantineBadge(c)}${addrBadges}</td>
        ${nickCell}
        <td title="Accepted shares in the full payout window">${fmtInt(c.shares)}</td>
        <td title="${fmtInt(c.eras_with_work ?? 0)} of ${fmtInt(c.window_eras ?? 0)} block-periods with shares (not payout %)">${Number(c.eras_with_work_pct || 0).toFixed(0)}%</td>
        <td title="Work since last confirmed pool find (unfinished current block)">${fmtInt(c.work_current ?? 0)}</td>
        <td title="Total work in payout window only (7 confirmed + current) — not lifetime">${fmtInt(c.work)}</td>
        <td title="Rough hashrate from recent shares (~10m)">${fmtHashrate(c.hashrate_hs)}</td>
        <td title="Your total window work ÷ window work">${Number(c.share_pct || 0).toFixed(2)}%</td>
      </tr>`;
  let sub = "";
  if (multi) {
    const workerHidden = nickHidden || !isOpen;
    const nickData = nickParent ? ` data-nick-parent="${nickParent}"` : "";
    const underNickGroup = Boolean(nickParent);
    sub = wlist
      .map((w) => {
        const payoutCell =
          w.sats != null
            ? `<span title="${fmtBtcTitle(w.sats)}">${fmtBtc(w.sats)}</span>`
            : `<span title="≈ ${Number(w.share_pct || 0).toFixed(1)}% of this address">${Number(w.share_pct || 0).toFixed(1)}%</span>`;
        const wraw = w.worker;
        const wn = escapeHtml(displayWorkerName(wraw));
        const star = workerFinderMark(c.address, wraw);
        // Under Stratum Endpoint nick-group: address | worker-in-nickname-column
        if (underNickGroup && !hideNick) {
          return `<tr class="worker-sub" data-parent="${expandId}"${nickData}${workerHidden ? " hidden" : ""}>
            <td></td>
            <td></td>
            <td class="mono muted" title="${escapeHtml(c.address)}">↳ ${shortAddr(c.address)}</td>
            <td class="mono" title="Worker">${star}${wn}</td>
            <td>${fmtInt(w.shares)}</td>
            <td class="muted">—</td>
            <td class="muted">—</td>
            <td>${fmtInt(w.work)}</td>
            <td>${fmtHashrate(w.hashrate_hs)}</td>
            <td>${payoutCell}</td>
          </tr>`;
        }
        // SV1 table (no nickname col) or plain DATUM expand
        const addrWorkerCell = hideNick
          ? `<td class="muted">↳ <span class="mono">${star}${wn}</span></td>`
          : `<td class="muted" colspan="2">↳ <span class="mono">${star}${wn}</span></td>`;
        return `<tr class="worker-sub" data-parent="${expandId}"${nickData}${workerHidden ? " hidden" : ""}>
            <td></td>
            <td></td>
            ${addrWorkerCell}
            <td>${fmtInt(w.shares)}</td>
            <td class="muted">—</td>
            <td class="muted">—</td>
            <td>${fmtInt(w.work)}</td>
            <td>${fmtHashrate(w.hashrate_hs)}</td>
            <td>${payoutCell}</td>
          </tr>`;
      })
      .join("");
  }
  return main + sub;
}


function wireContribExpand(cbody) {
  if (!cbody) return;
  cbody.querySelectorAll(".worker-plus:not(.nick-plus)").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const id = btn.getAttribute("data-expand");
      const open = btn.dataset.open === "1";
      if (open) contribWorkerOpen.delete(id);
      else contribWorkerOpen.add(id);
      saveContribWorkerOpen(contribWorkerOpen);
      btn.dataset.open = open ? "0" : "1";
      btn.textContent = open ? "+" : "−";
      cbody.querySelectorAll("tr.worker-sub").forEach((tr) => {
        if (tr.getAttribute("data-parent") !== id) return;
        const nickParent = tr.getAttribute("data-nick-parent");
        if (nickParent && !contribNickOpen.has(nickParent)) {
          tr.hidden = true;
          return;
        }
        tr.hidden = open;
      });
    });
  });
  cbody.querySelectorAll(".nick-plus").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const id = btn.getAttribute("data-nick-expand");
      const open = btn.dataset.open === "1";
      if (open) {
        contribNickOpen.delete(id);
        contribNickClosed.add(id);
      } else {
        contribNickClosed.delete(id);
        contribNickOpen.add(id);
      }
      saveContribNickOpen(contribNickOpen);
      saveContribNickClosed(contribNickClosed);
      btn.dataset.open = open ? "0" : "1";
      btn.textContent = open ? "+" : "−";
      cbody.querySelectorAll(`tr.nick-sub[data-nick-parent="${id}"]`).forEach((tr) => {
        tr.hidden = open;
      });
      cbody.querySelectorAll(`tr.worker-sub[data-nick-parent="${id}"]`).forEach((tr) => {
        const wid = tr.getAttribute("data-parent");
        tr.hidden = open || !contribWorkerOpen.has(wid);
      });
    });
  });
}

/** SV1 clients with non-address usernames (templates yes, payout nowhere). */
let sv1BadAuthLast = null;

async function loadSv1BadAuth() {
  try {
    const r = await fetch("/static/data/sv1_bad_auth.json?ts=" + Date.now(), {
      cache: "no-store",
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

function renderSv1BadAuth(data) {
  const el = document.getElementById("sv1BadAuthWarn");
  if (!el) return;
  sv1BadAuthLast = data;
  const clients = data && Array.isArray(data.clients) ? data.clients : [];
  if (!data || !data.ok || !clients.length) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  const payoutNote =
    (data && data.payout_detail) ||
    "Rejected at Prime (bad payout address) — 0 shares credited; payout goes nowhere (not ops, not the miner).";
  const items = clients
    .map((c) => {
      const user = escapeHtml(c.auth_username || c.payout_part || "?");
      const tip = c.ip_tail ? `·${escapeHtml(c.ip_tail)}` : "";
      const hr = c.hashrate ? ` · ${escapeHtml(c.hashrate)}` : "";
      return `<li><span class="mono">${user}</span> <span class="muted">${tip}</span> · <strong>0 shares</strong>${hr}</li>`;
    })
    .join("");
  el.hidden = false;
  el.innerHTML =
    `<strong class="warn">⚠ Unknown SV1 username(s)</strong>` +
    ` <span class="muted">— not a BTC payout address · still may receive templates</span>` +
    `<ul>${items}</ul>` +
    `<p class="muted" style="margin:0.4rem 0 0">${escapeHtml(payoutNote)}</p>`;
}

function renderContributors(contrib) {
  const cbodyDatum = document.getElementById("contribBodyDatum") || document.getElementById("contribBody");
  const cbodySv1 = document.getElementById("contribBodySv1");
  const more = document.getElementById("contribMore");
  const moreSv1 = document.getElementById("contribMoreSv1");
  const sortBar = document.getElementById("contribSortBar");
  if (!cbodyDatum) return;
  contribLast = Array.isArray(contrib) ? contrib : [];
  if (!contribLast.length) {
    cbodyDatum.innerHTML = `<tr><td colspan="10" class="muted">No shares in window yet</td></tr>`;
    if (cbodySv1) cbodySv1.innerHTML = `<tr><td colspan="9" class="muted">No SV1 shares in window yet</td></tr>`;
    const datumTotEl0 = document.getElementById("contribTotDatum");
    const sv1TotEl0 = document.getElementById("contribTotSv1");
    if (datumTotEl0) {
      datumTotEl0.textContent = "";
      datumTotEl0.title = "";
    }
    if (sv1TotEl0) {
      sv1TotEl0.textContent = "";
      sv1TotEl0.title = "";
    }
    if (more) {
      more.hidden = true;
      more.innerHTML = "";
    }
    if (moreSv1) {
      moreSv1.hidden = true;
      moreSv1.innerHTML = "";
    }
    if (sortBar) sortBar.hidden = true;
    return;
  }

  // Path-split the full window first, then each section collapses independently.
  const allDatum = [];
  const allSv1 = [];
  for (const c of contribLast) {
    if (isDatumPathMember(c)) allDatum.push(filterContribForPath(c, "datum"));
    if (isStratumEndpointMember(c)) allSv1.push(filterContribForPath(c, "sv1"));
  }
  const datumLive = allDatum.filter(isContribLive);
  const datumRestN = allDatum.length - datumLive.length;
  const showingAllDatum = contribShowAll || datumRestN <= 0;

  const sv1Live = allSv1.filter(isContribLive);
  const sv1RestN = allSv1.length - sv1Live.length;
  const showingAllSv1 = contribShowAllSv1 || sv1RestN <= 0;

  if (sortBar) sortBar.hidden = !showingAllDatum;
  if (showingAllDatum) {
    wireContribSortBar();
    const gSel = document.getElementById("contribGroup");
    const sSel = document.getElementById("contribSort");
    if (gSel) gSel.value = contribGroup || "";
    if (sSel) sSel.value = CONTRIB_SORT_OPTS.has(contribSort) ? contribSort : "work";
  }

  const sortModeDatum = showingAllDatum ? contribSort : "work";
  const groupMode = showingAllDatum ? contribGroup : "";
  const sortModeSv1 = showingAllSv1 ? contribSort : "work";
  const datumRows = showingAllDatum ? allDatum : datumLive;
  let sv1Rows = showingAllSv1 ? allSv1.slice() : sv1Live.slice();
  // Always pin ops fee row when it has window work (even if not "live").
  for (const c of allSv1) {
    if (!isOpsFeeContrib(c)) continue;
    if (!sv1Rows.some((r) => r.address === c.address)) sv1Rows.push(c);
  }

  // --- DATUM table (own ranking) ---
  const datumParts = [];
  let datumIdx = 0;
  if (groupMode === "nickname") {
    const byNick = new Map();
    for (const c of datumRows) {
      const k = nickKey(c);
      if (!byNick.has(k)) byNick.set(k, []);
      byNick.get(k).push(c);
    }
    let groups = [...byNick.entries()].map(([key, members]) => ({ key, members }));
    groups = sortNickGroups(groups, sortModeDatum);
    for (const g of groups) {
      const block = contribNickGroupBlock(g, datumIdx, {
        forceGroup: isAutoNickGroupName(g.key),
      });
      datumParts.push(block.html);
      datumIdx = block.displayIdx;
    }
  } else {
    const sortedDatum = sortContributors(datumRows, sortModeDatum);
    if (sortedDatum.length) {
      for (const c of sortedDatum) {
        datumIdx += 1;
        datumParts.push(contribAddressRowHtml(c, datumIdx));
      }
    } else {
      datumParts.push(
        `<tr><td colspan="10" class="muted">No own-Gateway contributors in this view.</td></tr>`
      );
    }
  }
  const datumTot = sumContribTotals(allDatum);
  if (allDatum.length) {
    datumParts.push(
      contribTotalsRowHtml(datumTot, { label: "Total DATUM" })
    );
  }
  cbodyDatum.innerHTML = datumParts.join("");
  wireContribExpand(cbodyDatum);
  const datumTotEl = document.getElementById("contribTotDatum");
  if (datumTotEl) {
    datumTotEl.textContent = allDatum.length
      ? `· ${fmtHashrate(datumTot.hs)} · ${fmtInt(datumTot.n)} addr`
      : "";
    datumTotEl.title = allDatum.length
      ? `Total ~H/s across all ${datumTot.n} DATUM contributors in the payout window`
      : "";
  }

  // --- SV1 table: ops unranked at top; no nickname col; own hashing-now collapse ---
  if (cbodySv1) {
    const sv1Parts = [];
    const opsRows = sv1Rows.filter(isOpsFeeContrib);
    const minerRows = sv1Rows.filter((c) => !isOpsFeeContrib(c));
    const sortedOps = sortContributors(opsRows, sortModeSv1);
    const sortedMiners = sortContributors(minerRows, sortModeSv1);
    for (const c of sortedOps) {
      sv1Parts.push(
        contribAddressRowHtml(c, 0, { hideNick: true, unranked: true })
      );
    }
    let sv1Idx = 0;
    for (const c of sortedMiners) {
      sv1Idx += 1;
      sv1Parts.push(contribAddressRowHtml(c, sv1Idx, { hideNick: true }));
    }
    if (!sv1Parts.length) {
      sv1Parts.push(
        `<tr><td colspan="9" class="muted">No SV1 / Stratum Endpoint addresses in this view yet.</td></tr>`
      );
    }
    const sv1Tot = sumContribTotals(allSv1);
    if (allSv1.length) {
      sv1Parts.push(
        contribTotalsRowHtml(sv1Tot, { hideNick: true, label: "Total SV1" })
      );
    }
    cbodySv1.innerHTML = sv1Parts.join("");
    wireContribExpand(cbodySv1);
    const sv1TotEl = document.getElementById("contribTotSv1");
    if (sv1TotEl) {
      sv1TotEl.textContent = allSv1.length
        ? `· ${fmtHashrate(sv1Tot.hs)} · ${fmtInt(sv1Tot.n)} addr`
        : "";
      sv1TotEl.title = allSv1.length
        ? `Total ~H/s across all ${sv1Tot.n} SV1 contributors in the payout window`
        : "";
    }
  }

  if (more) {
    if (datumRestN > 0) {
      more.hidden = false;
      more.innerHTML = contribShowAll
        ? `<button type="button" id="contribToggle">Show hashing now only (${datumLive.length})</button>` +
          ` <span class="muted">${allDatum.length} DATUM in window</span>`
        : `<button type="button" id="contribToggle">Show all in window (+${datumRestN} idle/offline)</button>` +
          ` <span class="muted">${datumLive.length} hashing now</span>`;
      const btn = document.getElementById("contribToggle");
      if (btn) {
        btn.onclick = () => {
          contribShowAll = !contribShowAll;
          saveContribShowAll(contribShowAll);
          renderContributors(contribLast);
        };
      }
    } else {
      more.hidden = true;
      more.innerHTML = "";
    }
  }

  if (moreSv1) {
    if (sv1RestN > 0) {
      moreSv1.hidden = false;
      moreSv1.innerHTML = contribShowAllSv1
        ? `<button type="button" id="contribToggleSv1">Show hashing now only (${sv1Live.length})</button>` +
          ` <span class="muted">${allSv1.length} SV1 in window</span>`
        : `<button type="button" id="contribToggleSv1">Show all in window (+${sv1RestN} idle/offline)</button>` +
          ` <span class="muted">${sv1Live.length} hashing now</span>`;
      const btn = document.getElementById("contribToggleSv1");
      if (btn) {
        btn.onclick = () => {
          contribShowAllSv1 = !contribShowAllSv1;
          saveContribShowAllSv1(contribShowAllSv1);
          renderContributors(contribLast);
        };
      }
    } else {
      moreSv1.hidden = true;
      moreSv1.innerHTML = "";
    }
  }
}

function ageFromAt(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  return Math.max(0, (Date.now() - t) / 1000);
}

function renderBlocksTable(blocks, bodyId, info) {
  const bbody = document.getElementById(bodyId);
  if (!bbody) return;
  if (!blocks.length) {
    bbody.innerHTML = `<tr><td colspan="8" class="muted">No pool blocks yet</td></tr>`;
    return;
  }
  bbody.innerHTML = blocks
    .map((b) => {
      const st = (b && b.status) || "confirmed";
      const orphaned = st === "orphaned" || st === "misattributed";
      const pending = st === "pending";
      let rowClass = "";
      const mode = ((b && b.payout_mode) || "");
      const manualPending =
        !orphaned &&
        (mode === "ops_manual" || mode === "needs_review") &&
        !(b && b.manual_payout_done);
      if (orphaned) rowClass = ' class="row-orphan"';
      else if (pending) rowClass = ' class="row-pending"';
      else if (manualPending && mode === "needs_review") rowClass = ' class="row-review"';
      else if (manualPending) rowClass = ' class="row-manual"';
      const reward = orphaned
        ? "-"
        : `<span title="${fmtBtcTitle(b.reward_sats)}">${fmtBtc(b.reward_sats)}</span>`;

      const href = mempoolBlockHref(b, info);
      const hashOk = b.block_hash && /^[0-9a-fA-F]{64}$/.test(String(b.block_hash));
      const heightCell = hashOk
        ? `<a class="mono" href="${href}" target="_blank" rel="noopener" title="${b.block_hash || ""}">${b.height}</a>`
        : `<span class="mono" title="${b.block_hash || ""}">${b.height}</span>`;
      const nick = (b.finder_nickname || "").trim();
      const addr = b.finder_address || "";
      const addrCell = addr
        ? `<a class="mono truncate" href="/address?a=${encodeURIComponent(addr)}" title="${addr}">${shortAddr(addr)}</a>`
        : `<span class="muted">—</span>`;
      const ageSec = ageFromAt(b.accounted_at);
      const whenLocal = b.accounted_at ? fmtLocalTime(b.accounted_at) : "";
      const agoCell =
        ageSec == null
          ? "—"
          : `<span title="${whenLocal}">${fmtAge(ageSec)}</span>`;
      return `<tr${rowClass}>
        <td>${heightCell}</td>
        <td>${blockStatusBadge(b, info)}</td>
        ${blocksFinderWorkerCell(b)}
        ${clipCell(nick, { title: nick ? `Nickname: ${nick}` : "", wide: true })}
        <td>${addrCell}</td>
        <td>${reward}</td>
        <td>${fmtInt(b.difficulty)}</td>
        <td>${agoCell}</td>
      </tr>`;
    })
    .join("");
  rememberFinderWorkers(blocks);
  bindManualAdjustmentClicks(blocks, info);
}

/* --- Charts (Chart.js) -------------------------------------------------- */
let poolChartRange = "24h";
let userChartRange = "24h";
let poolChartObj = null;
let userChartObj = null;
let tidesInfo = {};
const SHARES_PAGE_SIZE = 25;
let sharesPageOffset = 0;
let sharesHasMore = false;
let sharesAddress = "";

function chartReady() {
  return typeof Chart !== "undefined";
}

/** Humanize chart span (payout window can be hours…weeks). */
function fmtChartSpan(sec) {
  const s = Math.max(0, Number(sec) || 0);
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`;
  if (s < 48 * 3600) return `${(s / 3600).toFixed(s < 10 * 3600 ? 1 : 0)}h`;
  return `${(s / 86400).toFixed(s < 10 * 86400 ? 1 : 0)}d`;
}

function hsAxisMax(seriesList) {
  let m = 0;
  for (const s of seriesList) {
    for (const p of s || []) {
      const v = Number(p.hs || 0);
      if (v > m) m = v;
    }
  }
  return m > 0 ? m * 1.15 : 1;
}

/** Network Y max in H/s: follow data (ceil to whole PH) so the line is not clipped. */
function netAxisMaxHs(networkSeries) {
  const raw = hsAxisMax([networkSeries || []]);
  // At least 1 PH; ceil to next whole PH for readable ticks (0,1,2,…).
  const ph = Math.max(1, Math.ceil(raw / 1e15));
  return ph * 1e15;
}

/** Tick step for network axis (~4–8 labels). */
function netAxisStepHs(axisMaxHs) {
  const ph = Math.max(1, axisMaxHs / 1e15);
  if (ph <= 6) return 1e15;
  if (ph <= 12) return 2e15;
  if (ph <= 30) return 5e15;
  return 10e15;
}

function blockScatter(blocks, yMax, { inWindowOnly = null } = {}) {
  const y = yMax * 0.92 || 1;
  return (blocks || [])
    .filter((b) => {
      if (inWindowOnly === true) return !!b.in_window;
      if (inWindowOnly === false) return !b.in_window;
      return true;
    })
    .map((b) => ({
      x: Number(b.t) * 1000,
      y,
      height: b.height,
      block_hash: b.block_hash,
      worker: b.worker,
      nickname: b.nickname,
      status: b.status,
      in_window: !!b.in_window,
    }));
}

/**
 * X-range from series. Pad the right edge so find markers (r≈6) at "now"
 * are not clipped for the first few minutes after a find.
 */
function chartXBounds(data) {
  const xs = [];
  for (const p of data.pool || []) xs.push(Number(p.t) * 1000);
  for (const p of data.network || []) {
    if (Number(p.hs) > 0) xs.push(Number(p.t) * 1000);
  }
  for (const b of data.blocks || []) xs.push(Number(b.t) * 1000);
  let min;
  let max;
  // Prefer the requested series span from pool buckets when present
  if ((data.pool || []).length >= 2) {
    const a = Number(data.pool[0].t) * 1000;
    const b = Number(data.pool[data.pool.length - 1].t) * 1000;
    min = Math.min(a, b);
    max = Math.max(a, b);
  } else if (!xs.length) {
    return {};
  } else {
    min = Math.min(...xs);
    max = Math.max(...xs);
  }
  // Also cover any find past the last bucket (fresh block at tip).
  if (xs.length) {
    max = Math.max(max, Math.max(...xs));
    min = Math.min(min, Math.min(...xs));
  }
  const span = Math.max(0, max - min);
  // ~3% of the window (floor 15m, cap 6h). On 7d a fixed 45m pad was only
  // ~3px and still clipped r=6 find dots; percent-of-span keeps both ranges honest.
  const rightPad = Math.min(
    Math.max(span * 0.03, 15 * 60 * 1000),
    6 * 3600 * 1000
  );
  return { min, max: max + rightPad };
}

/** Soft vertical band for the payout window (7 confirmed + current). */
const payoutWindowBandPlugin = {
  id: "payoutWindowBand",
  beforeDraw(chart, _args, opts) {
    const win = opts && opts.window;
    if (!win || win.start_t == null || win.end_t == null) return;
    const { ctx, chartArea, scales } = chart;
    if (!chartArea || !scales.x) return;
    const x0 = scales.x.getPixelForValue(Number(win.start_t) * 1000);
    const x1 = scales.x.getPixelForValue(Number(win.end_t) * 1000);
    const left = Math.max(chartArea.left, Math.min(x0, x1));
    const right = Math.min(chartArea.right, Math.max(x0, x1));
    if (!(right > left)) return;
    ctx.save();
    ctx.fillStyle = "rgba(61, 214, 198, 0.10)";
    ctx.fillRect(left, chartArea.top, right - left, chartArea.bottom - chartArea.top);
    ctx.strokeStyle = "rgba(61, 214, 198, 0.45)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(left, chartArea.top);
    ctx.lineTo(left, chartArea.bottom);
    ctx.stroke();
    // Label at top of band
    const label = win.label || "Payout window";
    ctx.fillStyle = "rgba(61, 214, 198, 0.9)";
    ctx.font = "600 11px system-ui, sans-serif";
    const tw = ctx.measureText(label).width;
    const tx = Math.min(Math.max(left + 6, chartArea.left + 4), chartArea.right - tw - 4);
    ctx.fillText(label, tx, chartArea.top + 14);
    ctx.restore();
  },
};

/** Faded dotted stems from timeline up to find markers. */
const findStemPlugin = {
  id: "findStems",
  afterDatasetsDraw(chart, _args, opts) {
    const finds = (opts && opts.finds) || [];
    if (!finds.length) return;
    const { ctx, chartArea, scales } = chart;
    if (!chartArea || !scales.x || !scales.yPool) return;
    ctx.save();
    ctx.setLineDash([3, 4]);
    ctx.lineWidth = 1;
    for (const f of finds) {
      const x = scales.x.getPixelForValue(f.x);
      if (x < chartArea.left - 1 || x > chartArea.right + 1) continue;
      const yDot = scales.yPool.getPixelForValue(f.y);
      ctx.strokeStyle = f.in_window
        ? "rgba(240, 180, 41, 0.55)"
        : "rgba(240, 180, 41, 0.28)";
      ctx.beginPath();
      ctx.moveTo(x, chartArea.bottom);
      ctx.lineTo(x, yDot);
      ctx.stroke();
    }
    ctx.restore();
  },
};

function destroyChart(ref) {
  if (ref && typeof ref.destroy === "function") {
    try {
      ref.destroy();
    } catch (_) {}
  }
  return null;
}

function wireChartRanges(id, onPick) {
  const root = document.getElementById(id);
  if (!root) return;
  root.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-range]");
    if (!btn) return;
    root.querySelectorAll("button[data-range]").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    onPick(btn.getAttribute("data-range") || "24h");
  });
}

async function loadPoolChart(range) {
  if (!chartReady()) return;
  const canvas = document.getElementById("poolChart");
  if (!canvas) return;
  const data = await jget("/api/charts/pool?range=" + encodeURIComponent(range || "24h"));
  const poolMax = hsAxisMax([data.pool]);
  // Follow network HR (was hard-capped at 5 PH/s and clipped once tip rose above that).
  const netAxisMax = netAxisMaxHs(data.network);
  const netAxisStep = netAxisStepHs(netAxisMax);
  const findsIn = blockScatter(data.blocks, poolMax, { inWindowOnly: true });
  const findsOut = blockScatter(data.blocks, poolMax, { inWindowOnly: false });
  const win = data.window || null;
  const netSrc = data.network_source || "tip";
  const netLabel =
    netSrc === "samples"
      ? "Network"
      : "Network (tracking from now — history fills in over time)";
  const sub = document.querySelector("#poolChartBox .chart-sub");
  // Legend covers pool / network / finds — no prose subtitle.
  if (sub) {
    sub.hidden = true;
    sub.textContent = "";
  }
  const xBound = chartXBounds(data);
  const allFinds = findsIn.concat(findsOut);
  // Split solid history vs dashed open tip (incomplete bucket estimate).
  const poolPts = (data.pool || []).map((p) => ({
    x: p.t * 1000,
    y: p.hs,
    estimated: !!p.estimated,
  }));
  const netPts = (data.network || [])
    .filter((p) => Number(p.hs) > 0)
    .map((p) => ({
      x: p.t * 1000,
      y: p.hs,
      estimated: !!p.estimated,
    }));
  const poolSolid = poolPts.filter((p) => !p.estimated);
  const netSolid = netPts.filter((p) => !p.estimated);
  const poolTip =
    poolPts.length && poolPts[poolPts.length - 1].estimated
      ? poolSolid.slice(-1).concat(poolPts.slice(-1))
      : [];
  const netTip =
    netPts.length && netPts[netPts.length - 1].estimated
      ? netSolid.slice(-1).concat(netPts.slice(-1))
      : [];
  poolChartObj = destroyChart(poolChartObj);
  poolChartObj = new Chart(canvas.getContext("2d"), {
    plugins: [payoutWindowBandPlugin, findStemPlugin],
    data: {
      datasets: [
        {
          type: "line",
          label: "Pool",
          yAxisID: "yPool",
          data: poolSolid,
          borderColor: "#3dd6c6",
          backgroundColor: "rgba(61,214,198,0.12)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.25,
          fill: true,
        },
        {
          type: "line",
          label: "Pool (est.)",
          yAxisID: "yPool",
          data: poolTip,
          borderColor: "#3dd6c6",
          borderWidth: 2,
          borderDash: [6, 4],
          pointRadius: 0,
          tension: 0,
          fill: false,
        },
        {
          type: "line",
          label: netLabel,
          yAxisID: "yNet",
          data: netSolid,
          borderColor: "#6ea8ff",
          borderWidth: 1.5,
          borderDash: netSrc === "samples" ? undefined : [4, 3],
          pointRadius: 0,
          tension: 0.2,
          fill: false,
          spanGaps: false,
        },
        {
          type: "line",
          label: "Network (est.)",
          yAxisID: "yNet",
          data: netTip,
          borderColor: "#6ea8ff",
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          tension: 0,
          fill: false,
          spanGaps: false,
        },
        {
          type: "scatter",
          label: "Block found",
          yAxisID: "yPool",
          data: findsIn,
          backgroundColor: "#f0b429",
          borderColor: "#ffe08a",
          borderWidth: 2,
          pointRadius: 6,
          pointHoverRadius: 8,
          pointStyle: "circle",
        },
        {
          type: "scatter",
          label: "Older finds",
          yAxisID: "yPool",
          data: findsOut,
          backgroundColor: "rgba(240,180,41,0.35)",
          borderColor: "rgba(240,180,41,0.5)",
          pointRadius: 3.5,
          pointHoverRadius: 5,
          pointStyle: "circle",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { left: 0, right: 12, top: 4, bottom: 0 } },
      interaction: { mode: "x", intersect: false },
      onHover(evt, els) {
        const tip = els && els.length ? els[0] : null;
        const ds = tip && poolChartObj?.data?.datasets?.[tip.datasetIndex];
        const clickable =
          ds && (ds.label === "Block found" || ds.label === "Older finds");
        evt.native && (evt.native.target.style.cursor = clickable ? "pointer" : "default");
      },
      onClick(_evt, els) {
        if (!els || !els.length) return;
        const tip = els[0];
        const ds = poolChartObj?.data?.datasets?.[tip.datasetIndex];
        if (!ds || (ds.label !== "Block found" && ds.label !== "Older finds")) return;
        const raw = ds.data[tip.index];
        if (!raw) return;
        const href = mempoolBlockHref(
          { height: raw.height, block_hash: raw.block_hash },
          tidesInfo
        );
        window.open(href, "_blank", "noopener");
      },
      plugins: {
        payoutWindowBand: { window: win },
        findStems: { finds: allFinds },
        legend: {
          labels: {
            color: "#c5d0e6",
            boxWidth: 12,
            usePointStyle: true,
            pointStyle: "circle",
            filter(item) {
              const t = item.text || "";
              return t !== "Pool (est.)" && t !== "Network (est.)";
            },
          },
        },
        tooltip: {
          mode: "x",
          intersect: false,
          callbacks: {
            title(items) {
              const x = items && items[0] && items[0].parsed && items[0].parsed.x;
              if (x == null) return "";
              try {
                return fmtLocalTime(new Date(x).toISOString());
              } catch {
                return new Date(x).toLocaleString();
              }
            },
            label(ctx) {
              const lab = ctx.dataset.label || "";
              // Hide tip-estimate series when it only duplicates the solid point at same x
              if (lab === "Pool (est.)" || lab === "Network (est.)") {
                if (!(ctx.raw && ctx.raw.estimated)) return null;
                const y = ctx.parsed && ctx.parsed.y;
                const name = lab.startsWith("Pool") ? "Pool" : "Network";
                return ` ${name}: ${fmtHashrate(y)} · est. (bucket filling)`;
              }
              if (lab === "Pool" || lab.startsWith("Network")) {
                // If an est. point exists at this index tip, solid series still shows; fine.
                const y = ctx.parsed && ctx.parsed.y;
                return ` ${lab}: ${fmtHashrate(y)}`;
              }
              if (ctx.dataset.label === "Block found" || ctx.dataset.label === "Older finds") {
                const r = ctx.raw || {};
                const nick = r.nickname ? ` · ${r.nickname}` : "";
                const tag = r.in_window ? " (in window)" : "";
                return `Block ${r.height}${nick}${r.worker ? " · " + r.worker : ""}${tag} · click → mempool`;
              }
              if (ctx.dataset.yAxisID === "yNet") {
                return `${ctx.dataset.label}: ${fmtHashratePH(ctx.parsed.y)}`;
              }
              return `${ctx.dataset.label}: ${fmtHashrate(ctx.parsed.y)}`;
            },
          },
        },
      },
      scales: {
        x: {
          type: "linear",
          min: xBound.min,
          max: xBound.max,
          bounds: "ticks",
          ticks: {
            color: "#8b9bb8",
            maxTicksLimit: 8,
            callback(v) {
              const d = new Date(v);
              return d.toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              });
            },
          },
          grid: { color: "rgba(36,48,73,0.7)" },
        },
        yPool: {
          position: "left",
          title: { display: true, text: "Pool (TH/s)", color: "#3dd6c6" },
          ticks: {
            color: "#3dd6c6",
            callback(v) {
              return fmtAxisTH(v);
            },
          },
          grid: { color: "rgba(36,48,73,0.55)" },
          suggestedMax: poolMax,
        },
        yNet: {
          position: "right",
          title: { display: true, text: "Network (PH/s)", color: "#6ea8ff" },
          min: 0,
          max: netAxisMax,
          ticks: {
            color: "#6ea8ff",
            stepSize: netAxisStep,
            callback(v) {
              return fmtAxisPH(v);
            },
          },
          grid: { drawOnChartArea: false },
        },
      },
    },
  });
}

const WORKER_CHART_COLORS = [
  "#3dd6c6",
  "#6ea8ff",
  "#f0b429",
  "#e879f9",
  "#34d399",
  "#fb7185",
  "#a78bfa",
  "#fbbf24",
];
let userChartData = null;
let userWorkerVisible = {}; // worker -> bool; empty = all on
let userWorkerAddr = "";
const USER_WORKER_VIS_KEY = "tides_miner_worker_vis";

function loadUserWorkerVisible(address) {
  try {
    const all = JSON.parse(sessionStorage.getItem(USER_WORKER_VIS_KEY) || "{}");
    const saved = all[address];
    return saved && typeof saved === "object" ? { ...saved } : {};
  } catch {
    return {};
  }
}

function saveUserWorkerVisible(address, vis) {
  try {
    const all = JSON.parse(sessionStorage.getItem(USER_WORKER_VIS_KEY) || "{}");
    all[address] = vis;
    sessionStorage.setItem(USER_WORKER_VIS_KEY, JSON.stringify(all));
  } catch {
    /* ignore */
  }
}

function renderUserWorkerFilters(workers) {
  const el = document.getElementById("userWorkerFilters");
  if (!el) return;
  if (!workers || workers.length < 2) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  el.innerHTML =
    `<label><input type="checkbox" data-worker="__all__" ${
      Object.keys(userWorkerVisible).length === 0 ||
      workers.every((w) => userWorkerVisible[w] !== false)
        ? "checked"
        : ""
    }/> All</label>` +
    workers
      .map((w, i) => {
        const on = userWorkerVisible[w] !== false;
        const color = WORKER_CHART_COLORS[i % WORKER_CHART_COLORS.length];
        return `<label><input type="checkbox" data-worker="${escapeHtml(w)}" ${
          on ? "checked" : ""
        }/> <span style="color:${color}">${escapeHtml(w)}</span></label>`;
      })
      .join("");
  el.querySelectorAll('input[type="checkbox"]').forEach((inp) => {
    inp.addEventListener("change", () => {
      const key = inp.getAttribute("data-worker");
      if (key === "__all__") {
        const on = inp.checked;
        workers.forEach((w) => {
          userWorkerVisible[w] = on;
        });
        el.querySelectorAll('input[data-worker]:not([data-worker="__all__"])').forEach(
          (x) => {
            x.checked = on;
          }
        );
      } else {
        userWorkerVisible[key] = inp.checked;
        const allBox = el.querySelector('input[data-worker="__all__"]');
        if (allBox) {
          allBox.checked = workers.every((w) => userWorkerVisible[w] !== false);
        }
      }
      if (userWorkerAddr) saveUserWorkerVisible(userWorkerAddr, userWorkerVisible);
      if (userChartData) paintUserChart(userChartData);
    });
  });
}

function paintUserChart(data) {
  const canvas = document.getElementById("userChart");
  if (!canvas || !chartReady()) return;
  const byW = data.hashrate_by_worker || {};
  const workers = data.workers || Object.keys(byW);
  const seriesList = [];
  if (workers.length >= 2) {
    workers.forEach((w, i) => {
      if (userWorkerVisible[w] === false) return;
      const series = byW[w] || [];
      seriesList.push(series);
    });
  } else {
    seriesList.push(data.hashrate || []);
  }
  const yMax = hsAxisMax(seriesList);
  const finds = blockScatter(data.blocks, yMax);
  const xBound = chartXBounds({
    pool: data.hashrate,
    network: [],
    blocks: data.blocks,
  });
  const datasets = [];
  if (workers.length >= 2) {
    workers.forEach((w, i) => {
      if (userWorkerVisible[w] === false) return;
      const color = WORKER_CHART_COLORS[i % WORKER_CHART_COLORS.length];
      datasets.push({
        type: "line",
        label: w,
        yAxisID: "yPool",
        data: (byW[w] || []).map((p) => ({ x: p.t * 1000, y: p.hs })),
        borderColor: color,
        backgroundColor: "transparent",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.25,
        fill: false,
      });
    });
  } else {
    datasets.push({
      type: "line",
      label: "Your HR",
      yAxisID: "yPool",
      data: (data.hashrate || []).map((p) => ({ x: p.t * 1000, y: p.hs })),
      borderColor: "#3dd6c6",
      backgroundColor: "rgba(61,214,198,0.14)",
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.25,
      fill: true,
    });
  }
  datasets.push({
    type: "scatter",
    label: "Your finds",
    yAxisID: "yPool",
    data: finds,
    backgroundColor: "#f0b429",
    borderColor: "#f0b429",
    pointRadius: 5,
    pointHoverRadius: 7,
  });
  userChartObj = destroyChart(userChartObj);
  userChartObj = new Chart(canvas.getContext("2d"), {
    plugins: [findStemPlugin],
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { left: 0, right: 12, top: 4, bottom: 0 } },
      interaction: { mode: "nearest", intersect: true },
      onHover(evt, els) {
        const tip = els && els.length ? els[0] : null;
        const ds = tip && userChartObj?.data?.datasets?.[tip.datasetIndex];
        evt.native &&
          (evt.native.target.style.cursor =
            ds && ds.label === "Your finds" ? "pointer" : "default");
      },
      onClick(_evt, els) {
        if (!els || !els.length) return;
        const tip = els[0];
        const ds = userChartObj?.data?.datasets?.[tip.datasetIndex];
        if (!ds || ds.label !== "Your finds") return;
        const raw = ds.data[tip.index];
        if (!raw) return;
        window.open(
          mempoolBlockHref(
            { height: raw.height, block_hash: raw.block_hash },
            tidesInfo
          ),
          "_blank",
          "noopener"
        );
      },
      plugins: {
        findStems: { finds },
        legend: { labels: { color: "#c5d0e6", boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label(ctx) {
              if (ctx.dataset.label === "Your finds") {
                const r = ctx.raw || {};
                return `Block ${r.height}${r.worker ? " · " + r.worker : ""} · click → mempool`;
              }
              return `${ctx.dataset.label}: ${fmtHashrateTH(ctx.parsed.y)}`;
            },
          },
        },
      },
      scales: {
        x: {
          type: "linear",
          min: xBound.min,
          max: xBound.max,
          ticks: {
            color: "#8b9bb8",
            maxTicksLimit: 8,
            callback(v) {
              const d = new Date(v);
              return d.toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              });
            },
          },
          grid: { color: "rgba(36,48,73,0.7)" },
        },
        yPool: {
          position: "left",
          title: { display: true, text: "Your HR (TH/s)", color: "#8b9bb8" },
          ticks: {
            color: "#8b9bb8",
            callback(v) {
              return fmtAxisTH(v);
            },
          },
          grid: { color: "rgba(36,48,73,0.55)" },
          suggestedMax: yMax || undefined,
        },
      },
    },
  });
}

async function loadUserChart(address, range) {
  if (!chartReady() || !address) return;
  const canvas = document.getElementById("userChart");
  if (!canvas) return;
  const data = await jget(
    "/api/user/" +
      encodeURIComponent(address) +
      "/charts?range=" +
      encodeURIComponent(range || "24h")
  );
  userChartData = data;
  const workers = data.workers || [];
  renderUserWorkerFilters(workers);
  const userSub = document.querySelector("#userChartBox .chart-sub") ||
    document.querySelector("#minerPanel .chart-sub");
  if (userSub) {
    const spanSec = Number(data.range_sec || 0);
    const base =
      workers.length > 1
        ? `Per-worker lines · toggle above · markers = your finds`
        : "From your accepted shares · axis in TH/s · markers = your finds";
    userSub.textContent =
      data.range === "window" && spanSec > 0
        ? `${base} · x-axis = payout window (~${fmtChartSpan(spanSec)})`
        : base;
  }
  paintUserChart(data);
}

async function loadPool() {
  // Everything in parallel — public site is snap-first (instant) with a
  // background live cache. Paint coinbaser as soon as it lands so the page
  // never waits on a slow live proxy the way the old sync path did.
  const contribUrl = "/api/contributors?limit=500&offset=0";
  const coinbaserP = jget("/api/coinbaser")
    .then((coinbaser) => {
      renderCoinbaser(coinbaser);
      return coinbaser;
    })
    .catch((e) => {
      console.error(e);
      renderCoinbaser(null);
      return null;
    });

  const settled = await Promise.allSettled([
    jget("/api/stats"),
    jgetRes(contribUrl),
    jget("/api/blocks?limit=8"),
    jget("/api/info"),
    coinbaserP,
  ]);
  const val = (i, fallback) =>
    settled[i].status === "fulfilled" ? settled[i].value : fallback;
  const stats = val(0, null);
  const contribPack = val(1, { data: [], headers: new Headers() });
  const contrib = Array.isArray(contribPack.data)
    ? contribPack.data
    : contribPack.data || [];
  const xt = contribPack.headers && contribPack.headers.get("X-Total-Count");
  if (xt != null && xt !== "") contribTotal = Number(xt) || contrib.length;
  else contribTotal = contrib.length;
  const blocks = val(2, []);
  const info = val(3, {});
  tidesInfo = info || {};
  if (!stats) {
    throw settled[0].reason || new Error("stats failed");
  }
  for (let i = 1; i < settled.length; i++) {
    if (settled[i].status === "rejected") {
      console.error("pool partial load failed", settled[i].reason);
    }
  }

  const lastAge =
    stats.last_pool_block_age_sec != null
      ? fmtAge(stats.last_pool_block_age_sec)
      : stats.last_pool_block_height != null
        ? "found"
        : "none yet";
  const lastFindTip =
    stats.last_pool_block_height != null
      ? `Last pool find age (height ${stats.last_pool_block_height} — for reference only)`
      : "No pool find yet";
  const netHs = Number(stats.network_hashrate_hs || 0);
  const sharePct = Number(stats.pool_network_share_pct || 0);
  const etaSec = stats.est_block_time_sec;
  const minersInWindow = Number(stats.addresses_in_window || 0);
  const contribRows = Array.isArray(contrib) ? contrib : [];
  // Current = live now, split by path (DATUM vs Stratum/SV1 subsection — not port count).
  // Dual-path addresses can appear in both Current columns.
  const activeDatum = contribRows.filter(
    (c) => isContribLive(c) && isDatumPathMember(c) && !isOpsFeeContrib(c)
  ).length;
  // Stratum = live SV1 subsection payees (not "1 port"). Skip ops-fee synthetic row.
  const activeStratum = contribRows.filter(
    (c) =>
      isContribLive(c) && isStratumEndpointMember(c) && !isOpsFeeContrib(c)
  ).length;
  const hs1h = Number(stats.hashrate_hs_1h || 0);
  const estPerThs = Number(stats.est_sats_per_day_per_ths);
  const estPerThsTip =
    "Rough estimate only: (7-day find rate) × (block reward) × (1 TH/s ÷ pool hashrate). " +
    "Not a promise — swings with pool luck, network difficulty, and pool hashrate; " +
    "TIDES window dilution also differs from this simple model.";
  const estPerThsVal = Number.isFinite(estPerThs) && estPerThs > 0
    ? `<span title="${estPerThsTip.replace(/"/g, "&quot;")}">≈ ${fmtBtc(estPerThs)}/day</span>`
    : `<span title="${estPerThsTip.replace(/"/g, "&quot;")}">—</span>`;
  document.getElementById("poolCards").innerHTML = [
    card(
      "Network",
      cardSplitValue(
        fmtHashrate(netHs),
        "Estimated",
        fmtSharePct(sharePct),
        "our share",
        "Estimated network hashrate from Knots getnetworkhashps (~last 120 blocks) · pool share = pool HR / network HR"
      )
    ),
    card(
      "Pool hashrate",
      cardSplitValue(
        fmtHashrate(stats.hashrate_hs),
        "10 min",
        fmtHashrate(hs1h),
        "1 hour",
        "Pool hashrate from accepted share work · 10-minute and 1-hour averages"
      )
    ),
    card(
      "Block finds",
      cardSplitValue(
        `<span title="Estimated wait until the next pool block at current pool hashrate (diff × 2³² / pool HR). Luck varies.">${fmtDuration(etaSec)}</span>`,
        "Next · estimated",
        `<span title="${String(lastFindTip).replace(/"/g, "&quot;")}">${lastAge}</span>`,
        "Last",
        "Estimated time to next pool block · age of last pool find"
      )
    ),
    card(
      "Miners",
      cardSplitValue(
        fmtInt(activeDatum),
        "active DATUM",
        fmtInt(activeStratum),
        "active Stratum",
        fmtInt(minersInWindow),
        "in window",
        "Active = hashing now (~10 min): DATUM path vs Stratum (SV1) subsection · In window = all payout addresses with work in the TIDES window (DATUM+Stratum combined)"
      )
    ),
    card(
      "Blocks found",
      cardSplitValue(
        `${fmtInt(stats.blocks_last_24h)}<span class="split-luck">${fmtLuckPct(stats.luck_24h_pct)}</span>`,
        "24h",
        `${fmtInt(stats.blocks_last_7d ?? stats.blocks_last_24h)}<span class="split-luck">${fmtLuckPct(stats.luck_7d_pct)}</span>`,
        "1wk",
        `${fmtInt(stats.blocks_all_time ?? 0)}<span class="split-luck">${fmtLuckPct(stats.luck_all_pct)}</span>`,
        "all",
        "Luck% = 100 × Σ(network difficulty of finds) ÷ pool share-work in that period (handles retargets). 100% = expected. Orphans excluded from find counts."
          + (Number(stats.orphans_last_24h || 0) ||
            Number(stats.orphans_last_7d || 0) ||
            Number(stats.orphans_all_time || 0)
            ? ` · Orphans 24h ${fmtInt(stats.orphans_last_24h || 0)} · 1wk ${fmtInt(stats.orphans_last_7d || 0)} · all ${fmtInt(stats.orphans_all_time || 0)}`
            : "")
      )
    ),
    card(
      "Rough estimate @ 1 TH/s",
      estPerThsVal,
      false,
      "Varies with pool luck, network difficulty, and pool hashrate"
    ),
  ].join("");
  renderFeeFootnote(stats);

  // Prime finder ★ keys before contrib rows so worker machines get the star.
  rememberFinderWorkers(blocks);
  renderContributors(contrib);

  renderBlocksTable(blocks, "blocksBody", info);

  const foot = document.getElementById("footerMeta");
  if (foot) {
    foot.textContent = `${info.name || "tides-pool"} ${info.version || ""} · ${stats.pool_name || ""}`;
  }

  const j = info.join || {};
  const pre = document.getElementById("joinPre");
  if (pre) {
    pre.textContent = JSON.stringify(
      {
        datum: {
          pool_host: j.pool_host || "tides.maveth.ca",
          pool_port: j.pool_port || 28916,
          pool_pubkey: j.pool_pubkey || "(paste 128-hex pubkey — required)",
          pooled_mining_only: false,
        },
      },
      null,
      2
    );
  }

  try {
    await loadPoolChart(poolChartRange);
  } catch (e) {
    console.error("pool chart", e);
  }
  // Live block list may be ahead of snapshotted chart — pin find markers on the graph.
  mergeLiveFindsIntoPoolChart(blocks);
  const findH = Number(stats.last_pool_block_height || 0);
  if (findH > 0 && watchedFindHeight == null) watchedFindHeight = findH;
}

/** Last pool-find height the watcher has reacted to. */
let watchedFindHeight = null;
let findSnapRefreshBusy = false;
const FIND_WATCH_MS = 15000;

/** Inject live /api/blocks finds onto the pool chart (snap chart may lag). */
function mergeLiveFindsIntoPoolChart(apiBlocks) {
  if (!poolChartObj || !Array.isArray(apiBlocks) || !apiBlocks.length) return;
  const dsIn = (poolChartObj.data.datasets || []).find(
    (d) => d && d.label === "Block found"
  );
  if (!dsIn) return;
  const existing = new Set((dsIn.data || []).map((p) => Number(p.height)));
  let y =
    (dsIn.data && dsIn.data[0] && Number(dsIn.data[0].y)) ||
    (poolChartObj.scales &&
      poolChartObj.scales.yPool &&
      poolChartObj.scales.yPool.max * 0.92) ||
    1;
  let added = 0;
  for (const b of apiBlocks) {
    const height = Number(b && b.height);
    if (!height || existing.has(height)) continue;
    const ms = b.accounted_at ? Date.parse(b.accounted_at) : NaN;
    if (!Number.isFinite(ms)) continue;
    dsIn.data.push({
      x: ms,
      y,
      height,
      block_hash: b.block_hash,
      worker: b.finder_worker,
      nickname: b.finder_nickname,
    });
    existing.add(height);
    added += 1;
  }
  if (added) poolChartObj.update("none");
}

async function requestSnapRefresh() {
  try {
    await fetch("/api/snap/refresh", { method: "POST", cache: "no-store" });
  } catch (e) {
    console.error("snap refresh", e);
  }
}

/** After a find: live cards/blocks now; keep reloading until contrib snap catches up. */
async function refreshAfterNewFind() {
  if (findSnapRefreshBusy) return;
  findSnapRefreshBusy = true;
  try {
    await requestSnapRefresh();
    const path = (location.pathname || "/").replace(/\/+$/, "") || "/";
    const addr = qs("a");
    const beforeAsOf = (() => {
      try {
        return document.getElementById("snapFreshness")?.getAttribute("data-asof") || "";
      } catch (_) {
        return "";
      }
    })();
    for (let i = 0; i < 12; i++) {
      if (path === "/blocks") await loadBlocksPage();
      else if (!addr) await loadPool();
      else await loadUser(addr);
      // Snap rebuild usually finishes in ~2–4 min; poll often early for contrib/this-block.
      await new Promise((r) => setTimeout(r, i < 4 ? 8000 : 15000));
      try {
        const meta = await jget("/api/meta");
        const asOf = (meta && meta.as_of) || "";
        if (asOf && asOf !== beforeAsOf) {
          if (path === "/blocks") await loadBlocksPage();
          else if (!addr) await loadPool();
          else await loadUser(addr);
          break;
        }
      } catch (_) {
        /* keep trying */
      }
    }
  } finally {
    findSnapRefreshBusy = false;
  }
}

async function pollForNewFinds() {
  if (document.visibilityState === "hidden") return;
  try {
    const stats = await jget("/api/stats");
    const h = Number(stats && stats.last_pool_block_height) || 0;
    if (!h) return;
    if (watchedFindHeight == null) {
      watchedFindHeight = h;
      return;
    }
    if (h <= watchedFindHeight) return;
    watchedFindHeight = h;
    // No banner — kick snap rebuild + refresh cards/blocks/contrib/chart.
    refreshAfterNewFind().catch((e) => console.error("find refresh", e));
  } catch (e) {
    console.error("find watch", e);
  }
}

async function loadBlocksPage() {
  document.getElementById("poolView").classList.add("hidden");
  document.getElementById("userView").classList.add("hidden");
  document.getElementById("blocksView").classList.remove("hidden");
  document.title = "RIPTIDE · Pool blocks";

  const settled = await Promise.allSettled([
    jget("/api/blocks?limit=100"),
    jget("/api/info"),
    jget("/api/stats"),
  ]);
  const val = (i, fallback) =>
    settled[i].status === "fulfilled" ? settled[i].value : fallback;
  const blocks = val(0, []);
  const info = val(1, {});
  const stats = val(2, {});
  if (settled[0].status === "rejected") {
    throw settled[0].reason;
  }
  renderBlocksTable(blocks, "blocksAllBody", info);
  const foot = document.getElementById("footerMeta");
  if (foot) {
    foot.textContent = `${info.name || "tides-pool"} ${info.version || ""} · ${stats.pool_name || ""}`;
  }
}

/** Map a miner payout line → block-shaped object for blockStatusBadge. */
function payoutAsBlock(p) {
  return {
    height: p.height,
    status: p.status || "confirmed",
    payout_mode: p.payout_mode || "onchain_split",
    manual_payout_done: !!(p && p.manual_payout_done),
    manual_payout_note: p.manual_payout_note || null,
    manual_adjustment: p.manual_adjustment || null,
    intended_payout: null,
  };
}

function renderPayoutHistory(payouts) {
  const body = document.getElementById("payoutHistoryBody");
  const hint = document.getElementById("payoutHistoryHint");
  if (!body) return;
  if (!payouts || !payouts.length) {
    body.innerHTML = `<tr><td colspan="5" class="muted">No reconstructed payouts yet</td></tr>`;
    if (hint) hint.textContent = "none yet · click to expand";
    return;
  }
  // Match Total earned: tides lines + paid finder only (exclude unpaid finder).
  const earnedSats = payouts.reduce((a, p) => {
    if (p.kind === "finder" && (p.status === "unpaid" || p.paid_in_height == null)) {
      return a;
    }
    return a + Number(p.sats || 0);
  }, 0);
  const unpaidN = payouts.filter(
    (p) => p.kind === "finder" && (p.status === "unpaid" || p.paid_in_height == null)
  ).length;
  if (hint) {
    hint.textContent =
      `${payouts.length} line(s) · ${fmtBtc(earnedSats)}` +
      (unpaidN ? ` · ${unpaidN} unpaid finder` : "") +
      " · click to expand";
  }
  body.innerHTML = payouts
    .map((p) => {
      const kind =
        p.kind === "finder"
          ? `<span class="kind-finder-hist">finder</span>`
          : `<span class="kind-tides">tides</span>`;
      // Same status language as Recent pool blocks (manual / review / confirmed).
      let statusHtml;
      if (p.kind === "finder" && p.status === "unpaid") {
        statusHtml = `<span class="badge badge-pending" title="Finder bonus not yet paid in a later coinbase">unpaid</span>`;
      } else {
        statusHtml = blockStatusBadge(payoutAsBlock(p), tidesInfo);
        if (p.kind === "finder" && p.paid_in_height != null) {
          statusHtml += ` <span class="muted" title="Finder bonus paid in this later find">@${p.paid_in_height}</span>`;
        }
      }
      const when = p.accounted_at ? fmtLocalTime(p.accounted_at) : "—";
      const href = mempoolBlockHref(
        { height: p.height, block_hash: p.block_hash },
        tidesInfo
      );
      const hCell = href
        ? `<a href="${href}" target="_blank" rel="noopener" class="mono">${p.height}</a>`
        : `<span class="mono">${p.height}</span>`;
      const mode = String(p.payout_mode || "");
      const manualPending =
        (mode === "ops_manual" || mode === "needs_review") && !p.manual_payout_done;
      const rowClass = manualPending
        ? mode === "needs_review"
          ? ' class="row-review"'
          : ' class="row-manual"'
        : "";
      return `<tr${rowClass}>
        <td>${hCell}</td>
        <td>${kind}</td>
        <td title="${fmtBtcTitle(p.sats)}">${fmtBtc(p.sats)}</td>
        <td>${statusHtml}</td>
        <td class="mono">${when}</td>
      </tr>`;
    })
    .join("");
  // Expandable adj tables — same click handler as main Recent blocks table.
  const asBlocks = payouts.map(payoutAsBlock).filter((b) => b.manual_adjustment);
  if (asBlocks.length) {
    bindManualAdjustmentClicks(asBlocks, tidesInfo);
  }
}

function updateSharesPager() {
  const pager = document.getElementById("sharesPager");
  const prev = document.getElementById("sharesPrev");
  const next = document.getElementById("sharesNext");
  const label = document.getElementById("sharesPageLabel");
  if (!pager || !prev || !next || !label) return;
  const page = Math.floor(sharesPageOffset / SHARES_PAGE_SIZE) + 1;
  const from = sharesPageOffset + 1;
  const to = sharesPageOffset + (window.__sharesPageLen || 0);
  const show = sharesPageOffset > 0 || sharesHasMore || (window.__sharesPageLen || 0) > 0;
  pager.hidden = !show;
  prev.disabled = sharesPageOffset <= 0;
  next.disabled = !sharesHasMore;
  label.textContent =
    (window.__sharesPageLen || 0) > 0
      ? `Page ${page} · ${from}–${to}`
      : "No shares";
}

async function loadSharesPage(address, offset) {
  const sbody = document.getElementById("sharesBody");
  const hint = document.getElementById("sharesHint");
  if (!sbody) return;
  sharesAddress = address;
  sharesPageOffset = Math.max(0, offset | 0);
  const rows = await jget(
    "/api/user/" +
      encodeURIComponent(address) +
      "/shares?limit=" +
      SHARES_PAGE_SIZE +
      "&offset=" +
      sharesPageOffset
  );
  sharesHasMore = rows.length >= SHARES_PAGE_SIZE;
  window.__sharesPageLen = rows.length;
  if (hint) {
    hint.textContent = sharesHasMore
      ? `${SHARES_PAGE_SIZE}/page · older available`
      : `${rows.length || 0} shown · chart covers the trend`;
  }
  if (!rows.length) {
    sbody.innerHTML =
      sharesPageOffset > 0
        ? `<tr><td colspan="4" class="muted">No more shares</td></tr>`
        : `<tr><td colspan="4" class="muted">No shares for this address yet</td></tr>`;
  } else {
    sbody.innerHTML = rows
      .map((s) => {
        const worker = (s.worker || "").trim();
        return `<tr>
        <td class="mono">${s.seq}</td>
        ${clipCell(worker, { title: worker ? `Stratum worker: ${worker}` : "", mono: true })}
        <td>${fmtInt(s.work)}</td>
        <td class="mono">${fmtLocalTime(s.accepted_at)}</td>
      </tr>`;
      })
      .join("");
  }
  updateSharesPager();
}

function wireSharesPager() {
  const prev = document.getElementById("sharesPrev");
  const next = document.getElementById("sharesNext");
  if (prev && !prev.dataset.wired) {
    prev.dataset.wired = "1";
    prev.addEventListener("click", () => {
      if (!sharesAddress || sharesPageOffset <= 0) return;
      loadSharesPage(
        sharesAddress,
        Math.max(0, sharesPageOffset - SHARES_PAGE_SIZE)
      ).catch((e) => console.error(e));
    });
  }
  if (next && !next.dataset.wired) {
    next.dataset.wired = "1";
    next.addEventListener("click", () => {
      if (!sharesAddress || !sharesHasMore) return;
      loadSharesPage(
        sharesAddress,
        sharesPageOffset + SHARES_PAGE_SIZE
      ).catch((e) => console.error(e));
    });
  }
}

async function loadUser(address) {
  document.getElementById("poolView").classList.add("hidden");
  document.getElementById("blocksView").classList.add("hidden");
  document.getElementById("userView").classList.remove("hidden");
  document.getElementById("addrInput").value = address;
  userWorkerAddr = address;
  userWorkerVisible = loadUserWorkerVisible(address);
  userChartData = null;
  wireSharesPager();
  const [user, payouts, stats, info, blocks] = await Promise.all([
    jget("/api/user/" + encodeURIComponent(address)),
    jget("/api/user/" + encodeURIComponent(address) + "/payouts?limit=100").catch(
      (e) => {
        console.error("payouts", e);
        return [];
      }
    ),
    jget("/api/stats"),
    jget("/api/info"),
    jget("/api/blocks?limit=100").catch(() => []),
  ]);
  tidesInfo = info || tidesInfo || {};
  rememberFinderWorkers(Array.isArray(blocks) ? blocks : []);
  document.getElementById("userTitle").innerHTML =
    address + (user.quarantined ? quarantineBadge(user) : "");

  const qCard = user.quarantined
    ? card("Quarantine", user.quarantine_reason || "new shares frozen (coinbase mismatch)", true)
    : card("Reject-27 (last 20)", `${user.reject27_recent || 0} / ${user.attempt_recent || 0}`);

  let lastFindCard;
  if (user.last_find_height != null) {
    const age =
      user.last_find_age_sec != null
        ? fmtAge(user.last_find_age_sec)
        : ageFromAt(user.last_find_at) != null
          ? fmtAge(ageFromAt(user.last_find_at))
          : "—";
    const whenLocal = user.last_find_at ? fmtLocalTime(user.last_find_at) : "";
    lastFindCard = card(
      "Last find",
      `<span title="${whenLocal || "Your most recent pool block as finder"}">${user.last_find_height} · ${age}</span>`
    );
  } else {
    lastFindCard = card("Last find", "none yet");
  }

  document.getElementById("userCards").innerHTML = [
    qCard,
    card(
      "Total earned",
      `<span title="${fmtBtcTitle(user.total_earned_sats)} — TIDES share lines in past finds">${fmtBtc(user.total_earned_sats)}</span>`,
      true
    ),
    lastFindCard,
    card("Share of window", user.share_pct.toFixed(4) + "%"),
    card("Work in window", fmtInt(user.work_in_window)),
    card(
      "Est. next block payout",
      `<span title="${fmtBtcTitle(user.estimated_next_sats || 0)} — your tides window share">${fmtBtc(user.estimated_next_sats || 0)}</span>`
    ),
    card(
      "Workers",
      (user.worker_breakdown || []).length
        ? user.worker_breakdown.map((w) => w.worker).join(", ")
        : user.workers.length
          ? user.workers.join(", ")
          : "—",
      true
    ),
    card("Window size", fmtInt(stats.window_work_target) + " @ diff " + fmtInt(stats.block_difficulty)),
  ].join("");

  const wBox = document.getElementById("userWorkersBox");
  const wBody = document.getElementById("userWorkerBody");
  const wbreak = user.worker_breakdown || [];
  if (wBox && wBody) {
    if (wbreak.length >= 1) {
      wBox.hidden = false;
      const parts = [];
      for (const w of wbreak) {
        const tip =
          `Window work ${fmtInt(w.work)}` +
          (w.sats != null
            ? ` · est. next ${fmtBtc(w.sats)} (${fmtBtcTitle(w.sats)})`
            : ` · ${Number(w.share_pct || 0).toFixed(1)}% of this address`);
        const wid = `uw-${escapeHtml(w.worker)}`;
        const star = workerFinderMark(address, w.worker);
        parts.push(`<tr title="${escapeHtml(tip)}">
          <td class="mono">${star}${escapeHtml(displayWorkerName(w.worker) || w.worker)}</td>
          <td title="Shares in payout window (7 confirmed + current)">${fmtInt(w.shares)}</td>
          <td title="Recent hashrate (~10m)">${fmtHashrate(w.hashrate_hs)}</td>
          <td><button type="button" class="worker-plus" data-udetail="${wid}" title="Show work + est. next">+</button></td>
        </tr>`);
        parts.push(`<tr class="worker-sub" data-udetail-row="${wid}" hidden>
          <td class="muted" colspan="4">
            Work <strong>${fmtInt(w.work)}</strong>
            · ${Number(w.share_pct || 0).toFixed(1)}% of address
            · Est. next
            <strong title="${w.sats != null ? fmtBtcTitle(w.sats) : ""}">${
              w.sats != null ? fmtBtc(w.sats) : "—"
            }</strong>
          </td>
        </tr>`);
      }
      wBody.innerHTML = parts.join("");
      wBody.querySelectorAll(".worker-plus[data-udetail]").forEach((btn) => {
        btn.addEventListener("click", (ev) => {
          ev.preventDefault();
          const id = btn.getAttribute("data-udetail");
          const open = btn.dataset.open === "1";
          btn.dataset.open = open ? "0" : "1";
          btn.textContent = open ? "+" : "−";
          wBody.querySelectorAll(`[data-udetail-row="${id}"]`).forEach((tr) => {
            tr.hidden = open;
          });
        });
      });
    } else {
      wBox.hidden = true;
      wBody.innerHTML = "";
    }
  }

  renderPayoutHistory(payouts || []);
  await loadSharesPage(address, 0);

  try {
    await loadUserChart(address, userChartRange);
  } catch (e) {
    console.error("user chart", e);
  }
}

document.getElementById("lookup").addEventListener("submit", (e) => {
  e.preventDefault();
  const a = document.getElementById("addrInput").value.trim();
  if (!a) return;
  location.href = "/address?a=" + encodeURIComponent(a);
});

/**
 * Click-to-pin tooltips for miner status / gateway badges.
 * Native title= tips disappear when you press PrintScreen or open Snipping Tool —
 * click the dot/✓/⚠ once to pin a bubble, click again / outside / Esc to dismiss.
 */
function wireTipPins() {
  let bubble = null;
  let pinnedEl = null;

  function hide() {
    if (bubble) {
      bubble.remove();
      bubble = null;
    }
    if (pinnedEl) {
      pinnedEl.classList.remove("tip-pinned");
      pinnedEl = null;
    }
  }

  function place(el) {
    if (!bubble || !el) return;
    const r = el.getBoundingClientRect();
    const pad = 8;
    const bw = bubble.offsetWidth || 240;
    const bh = bubble.offsetHeight || 40;
    let left = r.left + r.width / 2 - bw / 2;
    let top = r.top - bh - pad;
    if (top < pad) top = r.bottom + pad;
    left = Math.max(pad, Math.min(left, window.innerWidth - bw - pad));
    bubble.style.left = `${Math.round(left)}px`;
    bubble.style.top = `${Math.round(top)}px`;
  }

  function show(el) {
    const tip = (el.getAttribute("data-tip") || "").trim();
    if (!tip) return;
    hide();
    bubble = document.createElement("div");
    bubble.className = "tip-bubble tip-bubble-pinned";
    bubble.setAttribute("role", "tooltip");
    bubble.textContent = tip;
    document.body.appendChild(bubble);
    place(el);
    pinnedEl = el;
    el.classList.add("tip-pinned");
  }

  document.addEventListener(
    "click",
    (ev) => {
      const el = ev.target && ev.target.closest && ev.target.closest(".tip-pin");
      if (el) {
        ev.preventDefault();
        ev.stopPropagation();
        if (pinnedEl === el) hide();
        else show(el);
        return;
      }
      if (pinnedEl && !(ev.target && ev.target.closest && ev.target.closest(".tip-bubble"))) {
        hide();
      }
    },
    true
  );

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && pinnedEl) hide();
    if ((ev.key === "Enter" || ev.key === " ") && ev.target && ev.target.classList && ev.target.classList.contains("tip-pin")) {
      ev.preventDefault();
      if (pinnedEl === ev.target) hide();
      else show(ev.target);
    }
  });

  window.addEventListener(
    "scroll",
    () => {
      if (pinnedEl) place(pinnedEl);
    },
    true
  );
  window.addEventListener("resize", () => {
    if (pinnedEl) place(pinnedEl);
  });
}

(async function main() {
  wireTipPins();
  const a = qs("a");
  const path = (location.pathname || "/").replace(/\/+$/, "") || "/";
  const onBlocks = path === "/blocks";

  wireChartRanges("poolChartRanges", (r) => {
    poolChartRange = r;
    loadPoolChart(r).catch((e) => console.error(e));
  });
  wireChartRanges("userChartRanges", (r) => {
    userChartRange = r;
    const addr = document.getElementById("addrInput")?.value?.trim() || qs("a");
    if (addr) loadUserChart(addr, r).catch((e) => console.error(e));
  });

  // Coinbaser paints inside loadPool (parallel with stats/contrib). No serial wait.
  try {
    if (onBlocks) await loadBlocksPage();
    else if (a) await loadUser(a);
    else await loadPool();
  } catch (err) {
    console.error(err);
    const el =
      document.getElementById(onBlocks ? "blocksAllBody" : "poolCards") ||
      document.getElementById("poolCards");
    if (el) el.textContent = "Failed to load: " + err.message;
  }

  refreshHealthStrip().catch((e) => console.error("health", e));

  // Soft 60s refresh for the active dashboard view (paused when tab hidden).
  setInterval(() => {
    if (document.visibilityState === "hidden") return;
    refreshHealthStrip().catch((e) => console.error("health", e));
    const p = (location.pathname || "/").replace(/\/+$/, "") || "/";
    if (p === "/blocks") return;
    const addr = qs("a");
    if (addr) {
      loadUser(addr).catch((e) => console.error("refresh user", e));
    } else {
      loadPool().catch((e) => console.error("refresh pool", e));
    }
  }, 60000);

  // Watch for new pool finds → trigger snapshot rebuild + reload (cards/blocks already live).
  setInterval(() => {
    pollForNewFinds().catch((e) => console.error("find watch", e));
  }, FIND_WATCH_MS);
  setTimeout(() => {
    pollForNewFinds().catch((e) => console.error("find watch", e));
  }, 3000);
})();
