#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="Miroservice Manager"
DEFAULT_ICON_PATH="static/assets/app_icon.icns"

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

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-build.txt

rm -rf build dist

PYI_ARGS=(
  --noconfirm
  --windowed
  --name
  "$APP_NAME"
  --add-data
  "static:static"
  --collect-all
  webview
)

if [[ -f "${APP_ICON:-$DEFAULT_ICON_PATH}" ]]; then
  ICON_PATH="${APP_ICON:-$DEFAULT_ICON_PATH}"
  PYI_ARGS+=(--icon "$ICON_PATH")
else
  echo "Icon file not found: ${APP_ICON:-$DEFAULT_ICON_PATH}"
  echo "Defaulting to PyInstaller's default app icon."
fi

PYI_ARGS+=(main.py)

.venv/bin/pyinstaller "${PYI_ARGS[@]}"

echo "Built: dist/$APP_NAME.app"
