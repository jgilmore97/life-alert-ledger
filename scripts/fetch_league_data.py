"""
Pull canonical league identities from ESPN. Read-only; nothing here writes back.

Writes two files consumed by the trade form:
  config/managers.json  — manager list (name, team id, claimed)
  config/players.json   — NFL player universe (id, name, position, pro team)

Usage:  python scripts/fetch_league_data.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from espn_api.football import League
from espn_api.football.constant import PRO_TEAM_MAP

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from settle.stats import position_of  # noqa: E402

# Credentials are shared with the FantasyAgent project (same ESPN account).
load_dotenv(ROOT.parent / "FantasyWork" / "FantasyAgent" / ".env")
load_dotenv(ROOT / ".env")  # local overrides win if present

LEAGUE_ID = int(os.getenv("COMMISH_LEAGUE_ID", "1988439171"))
SEASON = int(os.getenv("COMMISH_SEASON", "2026"))

# Positions that can plausibly appear in a trade.
TRADEABLE_POSITIONS = {"QB", "RB", "WR", "TE", "K", "D/ST"}


def _league() -> League:
    return League(
        league_id=LEAGUE_ID,
        year=SEASON,
        espn_s2=os.getenv("ESPN_S2"),
        swid=os.getenv("SWID"),
    )


def fetch_managers(lg: League) -> list[dict]:
    """One record per team; unclaimed teams are kept so the roster is complete.

    ESPN's owner id is deliberately NOT stored. It is the manager's SWID cookie value —
    a permanent per-account identifier that can't be rotated — and this repo has to be
    public for Pages. `team_id` is the stable key, and `claimed` is the only thing the
    owner id was ever consulted for, so it's resolved to a boolean here and dropped.
    """
    managers = []
    for team in lg.teams:
        owners = getattr(team, "owners", None) or []
        if owners:
            o = owners[0]
            first, last = (o.get("firstName") or "").strip(), (o.get("lastName") or "").strip()
            name = f"{first} {last}".strip()
            claimed = bool(o.get("id"))
        else:
            name, claimed = None, False
        managers.append(
            {
                "id": f"team{team.team_id}",       # stable trackable identifier
                "name": name or f"(unclaimed) {team.team_name}",
                "team_id": team.team_id,
                "team_name": team.team_name,
                "claimed": claimed,
            }
        )
    return sorted(managers, key=lambda m: (not m["claimed"], m["name"]))


def fetch_players(lg: League, limit: int = 3000) -> list[dict]:
    """The full player universe, ranked by ownership so autocomplete surfaces relevant
    names first. No status filter — rostered players are included."""
    filters = {
        "players": {
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    data = lg.espn_request.league_get(
        params={"view": "kona_player_info", "scoringPeriodId": 0},
        headers={"x-fantasy-filter": json.dumps(filters)},
    )

    players = []
    for entry in data.get("players", []):
        p = entry.get("player") or {}
        pos = position_of(p)
        if pos not in TRADEABLE_POSITIONS:
            continue
        players.append(
            {
                "id": p.get("id"),
                "name": p.get("fullName"),
                "pos": pos,
                "team": PRO_TEAM_MAP.get(p.get("proTeamId"), "FA"),
                "owned": round((p.get("ownership") or {}).get("percentOwned", 0) or 0, 1),
            }
        )
    return players


def main() -> None:
    lg = _league()
    cfg = ROOT / "config"
    cfg.mkdir(exist_ok=True)

    managers = fetch_managers(lg)
    players = fetch_players(lg)

    (cfg / "managers.json").write_text(json.dumps(managers, indent=2))
    (cfg / "players.json").write_text(json.dumps(players, indent=2))

    claimed = sum(m["claimed"] for m in managers)
    print(f"league   : {lg.settings.name} ({LEAGUE_ID}, {SEASON})")
    print(f"managers : {len(managers)} teams, {claimed} claimed -> config/managers.json")
    print(f"players  : {len(players)} tradeable -> config/players.json")
    for m in managers:
        flag = " " if m["claimed"] else "!"
        print(f"  {flag} {m['id']:<8} {m['name']:<20} {m['team_name']}")


if __name__ == "__main__":
    main()
