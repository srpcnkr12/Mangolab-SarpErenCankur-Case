#!/usr/bin/env bash
# Runs the tests. They pass with no network at all: respx intercepts every
# upstream call, so FX_UPSTREAM_BASE can point at a closed port.
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q -r requirements.txt

exec ./.venv/bin/python -m pytest "$@"
