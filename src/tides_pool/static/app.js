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

async function loadPool() {
  const [stats, contrib, blocks, info, coinbaser] = await Promise.all([
    jget("/api/stats"),
    jget("/api/contributors?limit=50"),
    jget("/api/blocks?limit=20"),
    jget("/api/info"),
    jget("/api/coinbaser"),
  ]);

  document.getElementById("poolCards").innerHTML = [
    card("Network", stats.network + (stats.rpc_ok ? " · RPC ok" : " · RPC ?")),
    card("Chain tip (RC2)", stats.chain_height ?? "—"),
    card("Difficulty", fmtInt(stats.block_difficulty)),
    card("Window target", fmtInt(stats.window_work_target) + " work"),
    card("Window filled", fmtInt(stats.window_work_filled)),
    card("Addresses in window", fmtInt(stats.addresses_in_window)),
    card("Share log work", fmtInt(stats.share_log_work)),
    card("Shares accepted", fmtInt(stats.share_count)),
    card(
      "Est. pool hashrate",
      fmtHashrate(stats.hashrate_hs) +
        (stats.hashrate_window_sec
          ? ` · ${fmtInt(stats.hashrate_shares)} shares / ${stats.hashrate_window_sec / 60}m`
          : ""),
      true
    ),
    card("Last pool block", stats.last_pool_block_height ?? "—"),
    card("Est. subsidy", fmtSats(stats.reward_estimate_sats)),
    card("Ops address", shortAddr(stats.pool_ops_address), true),
    card(
      "Addr work cap",
      stats.address_work_cap
        ? fmtInt(stats.address_work_cap) + " / " + (stats.address_work_cap_window_sec / 3600) + "h"
        : "—",
      true
    ),
    card(
      "Pending finder credit",
      stats.pending_finder_address
        ? shortAddr(stats.pending_finder_address) + " · " + fmtSats(stats.pending_finder_credit_sats)
        : "—",
      true
    ),
  ].join("");


  const cbBody = document.getElementById("coinbaserBody");
  if (cbBody) {
    const outs = (coinbaser && coinbaser.outputs) || [];
    if (!outs.length) {
      cbBody.innerHTML = `<tr><td colspan="3" class="muted">No coinbaser outputs (empty window → ops only)</td></tr>`;
    } else {
      cbBody.innerHTML = outs
        .map(
          (o) => `<tr>
          <td>${o.kind || "—"}</td>
          <td class="mono"><a href="/address?a=${encodeURIComponent(o.address)}" title="${o.address}">${o.address}</a></td>
          <td>${fmtSats(o.sats)}</td>
        </tr>`
        )
        .join("");
    }
  }

  const cbody = document.getElementById("contribBody");
  if (!contrib.length) {
    cbody.innerHTML = `<tr><td colspan="6" class="muted">No shares in window yet. Lab: POST /api/lab/share</td></tr>`;
  } else {
    cbody.innerHTML = contrib
      .map(
        (c, i) => `<tr>
        <td>${i + 1}</td>
        <td class="mono"><a href="/address?a=${encodeURIComponent(c.address)}" title="${c.address}">${c.address}</a></td>
        <td>${fmtInt(c.shares)}</td>
        <td title="Sum of Diff1 share work in TIDES window">${fmtInt(c.work)}</td>
        <td title="Rough hashrate from last 10 minutes of this address's shares">${fmtHashrate(c.hashrate_hs)}</td>
        <td>${c.share_pct.toFixed(2)}%</td>
      </tr>`
      )
      .join("");
  }

  const bbody = document.getElementById("blocksBody");
  if (!blocks.length) {
    bbody.innerHTML = `<tr><td colspan="4" class="muted">No pool blocks yet</td></tr>`;
  } else {
    bbody.innerHTML = blocks
      .map(
        (b) => `<tr>
        <td>${b.height}</td>
        <td>${
          b.finder_address
            ? `<a class="truncate" href="/address?a=${encodeURIComponent(b.finder_address)}">${shortAddr(b.finder_address)}</a>`
            : "—"
        }</td>
        <td>${fmtSats(b.reward_sats)}</td>
        <td>${fmtInt(b.difficulty)}</td>
      </tr>`
      )
      .join("");
  }

  document.getElementById("footerMeta").textContent =
    `${info.name} ${info.version} · ${stats.pool_name}`;

  const j = info.join || {};
  const pre = document.getElementById("joinPre");
  if (pre) {
    pre.textContent = JSON.stringify(
      {
        datum: {
          pool_host: j.pool_host || "tides.maveth.ca",
          pool_port: j.pool_port || 28916,
          pool_pubkey: j.pool_pubkey || "(empty = auto-fetch on MaVeTh Blake DATUM)",
          pooled_mining_only: false,
        },
      },
      null,
      2
    );
  }
}


async function loadUser(address) {
  document.getElementById("poolView").classList.add("hidden");
  document.getElementById("userView").classList.remove("hidden");
  document.getElementById("addrInput").value = address;
  document.getElementById("userTitle").textContent = address;

  const [user, shares, stats] = await Promise.all([
    jget("/api/user/" + encodeURIComponent(address)),
    jget("/api/user/" + encodeURIComponent(address) + "/shares?limit=100"),
    jget("/api/stats"),
  ]);

  document.getElementById("userCards").innerHTML = [
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
  try {
    if (a) await loadUser(a);
    else await loadPool();
  } catch (err) {
    console.error(err);
    document.getElementById("poolCards").textContent = "Failed to load: " + err.message;
  }
})();
