"""
The stats layer: what a player actually did, over an exact window of weeks.

This is the half of settlement that must never guess. It answers three questions
deterministically, straight from ESPN, with no model in the loop:

    how many fantasy points did this player score in weeks X..Y
    where did that rank him among everyone at his position
    what were his raw counting stats (rushing yards, receptions, ...)

ESPN detail that shapes everything here: `kona_player_info` returns weekly stat
splits only for the scoringPeriodId you ask for. There is no "give me the whole
season by week" call, so a window of N weeks costs N requests. They're cached to
disk — settlement is re-run often while arguing, and the cache doubles as the
audit trail for what the numbers were on settlement day.

Stat entry shape, for the record:
    statSourceId  0 = actual, 1 = projected      <- we only ever take 0
    statSplitTypeId 1 = single week, 0 = season
    appliedTotal  fantasy points IN THE LEAGUE'S SCORING (this is why the
                  request goes through the league endpoint and not a generic one)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from espn_api.football import League
from espn_api.football.constant import POSITION_MAP, PLAYER_STATS_MAP, PRO_TEAM_MAP

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "data" / "stats_cache"

# How deep into the ownership-ranked player pool to look. A player who could
# plausibly be the subject of a trade condition is never outside the top ~1500
# by percent owned; the pool size is recorded in every result so a settlement
# can be challenged on this if it ever matters.
POOL_LIMIT = 1500

# A "top 20 flex" finish ranks across the combined pool, exactly the way a
# single-position finish ranks across one. These are ESPN's own composite
# lineup slots — this league starts two RB/WR/TE flexes and one superflex (OP),
# so both are live here. Anything not listed is a single position.
POSITION_POOLS = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPERFLEX": ("QB", "RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
}


def pool_positions(name: str) -> tuple[str, ...]:
    """The positions a pool name covers. A plain position covers itself."""
    return POSITION_POOLS.get((name or "").upper(), (name,))

# Conditions are routinely written against totals ESPN doesn't store as a single
# stat. "1,500 yards from scrimmage" and "10 total TDs" are ordinary league-speak,
# and splitting them into per-stat clauses is not equivalent — 1,100 rushing plus
# 450 receiving makes 1,550 scrimmage yards while failing any fixed split. So they
# get derived once, here, and become first-class stats everywhere downstream.
DERIVED_STATS = {
    "scrimmageYards": ("rushingYards", "receivingYards"),
    "totalTouchdowns": ("rushingTouchdowns", "receivingTouchdowns", "passingTouchdowns"),
    "totalYards": ("rushingYards", "receivingYards", "passingYards"),
}


def add_derived(raw: dict[str, float]) -> dict[str, float]:
    """Fill in the combined totals. Absent parts count as zero — a WR with no
    carries still has scrimmage yards."""
    for name, parts in DERIVED_STATS.items():
        if any(p in raw for p in parts):
            raw[name] = round(sum(raw.get(p, 0.0) for p in parts), 2)
    return raw


def position_of(player: dict) -> str:
    """A player's true position — the first non-composite eligible slot.

    `defaultPositionId` indexes a DIFFERENT table than POSITION_MAP and silently
    mislabels QB/WR/TE/K, so it is only trustworthy for team defenses.
    (Same derivation as scripts/fetch_league_data.py — kept in sync by hand.)
    """
    if player.get("defaultPositionId") == 16:
        return "D/ST"
    for slot in player.get("eligibleSlots", []):
        label = POSITION_MAP.get(slot, "")
        if slot != 25 and label and "/" not in label:
            return label
    return ""


@dataclass
class TeamStanding:
    """Where a manager's team finished. Two different numbers, and conditions
    mean different ones by them:

      regular_season_rank  the seed after the last regular-season week — this is
                           what "made the playoffs" turns on
      final_rank           where they ended up once the bracket was played —
                           this is what "won it all" turns on

    They diverge exactly where it matters: the 4-seed who wins the title has
    regular_season_rank 4 and final_rank 1."""
    team_id: int
    manager: str
    team_name: str
    regular_season_rank: int
    final_rank: int
    wins: int
    losses: int
    points_for: float

    def measure(self, name: str) -> float:
        return {"regular_season_rank": self.regular_season_rank,
                "final_rank": self.final_rank,
                "wins": self.wins}[name]


@dataclass
class Window:
    """One player's production over a closed week range."""
    player_id: int
    name: str
    position: str
    pro_team: str
    from_week: int
    to_week: int
    weekly: dict[int, float]              # week -> fantasy points, played weeks only
    raw: dict[str, float] = field(default_factory=dict)   # 'rushingYards' -> total

    @property
    def games(self) -> int:
        """Weeks with an actual stat line. ESPN omits the entry entirely on a
        bye or when a player is inactive, so presence == played."""
        return len(self.weekly)

    @property
    def total(self) -> float:
        return round(sum(self.weekly.values()), 2)

    @property
    def ppg(self) -> float:
        return round(self.total / self.games, 2) if self.games else 0.0

    def value(self, basis: str) -> float:
        return self.ppg if basis == "ppg" else self.total


