#!/bin/bash
# Run the UI tests against a server (normally the local dev server).
#   SKIPCASTIFY_URL=http://127.0.0.1:5055 SKIPCASTIFY_USER=dev SKIPCASTIFY_PASSWORD=… scripts/uitest.sh [-only-testing:…]
set -uo pipefail
cd "$(dirname "$0")/.."
SIM="${SIM:-$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)}"
LOG=build/last-uitest.log
mkdir -p build
TEST_RUNNER_SKIPCASTIFY_URL="${SKIPCASTIFY_URL:-}" \
TEST_RUNNER_SKIPCASTIFY_USER="${SKIPCASTIFY_USER:-}" \
TEST_RUNNER_SKIPCASTIFY_PASSWORD="${SKIPCASTIFY_PASSWORD:-}" \
xcodebuild test -project Skipcastify.xcodeproj -scheme Skipcastify \
  -destination "platform=iOS Simulator,id=$SIM" -derivedDataPath build/DerivedData "$@" >"$LOG" 2>&1
status=$?
grep -E "error: |Test Case .*(passed|failed)|XCTAssert|\*\* TEST (SUCCEEDED|FAILED) \*\*|Executed [0-9]+ test" "$LOG" \
  | sed -E "s#$PWD/##" | grep -v "^\s*$" | tail -40
exit $status
