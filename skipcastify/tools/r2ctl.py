"""Hand controls for R2 publishing.

    uv run python -m skipcastify.tools.r2ctl status
    uv run python -m skipcastify.tools.r2ctl clear-breaker
    uv run python -m skipcastify.tools.r2ctl reconcile
    uv run python -m skipcastify.tools.r2ctl test-alert

The circuit breaker is deliberately not self-resetting, so something has to
clear it; `status` is the view you want first, and `reconcile` exists because
go-live day wants a reconciliation now rather than in 24 hours.

**Nothing here prints an object key or a URL.** Keys embed
PUBLIC_PATH_SECRET, so a published feed URL is effectively a password, and
this is a command whose output people paste into chat windows. Counts and
totals only.
"""

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

from skipcastify.services.publish_manifest import PublishManifest
from skipcastify.services.r2_breaker import R2Breaker
from skipcastify.utils.notify import notify

GB = 1024 ** 3


def _data_dir() -> str:
    load_dotenv()
    data_dir = os.getenv("DATA_DIR")
    if not data_dir:
        raise SystemExit("DATA_DIR is not set (is .env present?)")
    return data_dir


def _ago(then: float, now: float | None = None) -> str:
    if not then:
        return "never"
    hours = ((now or time.time()) - then) / 3600
    return f"{hours:.1f}h ago"


def cmd_status(_args) -> int:
    data_dir = _data_dir()
    manifest = PublishManifest(data_dir)
    breaker = R2Breaker.from_env(data_dir)
    state = breaker.state()

    enabled = os.environ.get("PUBLISH_TO_R2", "false").lower() == "true"
    print(f"PUBLISH_TO_R2:      {'true' if enabled else 'false'}")
    print(f"NTFY_TOPIC:         {'set' if os.environ.get('NTFY_TOPIC') else 'UNSET (alerts are silent)'}")
    if state is None:
        print("breaker:            closed (publishing allowed)")
    else:
        print(f"breaker:            TRIPPED — {state.get('reason', 'unknown')}")
        print(f"  tripped at:       {state.get('tripped_at_utc', 'unknown')}")
        print("  clear it with:    r2ctl clear-breaker")

    live = manifest.live_keys()
    print(f"objects tracked:    {len(live)} live, {len(manifest.all_keys())} incl. tombstones")
    print(f"audio stored:       {manifest.total_bytes('audio') / GB:.2f} GB")
    print(f"total stored:       {manifest.total_bytes() / GB:.2f} GB")
    print(f"class A today:      {manifest.class_a_today()} (limit {breaker.max_per_day})")
    print(f"class A this month: {manifest.class_a_this_month()}")
    print(f"last reconcile:     {_ago(manifest.last_reconcile_at())}")
    return 0


def cmd_clear_breaker(_args) -> int:
    breaker = R2Breaker.from_env(_data_dir())
    state = breaker.state()
    if breaker.clear():
        print(f"Breaker cleared (was: {state.get('reason', 'unknown')}).")
        print("Publishing resumes on the next pipeline run.")
        return 0
    print("Breaker was not tripped; nothing to do.")
    return 0


def cmd_reconcile(args) -> int:
    """Run the LIST-based reconciliation now. Costs a few Class A ops."""
    from skipcastify.services.publisher import R2Publisher

    try:
        publisher = R2Publisher.from_env(_data_dir())
    except ValueError as e:
        print(f"R2 is not configured: {e}", file=sys.stderr)
        return 1
    summary = publisher.reconcile(force=args.force)
    if summary is None:
        print("Reconcile skipped (breaker tripped, or the listing failed — "
              "see the log).")
        return 1
    for key, value in summary.items():
        label = key.replace("_", " ")
        if key == "bytes":
            print(f"{label + ':':<20}{value / GB:.2f} GB")
        else:
            print(f"{label + ':':<20}{value}")
    return 0


def cmd_test_alert(_args) -> int:
    """Confirm ntfy delivery, for go-live day."""
    _data_dir()
    if not os.environ.get("NTFY_TOPIC", "").strip():
        print("NTFY_TOPIC is not set, so every alert is a silent no-op.",
              file=sys.stderr)
        return 1
    ok = notify("Skipcastify test alert",
                f"Sent by r2ctl at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}. "
                f"If you can read this, pipeline and R2 alerts will reach you.")
    print("Delivered." if ok else "Not delivered — see the log.")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        prog="r2ctl", description="Hand controls for R2 publishing.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="publishing state, usage and breaker").set_defaults(
        func=cmd_status)
    sub.add_parser("clear-breaker", help="re-enable publishing after a trip").set_defaults(
        func=cmd_clear_breaker)
    sub.add_parser("test-alert", help="send one ntfy push to prove delivery").set_defaults(
        func=cmd_test_alert)
    reconcile = sub.add_parser(
        "reconcile", help="reconcile the manifest against the bucket now")
    reconcile.add_argument(
        "--no-force", dest="force", action="store_false",
        help="respect the once-per-R2_RECONCILE_HOURS gate")
    reconcile.set_defaults(func=cmd_reconcile, force=True)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
