#!/usr/bin/env bash
# Starts the service on $PORT (default 8080). The upstream base URL comes from
# $FX_UPSTREAM_BASE (default https://api.frankfurter.dev) — nothing here hardcodes
# the real host.
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q -r requirements.txt

exec ./.venv/bin/uvicorn app:app --host 0.0.0.0 --port "${PORT:-8080}"
