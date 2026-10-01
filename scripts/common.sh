#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" || "${!name}" == \<*\> ]]; then
    echo "Set $name before running this script." >&2
    exit 1
  fi
}

select_subscription() {
  require_env AZURE_SUBSCRIPTION
  az account set --subscription "$AZURE_SUBSCRIPTION"
  local state
  state="$(az account show --query state -o tsv)"
  if [[ "$state" != "Enabled" ]]; then
    echo "Azure subscription is not enabled." >&2
    exit 1
  fi
}