class SeasonStats:
    """Weekly actuals for the whole player pool, one season, cached per week."""

    def __init__(self, league_id: int, season: int, espn_s2: str | None, swid: str | None,
                 playoff_teams: int | None = None):
        self.league_id = league_id
        self.season = season
        self._league = League(league_id=league_id, year=season, espn_s2=espn_s2, swid=swid)
        self._weeks: dict[int, dict] = {}    # week -> {player_id: {...}}
        self._index: dict[int, dict] = {}    # player_id -> identity
        self._standings: dict[int, TeamStanding] | None = None
        self._playoff_teams: int | None = playoff_teams

    @classmethod
    def from_env(cls, league_id: int | None = None, season: int | None = None) -> "SeasonStats":
        return cls(
            league_id=int(league_id or os.environ["COMMISH_LEAGUE_ID"]),
            season=int(season or os.environ["COMMISH_SEASON"]),
            espn_s2=os.getenv("ESPN_S2"),
            swid=os.getenv("SWID"),
        )

    # ---------- standings ----------

    def _load_standings(self) -> None:
        if self._standings is not None:
            return
        rows = {}
        for team in self._league.teams:
            owners = getattr(team, "owners", None) or []
            o = owners[0] if owners else {}
            name = f"{(o.get('firstName') or '').strip()} {(o.get('lastName') or '').strip()}".strip()
            rows[team.team_id] = TeamStanding(
                team_id=team.team_id,
                manager=name or f"(unclaimed) {team.team_name}",
                team_name=team.team_name,
                regular_season_rank=team.standing,          # ESPN: playoffSeed
                final_rank=team.final_standing,             # ESPN: rankCalculatedFinal
                wins=team.wins, losses=team.losses,
                points_for=round(team.points_for, 1),
            )
        self._standings = rows

    @property
    def standings(self) -> list[TeamStanding]:
        self._load_standings()
        return sorted(self._standings.values(), key=lambda t: t.regular_season_rank)

    @property
    def team_count(self) -> int:
        return len(self._league.teams)

    @property
    def playoff_teams(self) -> int:
        """How many teams make the bracket. ESPN reports it per league; a league
        with no playoff format configured reports 0, hence the override."""
        if self._playoff_teams is not None:
            return self._playoff_teams
        data = self._league.espn_request.league_get(params={"view": "mSettings"})
        count = (data.get("settings", {}).get("scheduleSettings", {})
                 .get("playoffTeamCount") or 0)
        self._playoff_teams = int(count)
        return self._playoff_teams

    def find_manager(self, name: str) -> TeamStanding | None:
        """Resolve however the condition wrote them — "Derek Topper", "Topper",
        "derek". Ambiguity returns None rather than picking, same as players."""
        self._load_standings()
        target = name.strip().lower()
        rows = list(self._standings.values())
        for match in (
            lambda t: t.manager.lower() == target,
            lambda t: t.manager.lower().split()[-1:] == [target],
            lambda t: target in t.manager.lower(),
            lambda t: target in t.team_name.lower(),
        ):
            hits = [t for t in rows if match(t)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return None
        return None

    # ---------- fetching ----------

    def _cache_path(self, week: int) -> Path:
        return CACHE / str(self.league_id) / str(self.season) / f"week{week:02d}.json"

    def _fetch_week(self, week: int) -> dict:
        """All players' actual stat line for one week, keyed by player id."""
        filters = {"players": {"limit": POOL_LIMIT,
                               "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
        data = self._league.espn_request.league_get(
            params={"view": "kona_player_info", "scoringPeriodId": week},
            headers={"x-fantasy-filter": json.dumps(filters)},
        )

        out: dict[str, dict] = {}
        for entry in data.get("players", []):
            p = entry.get("player") or {}
            actual = next(
                (s for s in (p.get("stats") or [])
                 if s.get("statSourceId") == 0            # actual, not projection
                 and s.get("statSplitTypeId") == 1        # this single week
                 and s.get("scoringPeriodId") == week),
                None,
            )
            if actual is None:
                continue                                  # bye, inactive, or not yet played
            out[str(p["id"])] = {
                "name": p.get("fullName"),
                "pos": position_of(p),
                "team": PRO_TEAM_MAP.get(p.get("proTeamId"), "FA"),
                "pts": round(actual.get("appliedTotal") or 0.0, 2),
                "raw": {PLAYER_STATS_MAP[int(k)]: v
                        for k, v in (actual.get("stats") or {}).items()
                        if int(k) in PLAYER_STATS_MAP},
            }
        return out

    def load(self, weeks: range | list[int], refresh: bool = False) -> None:
        """Populate the given weeks, from cache where possible."""
        for week in weeks:
            if week in self._weeks:
                continue
            path = self._cache_path(week)
            if path.exists() and not refresh:
                payload = json.loads(path.read_text())
            else:
                payload = self._fetch_week(week)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload))
            self._weeks[week] = payload
            for pid, rec in payload.items():
                self._index.setdefault(int(pid), {"name": rec["name"], "pos": rec["pos"],
                                                  "team": rec["team"]})

    # ---------- reading ----------

    def window(self, player_id: int, from_week: int, to_week: int) -> Window | None:
        """One player's production across from_week..to_week inclusive."""
        self.load(range(from_week, to_week + 1))
        ident = self._index.get(player_id)
        weekly, raw = {}, {}
        for week in range(from_week, to_week + 1):
            rec = self._weeks[week].get(str(player_id))
            if rec is None:
                continue
            ident = ident or {"name": rec["name"], "pos": rec["pos"], "team": rec["team"]}
            weekly[week] = rec["pts"]
            for stat, val in rec["raw"].items():
                raw[stat] = round(raw.get(stat, 0.0) + val, 2)
        if ident is None:
            return None
        return Window(player_id=player_id, name=ident["name"], position=ident["pos"],
                      pro_team=ident["team"], from_week=from_week, to_week=to_week,
                      weekly=weekly, raw=add_derived(raw))

    def leaderboard(self, position: str, from_week: int, to_week: int,
                    basis: str = "total", min_games: int = 1) -> list[Window]:
        """Every player in `position` with at least `min_games` games in the
        window, best first. This is the pool a "top-N" condition ranks against.

        `position` is a single position ("RB") or a composite pool ("FLEX"),
        which is nothing more special than the same ranking over more positions."""
        self.load(range(from_week, to_week + 1))
        wanted = set(pool_positions(position))
        ids = {int(pid) for week in range(from_week, to_week + 1)
               for pid, rec in self._weeks[week].items() if rec["pos"] in wanted}
        rows = [w for w in (self.window(pid, from_week, to_week) for pid in ids)
                if w and w.games >= min_games]
        return sorted(rows, key=lambda w: -w.value(basis))

    def find_player(self, name: str) -> int | None:
        """Resolve a name to a player id from the loaded pool. Exact match first,
        then a unique case-insensitive substring — ambiguity returns None rather
        than picking, because picking wrong moves a draft pick."""
        target = name.strip().lower()
        exact = [pid for pid, r in self._index.items() if (r["name"] or "").lower() == target]
        if len(exact) == 1:
            return exact[0]
        loose = [pid for pid, r in self._index.items() if target in (r["name"] or "").lower()]
        return loose[0] if len(loose) == 1 else None

    @property
    def pool_size(self) -> int:
        return len(self._index)
