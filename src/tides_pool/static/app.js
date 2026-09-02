function quarantineBadge(c) {
  if (!c || !c.quarantined) return "";
  const tip = (c.quarantine_reason || "coinbase mismatch / reject-27")
    .replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  return ` <span class="badge-quarantine" title="${tip}">⚠ quarantined</span>`;
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString();
}

function fmtSats(sats) {
  const n = Number(sats || 0);
  if (n >= 1e8) return (n / 1e8).toFixed(4) + " BTC";
  return fmtInt(n) + " sats";
}

function fmtHashrate(hs) {
  const n = Number(hs || 0);
  if (n <= 0) return "—";
  if (n >= 1e12) return (n / 1e12).toFixed(2) + " TH/s";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GH/s";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + " MH/s";
  if (n >= 1e3) return (n / 1e3).toFixed(2) + " kH/s";
  return n.toFixed(0) + " H/s";
}

function shortAddr(a) {
  if (!a) return "—";
  if (a.length <= 16) return a;
  return a.slice(0, 8) + "…" + a.slice(-6);
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

function bpsPct(bps) {
  const n = Number(bps || 0);
  // Show one decimal only when needed (e.g. 12.5%)
  const pct = n / 100;
  return (Number.isInteger(pct) ? String(pct) : pct.toFixed(1)) + "%";
}

function renderFeeFootnote(stats) {
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
      ? `window = <strong>${paidInWindow} confirmed + current</strong>` +
        ` (cutoff = ${windowBlocks}th-last find` +
        (confFinds ? `; have ${confFinds} confirmed` : "") +
        `) · orphans do not count`
      : `window ≈ <strong>${windowBlocks}×</strong> network difficulty`;
  el.innerHTML =
    `Fee <strong>${bpsPct(fee)}</strong> of each block · <strong>${bpsPct(finderShare)}</strong> of that fee goes to the previous finder on the <em>next</em> block · ` +
    `ops keep <strong>${bpsPct(opsOfBlock)}</strong> of the block · ${windowLabel}.` +
    `<br />` +
    `<strong>Payout weight</strong> = sum of each share’s difficulty (work), not share count. A diff‑4096 share counts twice a diff‑2048 share. ` +
    `<strong>~H/s</strong> is a rough estimate from recent work.`;
}

function blockStatusBadge(b) {
  const st = (b && b.status) || "confirmed";
  if (st === "pending") return `<span class="badge badge-pending" title="Waiting for chain confirmations">pending</span>`;
  if (st === "orphaned" || st === "misattributed") {
    const why = b.orphan_reason ? ` — ${b.orphan_reason}` : "";
    return `<span class="badge badge-orphan" title="Not on tip / no payout${why}">orphaned</span>`;
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
    const bonusTxt = bonus > 0 ? ` · bonus ${fmtSats(bonus)}` : " · finder bonus";
    return `<span class="kind-finder" title="This line is TIDES work share + previous-finder bonus (~4% of the block; 80% of the 5% fee)">tides+finder${bonusTxt}</span>`;
  }
  if (k === "ops") {
    return `<span class="kind-ops" title="Pool ops fee keep (~1% when a finder bonus is active)">ops</span>`;
  }
  return k;
}

function renderCoinbaser(coinbaser) {
  const cbBody = document.getElementById("coinbaserBody");
  const cbNote = document.getElementById("coinbaserNote");
  if (!cbBody) return;
  if (!coinbaser) {
    cbBody.innerHTML = `<tr><td colspan="4" class="muted">Failed to load /api/coinbaser</td></tr>`;
    if (cbNote) cbNote.textContent = "Coinbaser unavailable";
    return;
  }
  const outs = coinbaser.outputs || [];
  const finderOut = outs.find((o) => o.kind === "tides+finder");
  const bonus = finderBonusSats(coinbaser.reward_sats_estimate);
  if (cbNote) {
    let note = outs.length
      ? `~${fmtSats(coinbaser.reward_sats_estimate)} total · ${outs.length} payout line(s) · window work ${fmtInt(coinbaser.window_work)}`
      : `No miner lines yet (~${fmtSats(coinbaser.reward_sats_estimate)}) — empty window pays ops only`;
    if (finderOut && bonus > 0) {
      note += ` · tides+finder includes ~${fmtSats(bonus)} finder bonus`;
    }
    cbNote.textContent = note;
  }
  if (!outs.length) {
    cbBody.innerHTML = `<tr><td colspan="4" class="muted">No coinbaser outputs (empty window → ops only)</td></tr>`;
    return;
  }
  cbBody.innerHTML = outs
    .map((o) => {
      let rowClass = "";
      if (o.kind === "tides+finder") rowClass = ' class="row-finder"';
      else if (o.kind === "ops") rowClass = ' class="row-ops"';
      const worker = (o.name || "").trim() || "—";
      return `<tr${rowClass}>
      <td>${kindCell(o, coinbaser.reward_sats_estimate)}</td>
      <td title="Stratum worker for this payout address">${worker}</td>
      <td class="mono"><a href="/address?a=${encodeURIComponent(o.address)}" title="${o.address}">${shortAddr(o.address)}</a></td>
      <td>${fmtSats(o.sats)}</td>
    </tr>`;
    })
    .join("");
}

function renderBlocksTable(blocks, bodyId, info) {
  const bbody = document.getElementById(bodyId);
  if (!bbody) return;
  if (!blocks.length) {
    bbody.innerHTML = `<tr><td colspan="5" class="muted">No pool blocks yet</td></tr>`;
    return;
  }
  bbody.innerHTML = blocks
    .map((b) => {
      const st = (b && b.status) || "confirmed";
      const orphaned = st === "orphaned" || st === "misattributed";
      const pending = st === "pending";
      let rowClass = "";
      if (orphaned) rowClass = ' class="row-orphan"';
      else if (pending) rowClass = ' class="row-pending"';
      const reward = orphaned ? "-" : fmtSats(b.reward_sats);
      const href = mempoolBlockHref(b, info);
      const hashOk = b.block_hash && /^[0-9a-fA-F]{64}$/.test(String(b.block_hash));
      const heightCell = hashOk
        ? `<a class="mono" href="${href}" target="_blank" rel="noopener" title="${b.block_hash || ""}">${b.height}</a>`
        : `<span class="mono" title="${b.block_hash || ""}">${b.height}</span>`;
      return `<tr${rowClass}>
        <td>${heightCell}</td>
        <td>${blockStatusBadge(b)}</td>
        <td>${
          b.finder_address
            ? `<a class="truncate" href="/address?a=${encodeURIComponent(b.finder_address)}">${shortAddr(b.finder_address)}</a>`
            : "—"
        }</td>
        <td>${reward}</td>
        <td>${fmtInt(b.difficulty)}</td>
      </tr>`;
    })
    .join("");
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
  if (!stats) {
    throw settled[0].reason || new Error("stats failed");
  }
  for (let i = 1; i < settled.length; i++) {
    if (settled[i].status === "rejected") {
      console.error("pool partial load failed", settled[i].reason);
    }
  }

  document.getElementById("poolCards").innerHTML = [
    card("Chain tip", String(stats.chain_height ?? "—") + (stats.rpc_ok ? "" : " · RPC?")),
    card("Pool hashrate", fmtHashrate(stats.hashrate_hs), true),
    card("Miners in window", fmtInt(stats.addresses_in_window)),
    card(
      "Reward window",
      `${fmtInt(Math.max(Number(stats.window_blocks ?? 8) - 1, 0))} + current`
    ),
    card(
      "Blocks / last 24h",
      Number(stats.orphans_last_24h || 0) > 0
        ? `${fmtInt(stats.blocks_last_24h)} · ${fmtInt(stats.orphans_last_24h)} orphaned`
        : fmtInt(stats.blocks_last_24h)
    ),
    card("Last pool block", stats.last_pool_block_height ?? "none yet"),
  ].join("");
  renderFeeFootnote(stats);

  const cbody = document.getElementById("contribBody");
  if (cbody && !contrib.length) {
    cbody.innerHTML = `<tr><td colspan="6" class="muted">No shares in window yet</td></tr>`;
  } else if (cbody) {
    cbody.innerHTML = contrib
      .map(
        (c, i) => `<tr>
        <td>${i + 1}</td>
        <td class="mono"><a href="/address?a=${encodeURIComponent(c.address)}" title="${c.address}">${c.address}</a>${quarantineBadge(c)}</td>
        <td title="How many shares were accepted">${fmtInt(c.shares)}</td>
        <td title="Payout weight = sum of share difficulties">${fmtInt(c.work)}</td>
        <td title="Rough hashrate from recent shares">${fmtHashrate(c.hashrate_hs)}</td>
        <td title="Your work ÷ window work">${Number(c.share_pct || 0).toFixed(2)}%</td>
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
}

async function loadBlocksPage() {
  document.getElementById("poolView").classList.add("hidden");
  document.getElementById("userView").classList.add("hidden");
  document.getElementById("blocksView").classList.remove("hidden");
  document.title = "TIDES · Pool blocks";

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

async function loadUser(address) {
  document.getElementById("poolView").classList.add("hidden");
  document.getElementById("blocksView").classList.add("hidden");
  document.getElementById("userView").classList.remove("hidden");
  document.getElementById("addrInput").value = address;
  const [user, shares, stats] = await Promise.all([
    jget("/api/user/" + encodeURIComponent(address)),
    jget("/api/user/" + encodeURIComponent(address) + "/shares?limit=100"),
    jget("/api/stats"),
  ]);
  document.getElementById("userTitle").innerHTML =
    address + (user.quarantined ? quarantineBadge(user) : "");

  const qCard = user.quarantined
    ? card("Quarantine", user.quarantine_reason || "new shares frozen (coinbase mismatch)", true)
    : card("Reject-27 (last 20)", `${user.reject27_recent || 0} / ${user.attempt_recent || 0}`);

  document.getElementById("userCards").innerHTML = [
    qCard,
    card("Share of window", user.share_pct.toFixed(4) + "%"),
    card("Work in window", fmtInt(user.work_in_window)),
    card("Est. next block payout", fmtSats(user.estimated_next_sats)),
    card("Pending finder credit", fmtSats(user.pending_finder_credit_sats)),
    card("Workers", user.workers.length ? user.workers.join(", ") : "—", true),
    card("Window size", fmtInt(stats.window_work_target) + " @ diff " + fmtInt(stats.block_difficulty)),
  ].join("");

  const sbody = document.getElementById("sharesBody");
  if (!shares.length) {
    sbody.innerHTML = `<tr><td colspan="4" class="muted">No shares for this address yet</td></tr>`;
  } else {
    sbody.innerHTML = shares
      .map(
        (s) => `<tr>
        <td class="mono">${s.seq}</td>
        <td class="mono">${s.worker || "—"}</td>
        <td>${fmtInt(s.work)}</td>
        <td class="mono">${new Date(s.accepted_at).toISOString().replace("T", " ").replace("Z", "")}</td>
      </tr>`
      )
      .join("");
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
})();
