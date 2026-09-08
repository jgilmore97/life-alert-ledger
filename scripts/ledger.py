"""
Read the trade ledger from the command line.

    python scripts/ledger.py health          is the worker up?
    python scripts/ledger.py list            every filed trade, newest first
    python scripts/ledger.py pending         only conditional trades awaiting settlement
    python scripts/ledger.py raw             the JSON, for piping

Authenticates with the league password from the Keychain (see secrets.py). The
password is never printed, logged, or passed on the command line.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from league_secrets import get_league_password  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def endpoint() -> str:
    cfg = json.loads((ROOT / "config" / "site.json").read_text())
    url = cfg.get("endpoint")
    if not url:
        raise SystemExit("No endpoint in config/site.json — deploy the worker first.")
    return url.rstrip("/")


def fetch(season: int | None = None, limit: int = 200) -> list[dict]:
    params = {"limit": limit}
    if season:
        params["season"] = season
    r = requests.get(
        endpoint() + "/trades",
        params=params,
        headers={"X-League-Password": get_league_password()},
        timeout=30,
    )
    if r.status_code == 401:
        raise SystemExit("The stored password was rejected. Update it with:\n"
                         '    security add-generic-password -a "$USER" -s life-alert-ledger -w -U')
    r.raise_for_status()
    return r.json().get("trades", [])


def last_name(v) -> str:
    return str(v or "").split(" ")[-1]


def describe(side: dict) -> str:
    bits = [f"{p['name']} ({p['pos']})" for p in side.get("players", [])]
    bits += [f"{p['year']} {ORD.get(p['round'], p['round'])}" for p in side.get("picks", [])]
    return ", ".join(bits) or "nothing"


def show(trades: list[dict]) -> None:
    if not trades:
        print("No trades filed yet.")
        return
    for t in trades:
        a, b = t.get("side_a", {}), t.get("side_b", {})
        flag = {"pending_condition": "CONDITIONAL", "resolved": "RESOLVED"}.get(t.get("status"), "SETTLED")
        print(f"\n[{flag}]  {(t.get('filed_at') or '')[:10]}  ({t.get('id', '')[:8]})")
        print(f"  {last_name(a.get('manager')):<12} sends  {describe(a)}")
        print(f"  {last_name(b.get('manager')):<12} sends  {describe(b)}")
        c = t.get("condition")
        if c:
            print(f"  Condition : {c.get('text')}")
            print(f"  Measured  : {c.get('window_text')} · {c.get('rank_by')} · {c.get('scoring')}")
            for s in t.get("scenarios", []):
                picks = "; ".join(
                    f"{p['year']} {ORD.get(p['round'], p['round'])} {last_name(p.get('from'))}"
                    f" -> {last_name(p.get('to'))}" for p in s.get("picks", [])
                ) or "no picks move"
                head = "Yes" if s.get("branch") == "if_true" else "No "
                print(f"    {head}: {s.get('label') or '—'} — {picks}")
    print(f"\n{len(trades)} trade(s).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["health", "list", "pending", "raw"])
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()

    if args.command == "health":
        r = requests.get(endpoint() + "/health", timeout=20)
        print(f"HTTP {r.status_code}  {r.text.strip()}")
        return

    trades = fetch(season=args.season)
    if args.command == "pending":
        trades = [t for t in trades if t.get("status") == "pending_condition"]
    if args.command == "raw":
        print(json.dumps(trades, indent=2))
    else:
        show(trades)


if __name__ == "__main__":
    main()
