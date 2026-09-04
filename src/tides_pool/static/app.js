function quarantineBadge(c) {
  if (!c || !c.quarantined) return "";
  const tip = (c.quarantine_reason || "coinbase mismatch / reject-27")
    .replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  return ` <span class="badge-quarantine" title="${tip}">⚠ quarantined</span>`;
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
  if (live) {
    return `<span class="activity-dot live" title="Hashing now (shares in the last ~10 minutes)"></span>`;
  }
  if (thisBlock) {
    return `<span class="activity-dot idle" title="Work on this block, but no shares in the last ~10 minutes"></span>`;
  }
  return `<span class="activity-dot offline" title="In the payout window, but no work on this block"></span>`;
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

/** Network axis always in PH/s (e.g. 0.50 PH/s, not 500 TH/s). */
function fmtHashratePH(hs) {
  const n = Number(hs || 0);
  if (n <= 0) return "—";
  return (n / 1e15).toFixed(2) + " PH/s";
}

/** Miner chart axis always in TH/s (e.g. 0.12 TH/s, not 117 GH/s). */
function fmtHashrateTH(hs) {
  const n = Number(hs || 0);
  if (n <= 0) return "—";
  const th = n / 1e12;
  if (th >= 100) return th.toFixed(1) + " TH/s";
  if (th >= 10) return th.toFixed(2) + " TH/s";
  return th.toFixed(2) + " TH/s";
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
  const u = (info && info.mempool_explorer_url) || window.MEMPOOL_URL || "https://mempool.maveth.ca";
  return String(u).replace(/\/$/, "");
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

function card(label, value, mono) {
  return `<div class="card"><div class="label">${label}</div><div class="value${mono ? " mono" : ""}">${value}</div></div>`;
}

/** Compact two-stat card value: 24h | 1wk (same box size as other cards). */
function cardSplitValue(leftN, leftK, rightN, rightK, title) {
  const tip = title ? ` title="${String(title).replace(/"/g, "&quot;")}"` : "";
  return `<div class="card-split"${tip}>
    <div class="split-cell"><span class="split-n">${leftN}</span><span class="split-k">${leftK}</span></div>
    <div class="split-sep" aria-hidden="true"></div>
    <div class="split-cell"><span class="split-n">${rightN}</span><span class="split-k">${rightK}</span></div>
  </div>`;
}

function bpsPct(bps) {
  const n = Number(bps || 0);
  // Show one decimal only when needed (e.g. 12.5%)
  const pct = n / 100;
  return (Number.isInteger(pct) ? String(pct) : pct.toFixed(1)) + "%";
}

function renderFeeFootnote(stats) {
  // Single location for fee / finder / window copy (coinbaser panel stays short).
  const el = document.getElementById("feeFootnote");
  if (!el) return;
  const fee = Number(stats.fee_bps ?? 500);
  const finderShare = Number(stats.finder_fee_share_bps ?? 8000);
  const windowBlocks = Number(stats.window_blocks ?? 8);
  const paidInWindow = Math.max(windowBlocks - 1, 0);
  const finderOfBlock = Math.floor((fee * finderShare) / 10000);
  const opsOfBlock = fee - finderOfBlock;
  const mode = stats.window_mode || "pool_finds";
  const confFinds = Number(stats.window_confirmed_finds ?? 0);
  const windowLabel =
    mode === "pool_finds"
      ? `Payout window = <strong>${paidInWindow} confirmed finds + current</strong>` +
        ` (cutoff = ${windowBlocks}th-last confirmed; orphans excluded` +
        (confFinds ? `; have ${confFinds}` : "") +
        `)`
      : `Payout window ≈ <strong>${windowBlocks}×</strong> network difficulty`;
  el.innerHTML =
    `<strong>Fees:</strong> <strong>${bpsPct(fee)}</strong> of each block · ` +
    `<strong>${bpsPct(finderShare)}</strong> of that fee → previous finder on the <em>next</em> block ` +
    `(highlighted <span class="kind-finder-inline" title="Pickaxe + trophy">⛏️🏆</span> line) · ` +
    `ops keep <strong>${bpsPct(opsOfBlock)}</strong> of the block.<br />` +
    `${windowLabel}. <strong>Payout weight</strong> = sum of share difficulties (work), not share count. ` +
    `<strong>~H/s</strong> is estimated from recent work.`;
}

function blockStatusBadge(b) {
  const st = (b && b.status) || "confirmed";
  if (st === "pending") return `<span class="badge badge-pending" title="Waiting for chain confirmations">pending</span>`;
  if (st === "orphaned" || st === "misattributed") {
    const why = b.orphan_reason ? ` — ${b.orphan_reason}` : "";
    return `<span class="badge badge-orphan" title="Not on tip / no payout${why}">orphaned</span>`;
  }
  const mode = (b && b.payout_mode) || "onchain_split";
  if (mode === "ops_manual") {
    const done = !!(b && b.manual_payout_done);
    const note =
      (b && b.manual_payout_note) ||
      "Coinbase was ops-only; ops will pay miners manually";
    const nOut = Array.isArray(b && b.intended_payout) ? b.intended_payout.length : 0;
    const extra = nOut ? ` · snapshot ${nOut} line(s)` : "";
    if (done) {
      return `<span class="badge badge-manual-done" title="${note}${extra}">manual paid</span>`;
    }
    return `<span class="badge badge-manual" title="${note}${extra}">manual payout</span>`;
  }
  return `<span class="badge badge-ok">confirmed</span>`;
}

function finderBonusSats(rewardEst) {
  // 4% of block = 80% of the 5% fee (matches fee_bps=500, finder_fee_share_bps=8000)
  return Math.floor(Number(rewardEst || 0) * 0.04);
}

function kindCell(o, rewardEst) {
  const k = (o && o.kind) || "—";
  if (k === "tides+finder") {
    const bonus = finderBonusSats(rewardEst);
    const tip =
      bonus > 0
        ? `Work share + previous-finder bonus · finder reward ~${fmtBtc(bonus)} (${fmtBtcTitle(bonus)})`
        : "Work share + previous-finder bonus (see fee footnote)";
    return `<span class="kind-ico kind-finder" title="${escapeHtml(tip)}" aria-label="Mining + finder bonus">${KIND_ICO.pickaxe}${KIND_ICO.trophy}</span>`;
  }
  if (k === "ops") {
    return `<span class="kind-ico kind-ops" title="Pool ops fee keep (see fee footnote)" aria-label="Ops fee">${KIND_ICO.ops}</span>`;
  }
  // Regular window work share
  return `<span class="kind-ico kind-tides" title="TIDES window work share" aria-label="Mining share">${KIND_ICO.pickaxe}</span>`;
}

/** Kind column icons (emoji; title= carries the detail). */
const KIND_ICO = {
  pickaxe: "⛏️",
  trophy: "🏆",
  ops: "⚙️",
};

function renderCoinbaser(coinbaser) {
  const cbBody = document.getElementById("coinbaserBody");
  const cbNote = document.getElementById("coinbaserNote");
  if (!cbBody) return;
  if (!coinbaser) {
    cbBody.innerHTML = `<tr><td colspan="5" class="muted">Failed to load /api/coinbaser</td></tr>`;
    if (cbNote) cbNote.textContent = "Coinbaser unavailable";
    return;
  }
  const outs = coinbaser.outputs || [];
  const finderOut = outs.find((o) => o.kind === "tides+finder");
  const bonus = finderBonusSats(coinbaser.reward_sats_estimate);
  if (cbNote) {
    let note = outs.length
      ? `~${fmtBtc(coinbaser.reward_sats_estimate)} total · ${outs.length} line(s) · window work ${fmtInt(coinbaser.window_work)}`
      : `No miner lines yet (~${fmtBtc(coinbaser.reward_sats_estimate)}) — empty window pays ops only`;
    if (finderOut && bonus > 0) {
      note += ` · ⛏️🏆 line includes ~${fmtBtc(bonus)} finder bonus`;
    }
    cbNote.textContent = note;
  }
  if (!outs.length) {
    cbBody.innerHTML = `<tr><td colspan="5" class="muted">No coinbaser outputs (empty window → ops only)</td></tr>`;
    return;
  }
  cbBody.innerHTML = outs
    .map((o) => {
      let rowClass = "";
      if (o.kind === "tides+finder") rowClass = ' class="row-finder"';
      else if (o.kind === "ops") rowClass = ' class="row-ops"';
      const worker = (o.name || "").trim();
      const nick = (o.nickname || "").trim();
      return `<tr${rowClass}>
      <td>${kindCell(o, coinbaser.reward_sats_estimate)}</td>
      ${clipCell(worker, { title: worker ? `Stratum worker: ${worker}` : "" })}
      ${clipCell(nick, { title: nick ? `Nickname: ${nick}` : "", wide: true })}
      <td class="mono"><a href="/address?a=${encodeURIComponent(o.address)}" title="${o.address}">${shortAddr(o.address)}</a></td>
      <td title="${fmtBtcTitle(o.sats)}">${fmtBtc(o.sats)}</td>
    </tr>`;
    })
    .join("");
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
      const manualPending =
        !orphaned &&
        ((b && b.payout_mode) || "") === "ops_manual" &&
        !(b && b.manual_payout_done);
      if (orphaned) rowClass = ' class="row-orphan"';
      else if (pending) rowClass = ' class="row-pending"';
      else if (manualPending) rowClass = ' class="row-manual"';
      const reward = orphaned
        ? "-"
        : `<span title="${fmtBtcTitle(b.reward_sats)}">${fmtBtc(b.reward_sats)}</span>`;

      const href = mempoolBlockHref(b, info);
      const hashOk = b.block_hash && /^[0-9a-fA-F]{64}$/.test(String(b.block_hash));
      const heightCell = hashOk
        ? `<a class="mono" href="${href}" target="_blank" rel="noopener" title="${b.block_hash || ""}">${b.height}</a>`
        : `<span class="mono" title="${b.block_hash || ""}">${b.height}</span>`;
      const worker = (b.finder_worker || "").trim();
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
        <td>${blockStatusBadge(b)}</td>
        ${clipCell(worker, { title: worker ? `Stratum worker: ${worker}` : "", mono: true })}
        ${clipCell(nick, { title: nick ? `Nickname: ${nick}` : "", wide: true })}
        <td>${addrCell}</td>
        <td>${reward}</td>
        <td>${fmtInt(b.difficulty)}</td>
        <td>${agoCell}</td>
      </tr>`;
    })
    .join("");
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
  // Fixed network axis 0–4 PH/s — auto-zoom (~2.75–3.2) made the line look flat.
  const NET_AXIS_MAX_HS = 4e15;
  const findsIn = blockScatter(data.blocks, poolMax, { inWindowOnly: true });
  const findsOut = blockScatter(data.blocks, poolMax, { inWindowOnly: false });
  const win = data.window || null;
  const netSrc = data.network_source || "tip";
  const netLabel =
    netSrc === "samples"
      ? "Network"
      : "Network (tracking from now — history fills in over time)";
  const sub = document.querySelector("#poolChartBox .chart-sub");
  if (sub) {
    const spanSec = Number(data.range_sec || 0);
    const spanBit =
      data.range === "window" && spanSec > 0
        ? ` · x-axis = payout window (~${fmtChartSpan(spanSec)})`
        : "";
    const winBit = win
      ? ` · shaded = payout window (${win.label}) · bright dots = finds in window`
      : " · markers = our finds";
    sub.textContent =
      netSrc === "samples"
        ? `Pool vs network (sampled)${spanBit}${winBit}`
        : `Pool vs network — sampling started; line fills in as we collect ~1/min${spanBit}${winBit}`;
  }
  const xBound = chartXBounds(data);
  const allFinds = findsIn.concat(findsOut);
  poolChartObj = destroyChart(poolChartObj);
  poolChartObj = new Chart(canvas.getContext("2d"), {
    plugins: [payoutWindowBandPlugin, findStemPlugin],
    data: {
      datasets: [
        {
          type: "line",
          label: "Pool",
          yAxisID: "yPool",
          data: (data.pool || []).map((p) => ({ x: p.t * 1000, y: p.hs })),
          borderColor: "#3dd6c6",
          backgroundColor: "rgba(61,214,198,0.12)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.25,
          fill: true,
        },
        {
          type: "line",
          label: netLabel,
          yAxisID: "yNet",
          data: (data.network || [])
            .filter((p) => Number(p.hs) > 0)
            .map((p) => ({ x: p.t * 1000, y: p.hs })),
          borderColor: "#6ea8ff",
          borderWidth: 1.5,
          borderDash: netSrc === "samples" ? undefined : [4, 3],
          pointRadius: 0,
          tension: 0.2,
          fill: false,
          spanGaps: false,
        },
        {
          type: "scatter",
          label: "In window",
          yAxisID: "yPool",
          data: findsIn,
          backgroundColor: "#f0b429",
          borderColor: "#ffe08a",
          borderWidth: 2,
          pointRadius: 6,
          pointHoverRadius: 8,
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
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { left: 0, right: 12, top: 4, bottom: 0 } },
      interaction: { mode: "nearest", intersect: true },
      onHover(evt, els) {
        const tip = els && els.length ? els[0] : null;
        const ds = tip && poolChartObj?.data?.datasets?.[tip.datasetIndex];
        const clickable =
          ds && (ds.label === "In window" || ds.label === "Older finds");
        evt.native && (evt.native.target.style.cursor = clickable ? "pointer" : "default");
      },
      onClick(_evt, els) {
        if (!els || !els.length) return;
        const tip = els[0];
        const ds = poolChartObj?.data?.datasets?.[tip.datasetIndex];
        if (!ds || (ds.label !== "In window" && ds.label !== "Older finds")) return;
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
          labels: { color: "#c5d0e6", boxWidth: 12 },
        },
        tooltip: {
          callbacks: {
            label(ctx) {
              if (ctx.dataset.label === "In window" || ctx.dataset.label === "Older finds") {
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
          title: { display: true, text: "Pool", color: "#3dd6c6" },
          ticks: {
            color: "#3dd6c6",
            callback(v) {
              return fmtHashrate(v);
            },
          },
          grid: { color: "rgba(36,48,73,0.55)" },
          suggestedMax: poolMax,
        },
        yNet: {
          position: "right",
          title: { display: true, text: "Network (PH/s)", color: "#6ea8ff" },
          min: 0,
          max: NET_AXIS_MAX_HS,
          ticks: {
            color: "#6ea8ff",
            callback(v) {
              return fmtHashratePH(v);
            },
          },
          grid: { drawOnChartArea: false },
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
  const yMax = hsAxisMax([data.hashrate]);
  const finds = blockScatter(data.blocks, yMax);
  const xBound = chartXBounds({
    pool: data.hashrate,
    network: [],
    blocks: data.blocks,
  });
  const userSub = document.querySelector("#userChartBox .chart-sub") ||
    document.querySelector("#minerPanel .chart-sub");
  if (userSub) {
    const spanSec = Number(data.range_sec || 0);
    const base = "From your accepted shares · axis in TH/s · markers = your finds";
    userSub.textContent =
      data.range === "window" && spanSec > 0
        ? `${base} · x-axis = payout window (~${fmtChartSpan(spanSec)})`
        : base;
  }
  userChartObj = destroyChart(userChartObj);
  userChartObj = new Chart(canvas.getContext("2d"), {
    plugins: [findStemPlugin],
    data: {
      datasets: [
        {
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
        },
        {
          type: "scatter",
          label: "Your finds",
          yAxisID: "yPool",
          data: finds,
          backgroundColor: "#f0b429",
          borderColor: "#f0b429",
          pointRadius: 5,
          pointHoverRadius: 7,
        },
      ],
    },
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
          title: { display: true, text: "TH/s", color: "#8b9bb8" },
          ticks: {
            color: "#8b9bb8",
            callback(v) {
              return fmtHashrateTH(v);
            },
          },
          grid: { color: "rgba(36,48,73,0.55)" },
          suggestedMax: yMax || undefined,
        },
      },
    },
  });
}

async function loadPool() {
  // Fetch coinbaser first so the suggested split is never stuck on "Loading…"
  // if contributors/blocks are slow or fail.
  let coinbaser = null;
  try {
    coinbaser = await jget("/api/coinbaser");
  } catch (e) {
    console.error(e);
  }
  renderCoinbaser(coinbaser);

  const settled = await Promise.allSettled([
    jget("/api/stats"),
    jget("/api/contributors?limit=50"),
    jget("/api/blocks?limit=8"),
    jget("/api/info"),
  ]);
  const val = (i, fallback) =>
    settled[i].status === "fulfilled" ? settled[i].value : fallback;
  const stats = val(0, null);
  const contrib = val(1, []);
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

  const lastH = stats.last_pool_block_height;
  let lastFindVal = "none yet";
  if (lastH != null) {
    const age =
      stats.last_pool_block_age_sec != null
        ? fmtAge(stats.last_pool_block_age_sec)
        : null;
    lastFindVal = age ? `${lastH} · ${age}` : String(lastH);
  }
  const netHs = Number(stats.network_hashrate_hs || 0);
  const sharePct = Number(stats.pool_network_share_pct || 0);
  const etaSec = stats.est_block_time_sec;
  document.getElementById("poolCards").innerHTML = [
    card("Chain tip", String(stats.chain_height ?? "—") + (stats.rpc_ok ? "" : " · RPC?")),
    card(
      "Network",
      cardSplitValue(
        fmtHashrate(netHs),
        "hash",
        fmtSharePct(sharePct),
        "our share",
        "Network hashrate (node) · pool share = pool HR / network HR"
      )
    ),
    card(
      "Est. find",
      `<span title="Expected wait for a pool block at current pool hashrate (diff × 2³² / pool HR). Luck varies.">${fmtDuration(etaSec)}</span>`
    ),
    card("Pool hashrate", fmtHashrate(stats.hashrate_hs), true),
    card("Miners in window", fmtInt(stats.addresses_in_window)),
    card(
      "Reward window",
      `${fmtInt(Math.max(Number(stats.window_blocks ?? 8) - 1, 0))} + current`
    ),
    card(
      "Blocks",
      cardSplitValue(
        fmtInt(stats.blocks_last_24h),
        "24h",
        fmtInt(stats.blocks_last_7d ?? stats.blocks_last_24h),
        "1wk",
        Number(stats.orphans_last_24h || 0) || Number(stats.orphans_last_7d || 0)
          ? `Orphans excluded · 24h orphaned ${fmtInt(stats.orphans_last_24h || 0)} · 1wk orphaned ${fmtInt(stats.orphans_last_7d || 0)}`
          : "Confirmed + pending finds (orphans excluded)"
      )
    ),
    card("Last find", lastFindVal),
  ].join("");
  renderFeeFootnote(stats);

  const cbody = document.getElementById("contribBody");
  if (cbody && !contrib.length) {
    cbody.innerHTML = `<tr><td colspan="10" class="muted">No shares in window yet</td></tr>`;
  } else if (cbody) {
    // Luck % column hidden for now — per-addr finder luck in a short window is
    // misleading (tiny work + a find → huge %; big hashrate + 0 finds → —).
    cbody.innerHTML = contrib
      .map(
        (c, i) => `<tr>
        <td class="activity-cell">${activityDot(c)}</td>
        <td>${i + 1}</td>
        <td class="mono"><a href="/address?a=${encodeURIComponent(c.address)}" title="${c.address}">${shortAddr(c.address)}</a>${quarantineBadge(c)}</td>
        ${clipCell(c.nickname, { title: c.nickname ? `Nickname: ${c.nickname}` : "", wide: true })}
        <td title="Accepted shares in the full payout window">${fmtInt(c.shares)}</td>
        <td title="${lastShareTitle(c)}">${lastShareLabel(c)}</td>
        <td title="Work since last confirmed pool find (unfinished current block)">${fmtInt(c.work_current ?? 0)}</td>
        <td title="Total work in payout window only (7 confirmed + current) — not lifetime">${fmtInt(c.work)}</td>
        <td title="Rough hashrate from recent shares (~10m)">${fmtHashrate(c.hashrate_hs)}</td>
        <td title="Your total window work ÷ window work">${Number(c.share_pct || 0).toFixed(2)}%</td>
      </tr>`
      )
      .join("");
  }

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
      let status = p.status || "—";
      if (p.kind === "finder" && p.paid_in_height != null) {
        status = `paid @ ${p.paid_in_height}`;
      } else if (p.kind === "finder" && p.status === "unpaid") {
        status = "unpaid";
      }
      const when = p.accounted_at ? fmtLocalTime(p.accounted_at) : "—";
      const href = mempoolBlockHref(
        { height: p.height, block_hash: p.block_hash },
        tidesInfo
      );
      const hCell = href
        ? `<a href="${href}" target="_blank" rel="noopener" class="mono">${p.height}</a>`
        : `<span class="mono">${p.height}</span>`;
      return `<tr>
        <td>${hCell}</td>
        <td>${kind}</td>
        <td title="${fmtBtcTitle(p.sats)}">${fmtBtc(p.sats)}</td>
        <td>${status}</td>
        <td class="mono">${when}</td>
      </tr>`;
    })
    .join("");
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
  wireSharesPager();
  const [user, payouts, stats, info] = await Promise.all([
    jget("/api/user/" + encodeURIComponent(address)),
    jget("/api/user/" + encodeURIComponent(address) + "/payouts?limit=100").catch(
      (e) => {
        console.error("payouts", e);
        return [];
      }
    ),
    jget("/api/stats"),
    jget("/api/info"),
  ]);
  tidesInfo = info || tidesInfo || {};
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
      `<span title="${fmtBtcTitle(user.total_earned_sats)} — TIDES share lines in past finds + paid finder bonuses">${fmtBtc(user.total_earned_sats)}</span>`,
      true
    ),
    lastFindCard,
    card("Share of window", user.share_pct.toFixed(4) + "%"),
    card("Work in window", fmtInt(user.work_in_window)),
    card(
      "Est. next block payout",
      `<span title="${fmtBtcTitle((user.estimated_next_sats || 0) + (user.pending_finder_credit_sats || 0))} — tides share + finder bonus if yours">${fmtBtc((user.estimated_next_sats || 0) + (user.pending_finder_credit_sats || 0))}</span>`
    ),
    card("Workers", user.workers.length ? user.workers.join(", ") : "—", true),
    card("Window size", fmtInt(stats.window_work_target) + " @ diff " + fmtInt(stats.block_difficulty)),
  ].join("");

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

(async function main() {
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

  // Always try coinbaser first / standalone so the payout table cannot stick on Loading.
  if (!a && !onBlocks) {
    try {
      renderCoinbaser(await jget("/api/coinbaser"));
    } catch (e) {
      console.error("coinbaser bootstrap", e);
      renderCoinbaser(null);
    }
  }
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

  // Soft 30s refresh for the active dashboard view (paused when tab hidden).
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
  }, 30000);
})();
