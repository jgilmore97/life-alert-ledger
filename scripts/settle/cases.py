"""
Test conditions, written the way managers actually write them.

The easy case ("top-10 RB") is not what this is for. These are the phrasings
that quietly settle wrong: tier language that looks like a rank, a stat that
spans two stats, an average where the form says total, labels written
backwards, and conditions that no box score can settle at all.

`expect` holds only what must be true of the INTERPRETATION. Whether the
condition actually hit is the evaluator's business, and it is checked against
live numbers rather than anything asserted here.
"""

SUBJECTS = {
    "pollard":  {"id": 3916148, "name": "Tony Pollard", "pos": "RB", "team": "TEN"},
    "jeanty":   {"id": 4890973, "name": "Ashton Jeanty", "pos": "RB", "team": "LV"},
    "gibbs":    {"id": 4429795, "name": "Jahmyr Gibbs", "pos": "RB", "team": "DET"},
    "nacua":    {"id": 4426515, "name": "Puka Nacua", "pos": "WR", "team": "LAR"},
    "collins":  {"id": 4258173, "name": "Nico Collins", "pos": "WR", "team": "HOU"},
    "hall":     {"id": 4241416, "name": "Breece Hall", "pos": "RB", "team": "NYJ"},
}


def trade(text, *, subject=None, rank_by="total", from_week=1, to_week=15,
          if_true=None, if_false=None,
          a="Derek Topper", b="Maggie Mansfield", player=("Tony Pollard", "RB")):
    """A filed trade record, shaped exactly as the form submits it.

    `if_true`/`if_false` are the hand-written branch labels the form used to ask
    for. It no longer does — the branches are structural now — so leaving them
    unset is what a record filed today looks like. Passing them reproduces a
    legacy record, which the agent still has to read correctly."""
    return {
        "season": 2025,
        "filed_by": a,
        "side_a": {"manager_id": "team9", "manager": a,
                   "players": [{"name": player[0], "pos": player[1]}], "picks": []},
        "side_b": {"manager_id": "team5", "manager": b, "players": [], "picks": []},
        "conditional": True,
        "condition": {
            "text": text,
            "subject_player": SUBJECTS.get(subject) if subject else None,
            "settle_when": "regular_season_end",
            "scoring": "ppr",
            "rank_by": rank_by,
            "from_week": from_week,
            "to_week": to_week,
            "window_text": f"Weeks {from_week}–{to_week}",
        },
        "scenarios": [
            {"branch": "if_true", **({"label": if_true} if if_true else {}),
             "picks": [{"year": 2027, "round": 1, "from": "Topper", "to": "Mansfield"}]},
            {"branch": "if_false", **({"label": if_false} if if_false else {}),
             "picks": [{"year": 2027, "round": 2, "from": "Topper", "to": "Mansfield"}]},
        ],
    }


