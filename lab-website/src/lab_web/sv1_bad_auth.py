"""SV1 clients with non-payout usernames (warning list for UI).

Fetches DATUM Gateway /clients HTML (digest auth), keeps rows whose Auth Username
is not a BTC payout address. Used by snapshot builder + one-shot host refresh.
No Prime/SV1 restart required.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def looks_like_payout_address(addr: str) -> bool:
    """Structural gate only (same idea as tides_pool.addresses.looks_like_payout_address)."""
    a = (addr or "").strip()
    if not a or len(a) < 26 or len(a) > 90:
        return False
    low = a.lower()
    if low.startswith(("bc1", "tb1", "bcrt1")):
        return True
    return a[0] in "13mn2" and all(
        ch in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz" for ch in a
    )


def username_payout_part(auth_user: str) -> str:
    """Stratum user is address or address.worker — payout part is before first '.'."""
    u = (auth_user or "").strip()
    if not u:
        return ""
    return u.split(".", 1)[0].strip()


def ip_tail(remhost: str) -> str:
    """Last two dotted octets, e.g. ::ffff:176.226.184.149 -> 184.149"""
    s = (remhost or "").strip()
    m = re.search(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\s*$", s)
    if not m:
        return ""
    return f"{m.group(3)}.{m.group(4)}"


def _load_auth(snap_dir: Path) -> dict[str, str]:
    env_url = (os.environ.get("LAB_SV1_CLIENTS_URL") or "").strip()
    env_user = (os.environ.get("LAB_SV1_API_USER") or "admin").strip()
    env_pass = (os.environ.get("LAB_SV1_API_PASSWORD") or "").strip()
    cfg_path = snap_dir / ".sv1_api_auth.json"
    data: dict[str, str] = {}
    if cfg_path.is_file():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): str(v) for k, v in raw.items() if v is not None}
        except Exception:
            data = {}
    url = env_url or data.get("url") or "http://host.docker.internal:7156/clients"
    user = env_user or data.get("username") or "admin"
    password = env_pass or data.get("password") or ""
    return {"url": url, "username": user, "password": password}


def _parse_www_auth(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not header:
        return out
    # Digest realm="…", nonce="…", algorithm=SHA-256, qop="auth", ...
    for m in re.finditer(r"(\w+)=(?:\"([^\"]*)\"|([^,\s]+))", header):
        out[m.group(1).lower()] = (m.group(2) if m.group(2) is not None else m.group(3) or "")
    return out


def _digest_response(
    *,
    username: str,
    password: str,
    method: str,
    uri: str,
    challenge: dict[str, str],
    nc: str = "00000001",
    cnonce: str | None = None,
) -> tuple[str, str]:
    """Return (authorization value body without 'Digest ', cnonce). Supports MD5 and SHA-256."""
    import hashlib
    import os as _os

    algo = (challenge.get("algorithm") or "MD5").upper().replace("-", "")
    if algo in ("SHA256", "SHA-256"):
        hf = hashlib.sha256
        algo_label = challenge.get("algorithm") or "SHA-256"
    else:
        hf = hashlib.md5
        algo_label = "MD5"
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop = (challenge.get("qop") or "auth").split(",")[0].strip()
    if cnonce is None:
        cnonce = _os.urandom(8).hex()
    ha1 = hf(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hf(f"{method}:{uri}".encode()).hexdigest()
    if qop:
        resp = hf(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
    else:
        resp = hf(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    parts = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'algorithm={algo_label}',
        f'response="{resp}"',
    ]
    if qop:
        parts.extend([f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"'])
    opaque = challenge.get("opaque")
    if opaque:
        parts.append(f'opaque="{opaque}"')
    return ", ".join(parts), cnonce


def fetch_clients_html(url: str, username: str, password: str, *, timeout: float = 12.0) -> str:
    """Digest auth fetch; supports SHA-256 (Gateway) which stdlib HTTPDigestAuthHandler lacks."""
    req = urllib.request.Request(url, headers={"Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        www = exc.headers.get("WWW-Authenticate") or ""
        if not www.lower().startswith("digest"):
            raise
        challenge = _parse_www_auth(www)
        path = urllib.parse.urlparse(url).path or "/"
        if urllib.parse.urlparse(url).query:
            path = path + "?" + urllib.parse.urlparse(url).query
        auth_body, _ = _digest_response(
            username=username,
            password=password,
            method="GET",
            uri=path,
            challenge=challenge,
        )
        req2 = urllib.request.Request(
            url,
            headers={"Accept": "text/html", "Authorization": f"Digest {auth_body}"},
        )
        with urllib.request.urlopen(req2, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


def parse_clients_table(html: str) -> list[dict[str, str]]:
    """Parse Gateway /clients HTML table into dict rows."""
    out: list[dict[str, str]] = []
    # Header row establishes column names
    headers: list[str] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.I | re.S):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.I | re.S)
        plain = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        plain = [re.sub(r"\s+", " ", p) for p in plain]
        if not plain:
            continue
        if not headers:
            # first row with RemHost / Auth looks like header
            joined = " ".join(plain).lower()
            if "remhost" in joined or "auth username" in joined or "username" in joined:
                headers = [p.lower() for p in plain]
                continue
            continue
        if len(plain) < 3:
            continue
        row = {headers[i]: plain[i] if i < len(plain) else "" for i in range(len(headers))}
        out.append(row)
    return out


def _col(row: dict[str, str], *names: str) -> str:
    for n in names:
        for k, v in row.items():
            if n in k:
                return v
    return ""


def bad_auth_clients(html: str) -> list[dict[str, Any]]:
    rows = parse_clients_table(html)
    bad: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        auth = _col(row, "auth username", "username", "user")
        host = _col(row, "remhost", "host", "ip")
        hr = _col(row, "hashrate")
        ua = _col(row, "useragent", "user agent", "ua")
        payout = username_payout_part(auth)
        if not auth or looks_like_payout_address(payout):
            continue
        key = (auth, ip_tail(host) or host)
        if key in seen:
            continue
        seen.add(key)
        bad.append(
            {
                "auth_username": auth,
                "payout_part": payout,
                "ip_tail": ip_tail(host),
                "remhost": host,
                "hashrate": hr,
                "user_agent": ua,
                "shares": 0,
                "note": "not a payout address — Prime rejects shares (bad payout address); no window credit",
            }
        )
    return bad


def build_payload(snap_dir: Path | None = None) -> dict[str, Any]:
    snap = Path(snap_dir or os.environ.get("LAB_SNAP_DIR", "/app/snapshots"))
    auth = _load_auth(snap)
    if not auth.get("password"):
        return {
            "as_of": _utc(),
            "ok": False,
            "error": "missing SV1 API password (.sv1_api_auth.json or LAB_SV1_API_PASSWORD)",
            "clients": [],
            "payout": "nowhere",
            "payout_detail": "Invalid stratum usernames are rejected at Prime before credit — not paid to the miner and not skimmed to ops.",
        }
    try:
        html = fetch_clients_html(auth["url"], auth["username"], auth["password"])
        clients = bad_auth_clients(html)
        return {
            "as_of": _utc(),
            "ok": True,
            "source": auth["url"],
            "clients": clients,
            "count": len(clients),
            "payout": "nowhere",
            "payout_detail": "Invalid stratum usernames are rejected at Prime before credit — not paid to the miner and not skimmed to ops. SV1 may still hand out templates.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "as_of": _utc(),
            "ok": False,
            "error": str(exc),
            "clients": [],
            "payout": "nowhere",
            "payout_detail": "Invalid stratum usernames are rejected at Prime before credit — not paid to the miner and not skimmed to ops.",
        }


def write_bad_auth_snaps(snap_dir: Path, static_data: Path | None = None) -> dict[str, Any]:
    payload = build_payload(snap_dir)
    snap_dir.mkdir(parents=True, exist_ok=True)
    path = snap_dir / "sv1_bad_auth.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if static_data is not None:
        static_data.mkdir(parents=True, exist_ok=True)
        (static_data / "sv1_bad_auth.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    return payload


if __name__ == "__main__":
    snap = Path(os.environ.get("LAB_SNAP_DIR", "/mnt/Alexandria/bitcoin/website/snapshots"))
    static = Path(os.environ.get("LAB_STATIC_DIR", "/mnt/Alexandria/bitcoin/website/static")) / "data"
    p = write_bad_auth_snaps(snap, static)
    print(json.dumps({"ok": p.get("ok"), "count": p.get("count"), "error": p.get("error")}, indent=2))
