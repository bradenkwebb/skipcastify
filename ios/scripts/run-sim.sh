#!/bin/bash
# Install the last build on a booted simulator and launch it against a server.
#   SERVER_URL=http://127.0.0.1:5055 SERVER_USER=dev SERVER_PASSWORD=… scripts/run-sim.sh
# Values are launch arguments (UserDefaults' argument domain), so nothing is saved.
# The password argument is honored only in Debug builds.
set -euo pipefail
cd "$(dirname "$0")/.."
SIM="${SIM:-$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)}"
APP=build/DerivedData/Build/Products/Debug-iphonesimulator/Skipcastify.app
BUNDLE=com.example.skipcastify
args=()
[ -n "${SERVER_URL:-}" ] && args+=(-server.url "$SERVER_URL")
if [ -n "${SERVER_USER:-}" ]; then
  args+=(-server.authMode basic -server.username "$SERVER_USER" -server.password "${SERVER_PASSWORD:-}")
fi
[ -n "${TAB:-}" ] && args+=(-root.tab "$TAB")
[ -n "${OPEN:-}" ] && args+=(-open "$OPEN")
xcrun simctl terminate "$SIM" "$BUNDLE" >/dev/null 2>&1 || true
xcrun simctl install "$SIM" "$APP"
xcrun simctl launch "$SIM" "$BUNDLE" "${args[@]}"
