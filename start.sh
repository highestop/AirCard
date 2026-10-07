#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
make app
exec open "build/Apple Wallet Card Skinner.app" --args "$@"
