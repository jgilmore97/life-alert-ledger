"""
Where the league password lives.

Named league_secrets rather than secrets so it never shadows the stdlib module.

macOS Keychain is the store. Nothing here ever prints the secret — callers use
it to sign a request and print the response, so the password stays out of shell
history, out of the repo, and out of any transcript.

Store it once (this prompts, so it never lands in shell history):

    security add-generic-password -a "$USER" -s life-alert-ledger -w

Update it later by adding -U to that same command.
"""
from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

KEYCHAIN_SERVICE = "life-alert-ledger"

_HOWTO = f"""The league password isn't stored yet. Add it to your Keychain:

    security add-generic-password -a "$USER" -s {KEYCHAIN_SERVICE} -w

That prompts for the value instead of taking it as an argument, so it stays out
of your shell history. Use -U on the same command to change it later."""


def _from_keychain() -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-a", getpass.getuser(), "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _from_dotenv() -> str | None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "LEAGUE_PASSWORD":
            return value.strip().strip("\"'") or None
    return None


def get_league_password() -> str:
    """Resolve the password: environment override, then Keychain, then .env."""
    for source in (lambda: os.getenv("LEAGUE_PASSWORD"), _from_keychain, _from_dotenv):
        value = source()
        if value:
            return value
    raise SystemExit(_HOWTO)
