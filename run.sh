#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

choose_python() {
  local candidates=()

  if [[ -n "${PYTHON_BIN:-}" ]]; then
    candidates+=("$PYTHON_BIN")
  fi

  candidates+=("python3" "/usr/bin/python3")

  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" - <<'PY' >/dev/null 2>&1
import http.server
import subprocess
PY
      then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done

  return 1
}

PY="$(choose_python)" || {
  echo "No usable Python 3 interpreter was found."
  exit 1
}

exec "$PY" main.py "$@"
