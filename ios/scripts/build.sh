#!/bin/bash
# Build the app for an iOS simulator; print only errors, warnings and the result.
#   scripts/build.sh            # first booted simulator
#   SIM=<udid> scripts/build.sh
set -uo pipefail
cd "$(dirname "$0")/.."
SIM="${SIM:-$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)}"
[ -n "$SIM" ] || { echo "no booted simulator"; exit 1; }
LOG="build/last-build.log"
mkdir -p build
xcodebuild -project Skipcastify.xcodeproj -scheme Skipcastify \
  -destination "platform=iOS Simulator,id=$SIM" -derivedDataPath build/DerivedData build >"$LOG" 2>&1
status=$?
grep -E "(error|warning): " "$LOG" | grep -v "^ld: warning" | sed -E "s#$PWD/##" | sort -u
grep -E "\*\* BUILD (SUCCEEDED|FAILED) \*\*" "$LOG"
echo "app: $(pwd)/build/DerivedData/Build/Products/Debug-iphonesimulator/Skipcastify.app"
exit $status