CASES = [
    # ---- the bread and butter ----
    dict(
        name="plain top-10",
        record=trade("If Tony Pollard finishes as a top 10 RB", subject="pollard"),
        expect=dict(settleable=True, kind="position_rank", operator="lte",
                    threshold=10, branch_ok=True),
    ),
    dict(
        name="mid-season window",
        record=trade("if Breece is a top 15 back from here on out", subject="hall",
                     from_week=8, to_week=15, player=("Breece Hall", "RB")),
        expect=dict(settleable=True, kind="position_rank", operator="lte", threshold=15),
    ),

    # ---- combined pools rank like any other ----
    dict(
        name="flex pool",
        record=trade("if Pollard finishes as a top 20 flex", subject="pollard"),
        expect=dict(settleable=True, kind="position_rank", position="FLEX",
                    operator="lte", threshold=20),
    ),
    dict(
        name="superflex pool",
        record=trade("if Pollard is a top 30 superflex guy", subject="pollard"),
        expect=dict(settleable=True, kind="position_rank", position="SUPERFLEX",
                    operator="lte", threshold=30),
    ),
    dict(
        name="position, not flex",
        record=trade("if Pollard finishes as a top 20 RB", subject="pollard"),
        # The mirror trap: don't widen a position condition into the flex pool.
        expect=dict(settleable=True, kind="position_rank", position="RB",
                    operator="lte", threshold=20),
    ),

    # ---- tier language that looks like a rank ----
    dict(
        name="RB2 tier, not rank 2",
        record=trade("if Pollard is an RB2 or better this year", subject="pollard"),
        # An "RB2" is the second starter on a 12-team roster: top 24, not rank 2.
        expect=dict(settleable=True, kind="position_rank", operator="lte", threshold=24),
    ),

    # ---- counting stats ----
    dict(
        name="rushing yards milestone",
        record=trade("If Jeanty runs for 1,000 yards", subject="jeanty",
                     player=("Ashton Jeanty", "RB")),
        expect=dict(settleable=True, kind="stat_total", stat="rushingYards",
                    operator="gte", threshold=1000),
    ),
    dict(
        name="more than, not at least",
        record=trade("if Nico Collins goes for more than 1,100 receiving yards", subject="collins",
                     player=("Nico Collins", "WR")),
        expect=dict(settleable=True, kind="stat_total", stat="receivingYards",
                    operator="gt", threshold=1100),
    ),

    # ---- basis disagreement: text says average, form says total ----
    dict(
        name="averages vs rank_by total",
        record=trade("if Gibbs averages at least 18 points a game", subject="gibbs",
                     rank_by="total", player=("Jahmyr Gibbs", "RB")),
        expect=dict(settleable=True, kind="fantasy_points", operator="gte",
                    threshold=18, basis="ppg", has_notes=True),
    ),

    # ---- availability ----
    dict(
        name="games played",
        record=trade("if Pollard plays at least 14 games", subject="pollard"),
        expect=dict(settleable=True, kind="games_played", operator="gte", threshold=14),
    ),

    # ---- compound ----
    dict(
        name="two clauses, and",
        record=trade("if Jeanty gets to 1,000 rushing yards AND finishes top 12 at RB",
                     subject="jeanty", player=("Ashton Jeanty", "RB")),
        expect=dict(settleable=True, combine="and", n_terms=2),
    ),

    # ---- the ones that must NOT be guessed: no number to check ----
    dict(
        name="vague",
        record=trade("if Pollard balls out", subject="pollard"),
        expect=dict(settleable=False),
    ),
    dict(
        name="scrimmage yards spans two stats",
        record=trade("if Gibbs puts up 1,500 yards from scrimmage", subject="gibbs",
                     player=("Jahmyr Gibbs", "RB")),
        # There is a derived stat for exactly this. The failure mode being
        # guarded against is quietly settling it on rushing yards alone.
        expect=dict(settleable=True, kind="stat_total", stat="scrimmageYards",
                    operator="gte", threshold=1500, not_single_stat="rushingYards"),
    ),

    # ---- legacy records: labels written backwards ----
    dict(
        name="inverted branch labels (legacy record)",
        record=trade("If Tony Pollard finishes as a top 10 RB", subject="pollard",
                     if_true="Pollard finishes RB11 or worse",
                     if_false="Pollard finishes RB1–RB10"),
        expect=dict(settleable=True, branch_ok=False),
    ),

    # ---- manager placement: about a team, not a player ----
    dict(
        name="makes the playoffs",
        record=trade("if Topper makes the playoffs"),
        # 10-team league, top 4 make it — the bracket size comes from site.json.
        expect=dict(settleable=True, kind="manager_placement",
                    measure="regular_season_rank", operator="lte", threshold=4,
                    manager_contains="Topper"),
    ),
    dict(
        name="wins the championship",
        record=trade("if Maggie wins the championship"),
        expect=dict(settleable=True, kind="manager_placement",
                    measure="final_rank", operator="lte", threshold=1),
    ),
    dict(
        name="reaches the final",
        record=trade("if Topper makes it to the championship game"),
        expect=dict(settleable=True, kind="manager_placement",
                    measure="final_rank", operator="lte", threshold=2),
    ),
    dict(
        name="finishes last",
        record=trade("if Maggie finishes dead last"),
        expect=dict(settleable=True, kind="manager_placement",
                    measure="final_rank", operator="gte", threshold=10),
    ),
    dict(
        name="win total",
        record=trade("if Topper wins at least 9 games"),
        expect=dict(settleable=True, kind="manager_placement",
                    measure="wins", operator="gte", threshold=9),
    ),
    dict(
        name="seed, not title",
        record=trade("if Topper gets a top 2 seed"),
        # The trap: a seed is regular_season_rank, not final_rank.
        expect=dict(settleable=True, kind="manager_placement",
                    measure="regular_season_rank", operator="lte", threshold=2),
    ),
    dict(
        name="player and placement together",
        record=trade("if Pollard is a top 20 RB and Topper misses the playoffs",
                     subject="pollard"),
        expect=dict(settleable=True, combine="and", n_terms=2),
    ),
    # ---- no tagged player: name must come out of the sentence ----
    dict(
        name="untagged player",
        record=trade("if Puka ends up a top 5 receiver", subject=None,
                     player=("Puka Nacua", "WR")),
        expect=dict(settleable=True, kind="position_rank", threshold=5,
                    player_contains="Nacua"),
    ),
]
