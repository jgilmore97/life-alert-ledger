"""
What a condition means, and how it gets checked.

The model interprets, code decides: Claude turns "if Pollard is a top-10 RB" into a
Predicate, and the evaluator below runs it against ESPN's numbers and returns a verdict
with the arithmetic attached. No model ever sees a stat line and announces a winner.

The week window and scoring are not the model's to choose — the form captured them as
structured fields. The model only interprets "what counts".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from .stats import POSITION_POOLS, SeasonStats, Window, pool_positions

Operator = Literal["lte", "gte", "lt", "gt", "eq"]

OP_TEXT = {"lte": "at most", "gte": "at least", "lt": "under", "gt": "over", "eq": "exactly"}

# Raw counting stats a condition can plausibly name. Keys are ESPN's own names
# from PLAYER_STATS_MAP, so the evaluator can look them up directly.
STAT_CHOICES = [
    "rushingYards", "rushingTouchdowns", "rushingAttempts",
    "receivingYards", "receivingTouchdowns", "receivingReceptions",
    "passingYards", "passingTouchdowns", "passingInterceptions",
    # Combined totals, derived in stats.py because league-speak asks for them
    # constantly and per-stat clauses can't express them.
    "scrimmageYards", "totalTouchdowns", "totalYards",
]


def compare(value: float, operator: Operator, threshold: float) -> bool:
    return {
        "lte": value <= threshold, "gte": value >= threshold,
        "lt": value < threshold, "gt": value > threshold,
        "eq": value == threshold,
    }[operator]


# --------------------------------------------------------------------------
# The schema Claude fills in
# --------------------------------------------------------------------------

class Term(BaseModel):
    """One checkable clause. Flat rather than a union of shapes, because a flat
    schema with an explicit `kind` is what models fill in most reliably."""

    kind: Literal["position_rank", "fantasy_points", "stat_total", "games_played",
                  "manager_placement"] = Field(
        description=(
            "position_rank: finishes top-N at his position (the common case). "
            "fantasy_points: a raw fantasy-point threshold. "
            "stat_total: a box-score counting stat like 1,000 rushing yards. "
            "games_played: how many games he appeared in. "
            "manager_placement: how a MANAGER'S TEAM finished — made the playoffs, "
            "won the title, finished last. Not about a player at all."
        )
    )
    player_name: str = Field(
        description="The player the clause is about, as written in the trade. "
                    "Empty string for manager_placement, which is about a team."
    )
    manager_name: str | None = Field(
        description="The manager whose finish the clause turns on. Only for manager_placement."
    )
    measure: Literal["regular_season_rank", "final_rank", "wins"] | None = Field(
        description=(
            "Which finish, for manager_placement only. "
            "regular_season_rank: the seed after the last regular-season week — use this for "
            "MAKING the playoffs, and for regular-season finish. "
            "final_rank: where they ended up after the bracket — use this for winning the title, "
            "reaching the final, or finishing last. "
            "wins: regular-season wins."
        )
    )
    position: str | None = Field(
        description=(
            "The pool to rank within, for position_rank only. Either one position — "
            "QB, RB, WR, TE, K, D/ST — or a combined pool: FLEX (RB/WR/TE), "
            "SUPERFLEX (QB/RB/WR/TE), REC_FLEX (WR/TE)."
        )
    )
    stat: str | None = Field(
        description=f"Which counting stat, only for stat_total. One of: {', '.join(STAT_CHOICES)}"
    )
    basis: Literal["total", "ppg"] = Field(
        description="Rank/measure by total points or points per game. Copy the trade's `rank_by` field."
    )
    operator: Operator = Field(
        description="How the measured value must compare to the threshold. 'Top 10' is lte 10 on rank."
    )
    threshold: float = Field(description="The number being compared against. Top-10 -> 10.")
    min_games: int | None = Field(
        description="Games required to qualify for a ranking, if the condition says so. Usually null."
    )


class Interpretation(BaseModel):
    """Claude's reading of one condition."""

    understanding: str = Field(
        description="One sentence restating the condition as you understood it, in plain English."
    )
    settleable: bool = Field(
        description=(
            "True only if every clause is checkable from this league's scoring, its box-score "
            "stats, or its standings. False when a condition names no number to check it "
            "against, or needs something the league's own data doesn't hold. Do not guess: a "
            "false here sends it to the commissioner, which is the correct outcome for those."
        )
    )
    unsettleable_reason: str | None = Field(
        description="If settleable is false, what specifically cannot be measured."
    )
    combine: Literal["and", "or", "single"] = Field(
        description="How multiple terms combine. Use 'single' when there is exactly one term."
    )
    terms: list[Term] = Field(description="The clauses. Empty when settleable is false.")
    branch_ok: bool = Field(
        description=(
            "Whether the trade's if_true scenario really is the branch where this condition HOLDS. "
            "False if the scenario labels look inverted relative to the condition text."
        )
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="How confident you are in this reading. Low means a human should look."
    )
    notes: list[str] = Field(
        description="Anything the commissioner should know: ambiguity, an assumption you made, a term you had to stretch."
    )


