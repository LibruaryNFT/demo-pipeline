#!/usr/bin/env python3
"""Fail if anything that looks like a credential is committed.

GitHub's own secret scanning and push protection are only free on public
repositories; on a private one they need paid Advanced Security. This is the
stand-in, so a shared private repo still has a mechanical guard rather than
relying on everyone remembering.

    python scripts/scan_secrets.py            # scan tracked files
    python scripts/scan_secrets.py --history  # scan every blob ever committed

Exit code is 1 if anything matched. Run --selftest to prove the patterns
still catch planted secrets; a scanner nobody has verified is worse than
none, because it reports clean either way.
"""

import argparse
import re
import subprocess

PATTERNS = {
    "OpenAI key": r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{20,}",
    "GitHub PAT": r"github_pat_[A-Za-z0-9_]{20,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "Google API key": r"AIza[0-9A-Za-z_-]{35}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "Private key block": r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY",
    "JWT": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.",
    "Hex secret (64)": r"\b[0-9a-f]{64}\b",
}

# Files that legitimately contain pattern-like strings.
ALLOWLIST = ("scripts/scan_secrets.py", "tests/test_scan_secrets.py")

COMPILED = {name: re.compile(p) for name, p in PATTERNS.items()}


def scan_text(text: str) -> list[tuple[str, str]]:
    """Return (pattern name, matched excerpt) for every hit."""
    hits = []
    for name, rx in COMPILED.items():
        for m in rx.finditer(text):
            excerpt = m.group(0)
            # Never print a full credential, even into CI logs.
            hits.append((name, excerpt[:12] + "..."))
    return hits


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def scan_tracked() -> int:
    failures = 0
    for path in _git("ls-files").splitlines():
        if path in ALLOWLIST:
            continue
        try:
            content = _git("show", f"HEAD:{path}")
        except subprocess.CalledProcessError:
            continue
        for name, excerpt in scan_text(content):
            print(f"{path}: possible {name} ({excerpt})")
            failures += 1
    return failures


def scan_history() -> int:
    failures = 0
    objects = _git("rev-list", "--objects", "--all").splitlines()
    for line in objects:
        parts = line.split(maxsplit=1)
        sha = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        if path in ALLOWLIST:
            continue
        try:
            kind = _git("cat-file", "-t", sha).strip()
            if kind != "blob":
                continue
            content = _git("cat-file", "-p", sha)
        except (subprocess.CalledProcessError, UnicodeDecodeError):
            continue
        for name, excerpt in scan_text(content):
            print(f"{sha} ({path or 'unknown path'}): possible {name} ({excerpt})")
            failures += 1
    return failures


def selftest() -> int:
    """Prove each pattern still fires. A clean scan is only meaningful if
    the scanner is known to detect what it claims to."""
    planted = {
        "OpenAI key": "sk-proj-" + "A" * 24,
        "GitHub token": "ghp_" + "A" * 36,
        "GitHub PAT": "github_pat_" + "A" * 22,
        "AWS access key": "AKIAIOSFODNN7EXAMPLE",
        "Google API key": "AIza" + "A" * 35,
        "Slack token": "xoxb-" + "1" * 12,
        "Private key block": "-----BEGIN RSA PRIVATE KEY",
        "JWT": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc",
        "Hex secret (64)": "a" * 64,
    }
    missed = []
    for name, sample in planted.items():
        if not COMPILED[name].search(sample):
            missed.append(name)
    if missed:
        print("SELFTEST FAILED — these patterns did not match: " + ", ".join(missed))
        return 1
    print(f"selftest ok — all {len(planted)} patterns matched planted samples")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", action="store_true", help="scan all git history")
    ap.add_argument("--selftest", action="store_true", help="verify the patterns")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    failures = scan_history() if args.history else scan_tracked()
    scope = "history" if args.history else "tracked files"
    if failures:
        print(f"\n{failures} possible secret(s) found in {scope}.")
        return 1
    print(f"no secrets found in {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
