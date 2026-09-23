#!/usr/bin/env bash
set -Eeuo pipefail
# Run from a checkout/archive of this repository. Does not run install.sh or replace the DB.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$EUID" != 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
for cmd in python3 nginx openssl ss; do
  if ! command -v "$cmd" >/dev/null; then
    echo "Missing $cmd. Install python3 nginx openssl iproute2 first; see docs/site-gateway.md." >&2
    exit 1
  fi
done
exec python3 "$SCRIPT_DIR/site_gateway.py" "$@"