# --------------------------------------------------------------------------
# The deterministic half
# --------------------------------------------------------------------------

@dataclass
class TermResult:
    term: Term
    met: bool
    measured: float                  # the value that was compared
    detail: str = ""
    window: Window | None = None
    context: list[str] = field(default_factory=list)   # the audit trail


@dataclass
class Verdict:
    met: bool | None                 # None = could not be settled
    combine: str
    terms: list[TermResult]
    problems: list[str] = field(default_factory=list)

    @property
    def settled(self) -> bool:
        return self.met is not None


def evaluate(interp: Interpretation, stats: SeasonStats,
             from_week: int, to_week: int) -> Verdict:
    """Run an interpretation against real numbers. Pure computation."""
    if not interp.settleable:
        return Verdict(met=None, combine=interp.combine, terms=[],
                       problems=[interp.unsettleable_reason or "Not settleable from stats."])

    results, problems = [], []
    for term in interp.terms:
        try:
            results.append(_evaluate_term(term, stats, from_week, to_week))
        except Unsettleable as exc:
            problems.append(str(exc))

    if problems or not results:
        return Verdict(met=None, combine=interp.combine, terms=results, problems=problems)

    flags = [r.met for r in results]
    met = all(flags) if interp.combine == "and" else (any(flags) if interp.combine == "or" else flags[0])
    return Verdict(met=met, combine=interp.combine, terms=results)


class Unsettleable(Exception):
    """Raised when the numbers needed simply aren't there."""


def _subject(term: Term, stats: SeasonStats, from_week: int, to_week: int) -> Window:
    stats.load(range(from_week, to_week + 1))
    pid = stats.find_player(term.player_name)
    if pid is None:
        raise Unsettleable(f"Couldn't pin down '{term.player_name}' in the player pool.")
    win = stats.window(pid, from_week, to_week)
    if win is None or win.games == 0:
        raise Unsettleable(f"{term.player_name} has no games in weeks {from_week}–{to_week}.")
    return win


