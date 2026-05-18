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
import venv
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
  echo "No Python 3 interpreter with venv support was found."
  exit 1
}

if [[ ! -x ".venv/bin/python" ]]; then
  "$PY" -m venv .venv
fi

if [[ ! -f ".venv/.microrunner-desktop-ready" || requirements.txt -nt ".venv/.microrunner-desktop-ready" ]]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.microrunner-desktop-ready
fi

exec .venv/bin/python main.py --desktop "$@"
