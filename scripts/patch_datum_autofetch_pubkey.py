#!/usr/bin/env python3
"""If pool_pubkey empty, auto-fetch from https://{pool_host}/api/pool_pubkey (TIDES lab)."""
from pathlib import Path
import sys

p = Path(sys.argv[1] if len(sys.argv) > 1 else "/mnt/Alexandria/local/bip110-lab/datum-pow/src/datum_protocol.c")
t = p.read_text()
if "datum_protocol_autofetch_pool_pubkey" in t:
    print("already patched")
    sys.exit(0)

if "#include <curl/curl.h>" not in t:
    t = t.replace("#include <sodium.h>", "#include <sodium.h>\n#include <curl/curl.h>\n#include <stdlib.h>", 1)

helper = r'''
// TIDES lab: when pool_pubkey is empty, fetch from HTTPS stats host (same DNS as pool_host).
struct datum_pubkey_fetch_buf {
	char *data;
	size_t len;
};

static size_t datum_pubkey_write_cb(void *contents, size_t size, size_t nmemb, void *userp) {
	size_t realsize = size * nmemb;
	struct datum_pubkey_fetch_buf *mem = (struct datum_pubkey_fetch_buf *)userp;
	char *ptr = (char *)realloc(mem->data, mem->len + realsize + 1);
	if (!ptr) return 0;
	mem->data = ptr;
	memcpy(&(mem->data[mem->len]), contents, realsize);
	mem->len += realsize;
	mem->data[mem->len] = 0;
	return realsize;
}

static int datum_protocol_autofetch_pool_pubkey(void) {
	CURL *curl;
	CURLcode res;
	char url[768];
	struct datum_pubkey_fetch_buf chunk;
	const char *host = datum_config.datum_pool_host;
	int i, n = 0;
	char cleaned[160];

	memset(&chunk, 0, sizeof(chunk));
	if (!host || !host[0]) {
		DLOG_WARN("Cannot auto-fetch pool pubkey: pool_host is empty");
		return -1;
	}
	snprintf(url, sizeof(url), "https://%s/api/pool_pubkey", host);
	DLOG_INFO("Auto-fetching pool pubkey from %s ...", url);

	curl = curl_easy_init();
	if (!curl) return -1;
	curl_easy_setopt(curl, CURLOPT_URL, url);
	curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, datum_pubkey_write_cb);
	curl_easy_setopt(curl, CURLOPT_WRITEDATA, (void *)&chunk);
	curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
	curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 8L);
	curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
	curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
	res = curl_easy_perform(curl);
	if (res != CURLE_OK) {
		DLOG_WARN("HTTPS pubkey fetch failed (%s); trying http://%s:8088/api/pool_pubkey", curl_easy_strerror(res), host);
		snprintf(url, sizeof(url), "http://%s:8088/api/pool_pubkey", host);
		curl_easy_setopt(curl, CURLOPT_URL, url);
		free(chunk.data);
		chunk.data = NULL;
		chunk.len = 0;
		res = curl_easy_perform(curl);
	}
	curl_easy_cleanup(curl);
	if (res != CURLE_OK || !chunk.data) {
		DLOG_WARN("Pool pubkey auto-fetch failed: %s", curl_easy_strerror(res));
		free(chunk.data);
		return -1;
	}

	for (i = 0; chunk.data[i]; i++) {
		char c = chunk.data[i];
		if ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			if (n < 128) cleaned[n++] = (char)((c >= 'A' && c <= 'F') ? (c + 32) : c);
		}
	}
	cleaned[n] = 0;
	free(chunk.data);
	if (n != 128) {
		DLOG_WARN("Auto-fetch pubkey length %d (want 128 hex)", n);
		return -1;
	}
	strncpy(datum_config.datum_pool_pubkey, cleaned, sizeof(datum_config.datum_pool_pubkey) - 1);
	datum_config.datum_pool_pubkey[sizeof(datum_config.datum_pool_pubkey) - 1] = 0;
	DLOG_INFO("Auto-fetched pool pubkey OK");
	return 0;
}

'''

marker = "int datum_protocol_init(void)"
idx = t.rfind(marker)
if idx < 0:
    raise SystemExit("datum_protocol_init not found")
t = t[:idx] + helper + t[idx:]

old = """\tif (datum_pubkey_to_struct(datum_config.datum_pool_pubkey, &pool_keys) != 0) {
\t\tDLOG_WARN("Pool pubkey not specified or invalid.");
\t\treturn -1;
\t}"""

new = """\tif (datum_pubkey_to_struct(datum_config.datum_pool_pubkey, &pool_keys) != 0) {
\t\tDLOG_WARN("Pool pubkey not specified or invalid — attempting auto-fetch...");
\t\tif (datum_protocol_autofetch_pool_pubkey() != 0 ||
\t\t    datum_pubkey_to_struct(datum_config.datum_pool_pubkey, &pool_keys) != 0) {
\t\t\tDLOG_WARN("Pool pubkey missing. Set datum.pool_pubkey or ensure https://<pool_host>/api/pool_pubkey works.");
\t\t\treturn -1;
\t\t}
\t}"""

if old not in t:
    raise SystemExit("pubkey check block not found")
t = t.replace(old, new, 1)
p.write_text(t)
print(f"patched {p}")