def _evaluate_term(term: Term, stats: SeasonStats, from_week: int, to_week: int) -> TermResult:
    if term.kind == "manager_placement":
        return _evaluate_placement(term, stats)

    win = _subject(term, stats, from_week, to_week)

    if term.kind == "position_rank":
        position = (term.position or win.position).upper()
        eligible = pool_positions(position)
        is_pool = position in POSITION_POOLS
        if win.position not in eligible:
            raise Unsettleable(
                f"{win.name} is a {win.position}, so he can't be ranked in "
                f"{position} ({'/'.join(eligible)})."
            )

        board = stats.leaderboard(position, from_week, to_week,
                                  basis=term.basis, min_games=term.min_games or 1)
        ranks = {w.player_id: i for i, w in enumerate(board, start=1)}
        if win.player_id not in ranks:
            raise Unsettleable(
                f"{win.name} didn't qualify for the {position} board "
                f"(needs {term.min_games} games, played {win.games})."
            )
        rank = ranks[win.player_id]
        met = compare(rank, term.operator, term.threshold)
        basis_text = "points per game" if term.basis == "ppg" else "total points"
        # Keep the neighbourhood of the cutoff — that's the part anyone disputes.
        edge = int(term.threshold)
        context = [f"{i:>3}. {w.name:<22} {w.position if is_pool else '':<5} "
                   f"{w.value(term.basis):>7}  ({w.games} games)"
                   for i, w in enumerate(board, start=1)
                   if i <= edge + 3 or abs(i - rank) <= 2]
        # "RB23" reads naturally; "FLEX23" doesn't, so a pool gets said out loud.
        placing = (f"{_ordinal(rank)} in the {position} pool ({'/'.join(eligible)})"
                   if is_pool else f"{position}{rank}")
        return TermResult(
            term=term, met=met, measured=float(rank), window=win,
            detail=(f"{win.name} finished {placing} of {len(board)} by {basis_text} "
                    f"in weeks {from_week}–{to_week} "
                    f"({win.value(term.basis)} {basis_text}, {win.games} games)."),
            context=context,
        )

    if term.kind == "fantasy_points":
        value = win.value(term.basis)
        basis_text = "points per game" if term.basis == "ppg" else "total points"
        return TermResult(
            term=term, met=compare(value, term.operator, term.threshold),
            measured=value, window=win,
            detail=(f"{win.name} scored {value} {basis_text} in weeks {from_week}–{to_week} "
                    f"— needed {OP_TEXT[term.operator]} {term.threshold}."),
        )

    if term.kind == "stat_total":
        if not term.stat:
            raise Unsettleable("A counting-stat condition didn't say which stat.")
        value = win.raw.get(term.stat, 0.0)
        return TermResult(
            term=term, met=compare(value, term.operator, term.threshold),
            measured=value, window=win,
            detail=(f"{win.name} put up {value:g} {_stat_text(term.stat)} in weeks "
                    f"{from_week}–{to_week} — needed {OP_TEXT[term.operator]} {term.threshold:g}."),
        )

    if term.kind == "games_played":
        return TermResult(
            term=term, met=compare(win.games, term.operator, term.threshold),
            measured=float(win.games), window=win,
            detail=(f"{win.name} played {win.games} games in weeks {from_week}–{to_week} "
                    f"— needed {OP_TEXT[term.operator]} {term.threshold:g}."),
        )

    raise Unsettleable(f"Unknown condition kind '{term.kind}'.")


PLACEMENT_TEXT = {
    "regular_season_rank": "regular-season seed",
    "final_rank": "final standing",
    "wins": "regular-season wins",
}


def _evaluate_placement(term: Term, stats: SeasonStats) -> TermResult:
    """How a manager's team finished. Window-free: the trade's week range scopes a
    player's production and has no bearing on standings."""
    if not term.manager_name:
        raise Unsettleable("A placement condition didn't say which manager.")
    if not term.measure:
        raise Unsettleable("A placement condition didn't say which finish it turns on.")

    team = stats.find_manager(term.manager_name)
    if team is None:
        raise Unsettleable(
            f"Couldn't pin down '{term.manager_name}' to one team — check the spelling, "
            f"or use a full name if two managers share a surname."
        )

    value = team.measure(term.measure)
    met = compare(value, term.operator, term.threshold)
    label = PLACEMENT_TEXT[term.measure]
    if term.measure == "wins":
        detail = (f"{team.manager} won {team.wins} games ({team.wins}-{team.losses}) "
                  f"— needed {OP_TEXT[term.operator]} {term.threshold:g}.")
    else:
        detail = (f"{team.manager} finished {_ordinal(int(value))} by {label} "
                  f"of {stats.team_count} — needed {OP_TEXT[term.operator]} "
                  f"{_ordinal(int(term.threshold))}.")

    order = "wins" if term.measure == "wins" else term.measure
    board = sorted(stats.standings,
                   key=lambda t: -t.wins if order == "wins" else t.measure(order))
    context = [f"{t.measure(order):>3g}  {t.manager:<20} {t.team_name[:22]:<22} "
               f"{t.wins}-{t.losses}" for t in board]
    return TermResult(term=term, met=met, measured=float(value), window=None,
                      detail=detail, context=context)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _stat_text(stat: str) -> str:
    """camelCase ESPN stat name -> something readable in a settlement note."""
    words = []
    for ch in stat:
        words.append(" " + ch.lower() if ch.isupper() else ch)
    return "".join(words).strip()
