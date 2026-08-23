#!/bin/bash
set -euo pipefail
BASE="${1:-http://127.0.0.1:8088}"

curl -sS -X POST "${BASE}/api/lab/block?finder=mBootstrap&height=100&difficulty=100&reward_sats=1000000"
echo
curl -sS -X POST "${BASE}/api/lab/share?address=mqMY6RozgMuiV4SsoQShd2Rn57JT7wTe72&work=250&worker=s11"
echo
curl -sS -X POST "${BASE}/api/lab/share?address=mqMY6RozgMuiV4SsoQShd2Rn57JT7wTe72&work=100&worker=gpu1"
echo
curl -sS -X POST "${BASE}/api/lab/share?address=mOtherMinerTn4Addr000000000000001&work=150&worker=box"
echo
curl -sS -X POST "${BASE}/api/lab/block?finder=mqMY6RozgMuiV4SsoQShd2Rn57JT7wTe72&height=101&difficulty=100&reward_sats=1000000"
echo
echo "--- contributors ---"
curl -sS "${BASE}/api/contributors"
echo
echo "--- shares ---"
curl -sS "${BASE}/api/user/mqMY6RozgMuiV4SsoQShd2Rn57JT7wTe72/shares?limit=5"
echo
curl -sS -o /dev/null -w "ui=%{http_code}\n" "${BASE}/"
curl -sS -o /dev/null -w "addr=%{http_code}\n" "${BASE}/address?a=mqMY6RozgMuiV4SsoQShd2Rn57JT7wTe72"
