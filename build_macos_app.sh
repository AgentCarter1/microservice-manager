#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="Miroservice Manager"
DEFAULT_ICON_PATH="static/assets/app_icon.icns"
APP_BUNDLE_PATH="dist/$APP_NAME.app"
DMG_STAGING_DIR="dist/dmg-root"
DMG_PATH="dist/$APP_NAME.dmg"
ROOT_DMG_PATH="$APP_NAME.dmg"

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

if command -v xattr >/dev/null 2>&1; then
  xattr -cr "$APP_BUNDLE_PATH" || true
fi

if command -v codesign >/dev/null 2>&1; then
  CODESIGN_IDENTITY="${CODESIGN_IDENTITY:--}"
  if codesign --force --deep --sign "$CODESIGN_IDENTITY" "$APP_BUNDLE_PATH"; then
    echo "Signed: $APP_BUNDLE_PATH"
  else
    echo "Warning: code signing failed; continuing with an unsigned app."
  fi
fi

if ! command -v hdiutil >/dev/null 2>&1; then
  echo "Built: $APP_BUNDLE_PATH"
  echo "hdiutil was not found, so a DMG could not be created on this machine."
  exit 0
fi

rm -rf "$DMG_STAGING_DIR"
mkdir -p "$DMG_STAGING_DIR"
cp -R "$APP_BUNDLE_PATH" "$DMG_STAGING_DIR/$APP_NAME.app"
ln -s /Applications "$DMG_STAGING_DIR/Applications"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$DMG_STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

cp "$DMG_PATH" "$ROOT_DMG_PATH"
rm -rf "$DMG_STAGING_DIR"

echo "Built: $APP_BUNDLE_PATH"
echo "Built: $DMG_PATH"
echo "Copied: $ROOT_DMG_PATH"
echo "Note: for warning-free public distribution, sign with an Apple Developer ID and notarize the DMG."
