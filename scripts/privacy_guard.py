"""Fail if a private value appears in the lines or commit messages a change adds.

Patterns come from $PRIVACY_PATTERNS (a GitHub secret), one per line, matched
as case-insensitive substrings; blank lines and #comments are ignored. Output
names file:line or the commit only, never the matched text: CI logs are public.

    python scripts/privacy_guard.py BASE...HEAD   # or BASE..HEAD
"""

import os
import re
import subprocess
import sys

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def patterns(raw: str) -> list[str]:
    return [p.strip().lower() for p in raw.splitlines() if p.strip() and not p.strip().startswith("#")]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def added_lines(rng: str):
    """(path, line number, text) for every line the range adds."""
    path, line = None, 0
    for row in git("diff", "--unified=0", "--no-color", "--no-ext-diff", rng).splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else None
        elif m := HUNK.match(row):
            line = int(m.group(1))
        elif row.startswith("+") and path:
            yield path, line, row[1:]
            line += 1


def resolve(rng: str) -> str:
    """Fall back to the head commit alone if the base is gone (force push, new branch)."""
    base = re.split(r"\.{2,3}", rng)[0]
    head = rng.split("..")[-1].lstrip(".")
    ok = subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], capture_output=True).returncode == 0
    return rng if ok and set(base) != {"0"} else f"{head}~1..{head}"


def main(rng: str) -> int:
    pats = patterns(os.environ.get("PRIVACY_PATTERNS", ""))
    if not pats:
        print("::warning::PRIVACY_PATTERNS is empty or unavailable (fork PR?); nothing checked")
        return 0
    rng = resolve(rng)
    hits = [f"{path}:{n}" for path, n, text in added_lines(rng) if any(p in text.lower() for p in pats)]
    for sha in git("rev-list", rng.replace("...", "..")).split():
        if any(p in git("log", "-1", "--format=%B", sha).lower() for p in pats):
            hits.append(f"commit {sha[:10]} message")
    for hit in hits:
        print(f"::error::private value added at {hit}")
    print(f"checked {rng}: {len(hits)} hit(s) for {len(pats)} pattern(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
