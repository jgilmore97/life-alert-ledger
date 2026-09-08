"""
Does the settlement agent work?

Two questions, tested separately, because they fail for different reasons:

  INTERPRETATION — does Claude turn league-speak into the right clause?
      Asserted against scripts/settle/cases.py. This is the model under test.

  EVALUATION — does the deterministic half compute the right numbers?
      Checked against a completed season's real ESPN data, and against itself:
      a "top-1 RB" and a "top-100 RB" clause on the same player must both agree
      with the rank the leaderboard actually gives him. A rank that satisfies
      one and not the other in the wrong direction is an evaluator bug.

Usage:
    PY=../FantasyWork/FantasyAgent/venv/bin/python
    $PY -m scripts.settle.run_tests                 # interpretation only, no ESPN
    $PY -m scripts.settle.run_tests --evaluate      # + real 2025 numbers
    $PY -m scripts.settle.run_tests --case "RB2"    # one case
    $PY -m scripts.settle.run_tests --model sonnet  # cheaper while iterating

The default is the model the agent actually ships with, so a bare run tests what
will settle real trades. `--model sonnet` is for iterating on cases and prompt
wording without paying Opus rates; a green suite on Sonnet is encouraging but is
not the result that matters.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT.parent / "FantasyWork" / "FantasyAgent" / ".env")
load_dotenv(ROOT / ".env")

from scripts.settle.cases import CASES                      # noqa: E402
from scripts.settle.interpret import MODEL, interpret       # noqa: E402
from scripts.settle.predicates import Interpretation, Term, evaluate   # noqa: E402
from scripts.settle.stats import SeasonStats                # noqa: E402

# A completed season to test against. The dynasty league didn't exist in 2025,
# so the numbers come from the sibling league — same ESPN mechanics, and the
# point here is the pipeline, not this league's own history.
TEST_LEAGUE = int(os.getenv("LEAGUE_ID", "0"))
TEST_SEASON = 2025

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def check(interp, expect: dict) -> list[str]:
    """Everything the interpretation got wrong, in plain terms."""
    bad = []
    terms = interp.terms
    first = terms[0] if terms else None

    if "settleable" in expect and interp.settleable != expect["settleable"]:
        bad.append(f"settleable: expected {expect['settleable']}, got {interp.settleable}")
    if not interp.settleable:
        return bad                       # nothing else applies

    if "kind" in expect and (not first or first.kind != expect["kind"]):
        bad.append(f"kind: expected {expect['kind']}, got {first.kind if first else None}")
    if "operator" in expect and (not first or first.operator != expect["operator"]):
        bad.append(f"operator: expected {expect['operator']}, got {first.operator if first else None}")
    if "threshold" in expect and (not first or first.threshold != expect["threshold"]):
        bad.append(f"threshold: expected {expect['threshold']}, got {first.threshold if first else None}")
    if "stat" in expect and (not first or first.stat != expect["stat"]):
        bad.append(f"stat: expected {expect['stat']}, got {first.stat if first else None}")
    if "position" in expect and (not first or (first.position or "").upper()
                                 != expect["position"].upper()):
        bad.append(f"position: expected {expect['position']}, got {first.position if first else None}")
    if "measure" in expect and (not first or first.measure != expect["measure"]):
        bad.append(f"measure: expected {expect['measure']}, got {first.measure if first else None}")
    if "manager_contains" in expect and (not first or expect["manager_contains"].lower()
                                         not in (first.manager_name or "").lower()):
        bad.append(f"manager: expected to contain {expect['manager_contains']!r}, "
                   f"got {first.manager_name if first else None!r}")
    if "basis" in expect and (not first or first.basis != expect["basis"]):
        bad.append(f"basis: expected {expect['basis']}, got {first.basis if first else None}")
    if "combine" in expect and interp.combine != expect["combine"]:
        bad.append(f"combine: expected {expect['combine']}, got {interp.combine}")
    if "n_terms" in expect and len(terms) != expect["n_terms"]:
        bad.append(f"terms: expected {expect['n_terms']}, got {len(terms)}")
    if "branch_ok" in expect and interp.branch_ok != expect["branch_ok"]:
        bad.append(f"branch_ok: expected {expect['branch_ok']}, got {interp.branch_ok}")
    if "player_contains" in expect and (not first or expect["player_contains"].lower()
                                        not in first.player_name.lower()):
        bad.append(f"player: expected to contain {expect['player_contains']!r}, "
                   f"got {first.player_name if first else None!r}")
    if "has_notes" in expect and expect["has_notes"] and not interp.notes:
        bad.append("expected a note flagging the ambiguity, got none")
    if "not_single_stat" in expect:
        # The trap: quietly settling a two-stat condition on one stat.
        if len(terms) == 1 and terms[0].stat == expect["not_single_stat"]:
            bad.append(f"settled a multi-stat condition on {expect['not_single_stat']} alone")
    return bad


def show_evaluation(record, interp, stats) -> None:
    cond = record["condition"]
    verdict = evaluate(interp, stats, cond["from_week"], cond["to_week"])
    if not verdict.settled:
        print(f"    {YELLOW}unsettled{OFF}: {'; '.join(verdict.problems)}")
        return
    mark = f"{GREEN}MET{OFF}" if verdict.met else f"{RED}NOT MET{OFF}"
    print(f"    verdict: {mark}")
    for r in verdict.terms:
        print(f"      {r.detail}")
        for line in r.context[:14]:
            print(f"        {DIM}{line}{OFF}")


def rank_consistency(stats, player_name: str, position: str,
                     from_week: int, to_week: int) -> str:
    """An evaluator self-check that needs no memory of how the season went:
    the rank the leaderboard gives a player must be the rank a top-N clause
    settles on, for every N. Catches off-by-ones and pool-filtering bugs."""
    stats.load(range(from_week, to_week + 1))
    pid = stats.find_player(player_name)
    board = stats.leaderboard(position, from_week, to_week, basis="total")
    true_rank = next((i for i, w in enumerate(board, 1) if w.player_id == pid), None)
    if true_rank is None:
        return f"{RED}FAIL{OFF} {player_name} not on the {position} board"

    for n in (1, true_rank - 1, true_rank, true_rank + 1, 100):
        if n < 1:
            continue
        term = Term(kind="position_rank", player_name=player_name, position=position,
                    manager_name=None, measure=None, stat=None, basis="total",
                    operator="lte", threshold=n, min_games=None)
        got = evaluate(_wrap(term), stats, from_week, to_week).met
        if got is not (true_rank <= n):
            return (f"{RED}FAIL{OFF} {player_name} is {position}{true_rank}; "
                    f"top-{n} clause returned {got}")
    return (f"{GREEN}PASS{OFF} {player_name} = {position}{true_rank} of {len(board)}, "
            f"consistent at every threshold")


def _wrap(term):
    """One term, as a settleable Interpretation — for checking the evaluator
    without going through the model."""
    return Interpretation(understanding="", settleable=True, unsettleable_reason=None,
                          combine="single", terms=[term], branch_ok=True,
                          confidence="high", notes=[])


def placement_consistency(stats, manager: str, measure: str) -> str:
    """Same idea as rank_consistency, for standings: the finish a manager
    actually has must agree with what a threshold clause settles, at every
    threshold. Catches an off-by-one between seed and final rank."""

    team = stats.find_manager(manager)
    if team is None:
        return f"{RED}FAIL{OFF} couldn't resolve {manager!r}"
    actual = team.measure(measure)

    for n in range(1, stats.team_count + 1):
        for op, want in (("lte", actual <= n), ("gte", actual >= n)):
            term = Term(kind="manager_placement", player_name="", manager_name=manager,
                        measure=measure, position=None, stat=None, basis="total",
                        operator=op, threshold=n, min_games=None)
            if evaluate(_wrap(term), stats, 1, 15).met is not want:
                return (f"{RED}FAIL{OFF} {team.manager} has {measure} {actual}; "
                        f"{op} {n} returned the wrong answer")
    return (f"{GREEN}PASS{OFF} {team.manager} {measure} = {actual:g}, "
            f"consistent at all {stats.team_count} thresholds both ways")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evaluate", action="store_true",
                    help="also run each interpretation against real 2025 ESPN numbers")
    ap.add_argument("--case", help="substring of a case name, to run just that one")
    ap.add_argument("--model", default=MODEL,
                    help=f"model to interpret with (default {MODEL}). "
                         "Accepts the shorthands opus / sonnet / haiku.")
    args = ap.parse_args()

    # Shorthands so iterating doesn't mean typing a model id.
    model = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5",
             "haiku": "claude-haiku-4-5"}.get(args.model, args.model)

    cases = [c for c in CASES if not args.case or args.case.lower() in c["name"].lower()]
    stats = None
    if args.evaluate:
        if not TEST_LEAGUE:
            sys.exit("Set LEAGUE_ID for a league with a completed 2025 season.")
        stats = SeasonStats(league_id=TEST_LEAGUE, season=TEST_SEASON,
                            espn_s2=os.getenv("ESPN_S2"), swid=os.getenv("SWID"),
                            playoff_teams=4)

    print(f"{DIM}interpreting with {model}"
          + ("" if model == MODEL else f"  (ships with {MODEL})") + OFF)

    passed = failed = 0
    for case in cases:
        print(f"\n{case['name']}")
        print(f"  {DIM}{case['record']['condition']['text']!r}{OFF}")
        interp = interpret(case["record"], model=model)

        print(f"  read as: {interp.understanding}")
        if interp.settleable:
            for t in interp.terms:
                bits = [t.kind, t.player_name or (t.manager_name or "")]
                if t.measure:
                    bits.append(t.measure)
                if t.position:
                    bits.append(t.position)
                if t.stat:
                    bits.append(t.stat)
                bits.append(f"{t.operator} {t.threshold:g}"
                            + ("" if t.kind == "manager_placement" else f" ({t.basis})"))
                print(f"    -> {' | '.join(bits)}")
            if interp.combine != "single":
                print(f"    -> combined with {interp.combine.upper()}")
        else:
            print(f"    -> {YELLOW}not settleable{OFF}: {interp.unsettleable_reason}")
        for note in interp.notes:
            print(f"    {DIM}note: {note}{OFF}")
        if not interp.branch_ok:
            print(f"    {YELLOW}branch labels look inverted{OFF}")

        problems = check(interp, case["expect"])
        if problems:
            failed += 1
            print(f"  {RED}FAIL{OFF} ({interp.confidence} confidence)")
            for p in problems:
                print(f"    - {p}")
        else:
            passed += 1
            print(f"  {GREEN}PASS{OFF} ({interp.confidence} confidence)")

        if stats is not None and interp.settleable:
            show_evaluation(case["record"], interp, stats)

    print(f"\n{'='*66}")
    print(f"interpretation: {GREEN}{passed} passed{OFF}, "
          f"{RED if failed else DIM}{failed} failed{OFF}, {len(cases)} total"
          f"  {DIM}[{model}]{OFF}")

    if stats is not None:
        print("\nevaluator self-check (rank consistency):")
        for name, pos in (("Jahmyr Gibbs", "RB"), ("Puka Nacua", "WR"), ("Tony Pollard", "RB")):
            print(f"  {rank_consistency(stats, name, pos, 1, 15)}")
        print("evaluator self-check (standings consistency):")
        for who, measure in (("Derek Topper", "regular_season_rank"),
                             ("Jack Gilmore", "final_rank"),
                             ("Maggie Mansfield", "wins")):
            print(f"  {placement_consistency(stats, who, measure)}")
        print(f"\n{DIM}pool: {stats.pool_size} players, "
              f"league {TEST_LEAGUE} scoring, {TEST_SEASON}{OFF}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
