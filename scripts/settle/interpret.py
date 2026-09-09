"""
The model half: league-speak in, a checkable Predicate out.

One call, structured output, no tools and no agent loop — interpreting a sentence is an
extraction task, not an agentic one.

Two things are withheld from the model: the week window and scoring basis (the form
captured them as structured fields, so the model reads but cannot change them), and the
numbers (it never sees a stat line, so it can't be talked into a verdict by one).

Its one real judgement call is `settleable`. "If Pollard balls out" is a fine handshake
and a terrible settlement input; handing it to the commissioner is the right answer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic

from .predicates import Interpretation, STAT_CHOICES

MODEL = "claude-opus-5"

ROOT = Path(__file__).resolve().parent.parent.parent


def league_shape() -> dict:
    """Team count and bracket size, which "makes the playoffs" needs to become a number.
    Read from config/site.json so the form and the agent can't drift."""
    cfg = json.loads((ROOT / "config" / "site.json").read_text())
    return {"teams": cfg.get("teams", 10), "playoff_teams": cfg.get("playoff_teams", 4)}


SYSTEM = f"""You settle conditional trades for a 10-team dynasty fantasy football league.

A manager wrote a condition in plain English — the way they'd say it out loud, not
the way a database wants it. Your job is to turn that sentence into a precise,
checkable clause. You do NOT decide whether it happened. Code does that against
ESPN's numbers. You decide what "it" is.

WHAT IS ALREADY DECIDED, AND NOT YOURS TO CHANGE
  * The week window. The form made the managers pick it explicitly. Never widen,
    narrow, or second-guess it, even if the condition text implies something else
    — if the text and the window disagree, say so in `notes` and use the window.
  * The scoring. The league is full PPR, always.
  * The ranking basis. Copy the trade's `rank_by` field into every term's `basis`.

HOW LEAGUE-SPEAK MAPS TO TERMS
  "top-10 RB", "finishes as a top 12 wideout", "RB1 or better"
      -> position_rank, operator lte, threshold = the number.
      "RB2" as a tier means top-24 at the position, NOT rank 2. "Top-5 at his
      position" uses the position he actually plays.

      A flex condition ranks across the combined pool, which is the same ranking
      over more positions: "top 20 flex" is position FLEX (RB/WR/TE), and
      "superflex" is SUPERFLEX (QB/RB/WR/TE). This league starts two flexes and
      a superflex, so both are ordinary conditions. Only set the pool the
      manager actually said — "top 20 RB" is RB, not FLEX.
  "scores 200 points", "averages 15 a game"
      -> fantasy_points. Note "averages" implies basis ppg even if rank_by says
      total; flag that disagreement in `notes` rather than silently overriding.
  "1,000 rushing yards", "10 total touchdowns", "80 catches"
      -> stat_total, with `stat` from: {', '.join(STAT_CHOICES)}
      Combined totals have their own stats — "yards from scrimmage" is
      scrimmageYards, "total touchdowns" is totalTouchdowns. Use those rather
      than splitting, because a split is not equivalent: 1,100 rushing plus 450
      receiving clears 1,500 scrimmage yards but fails any fixed per-stat pair.
      For a combination with no derived stat, split into terms with combine
      "and"/"or" only if each half is separately checkable; otherwise mark it
      unsettleable. Never silently drop half of a two-stat condition.
  "plays at least 14 games", "stays healthy"
      -> games_played for the first. "Stays healthy" alone is too vague unless
      the manager gave it a number.

  MANAGER PLACEMENT — the condition is about a TEAM's finish, not a player.
      "if Topper makes the playoffs", "if I win the whole thing", "if his team
      finishes last", "if she wins 8 games"
      -> manager_placement. Put the manager in `manager_name`, leave
      `player_name` an empty string, and pick the right `measure`:

        MAKING the playoffs -> regular_season_rank, lte the bracket size.
            The league's size and bracket are given below; use those numbers.
        Winning the title   -> final_rank, lte 1.
        Reaching the final  -> final_rank, lte 2.
        A bye               -> regular_season_rank, lte half the bracket.
        Finishing last      -> final_rank, gte the number of teams.
        "Top 3 seed"        -> regular_season_rank, lte 3.
        A win count         -> wins.

      The distinction that matters: regular_season_rank is the seed going INTO
      the bracket, final_rank is where they came out of it. A 4-seed who wins
      the title is regular_season_rank 4 and final_rank 1, so "made the
      playoffs" and "won it all" are different measures, not different
      thresholds on one. Placement is a whole-season fact — the trade's week
      window scopes a player's production and has no bearing on standings, so
      ignore it here and don't mention it as a caveat.

NOT SETTLEABLE — say so, don't approximate
  Everything settleable comes from two places: this league's scoring and its own
  standings. If a condition needs anything else, or names no number you could
  check it against, set settleable false and write what's missing.

  In practice that is conditions with no threshold in them — "balls out",
  "stays healthy", "is a stud". Say what number would make it checkable; usually
  the manager just needs to name one.

  This is a good outcome, not a failure. A wrong guess costs someone a
  first-round pick.

THE BRANCH CHECK
  `if_true` is the branch that happens when the condition HOLDS.

  Current records carry no branch labels — the form defines the branches
  structurally, so there is nothing to get backwards. Set branch_ok true.

  Older records were filed when managers hand-wrote a label on each branch, and
  some wrote them backwards. When a label IS present and `if_true`'s label
  describes the condition FAILING, set branch_ok false.

Be exact about thresholds. "Top 10" is lte 10. "More than 1,000 yards" is gt 1000;
"1,000 yards" as a milestone is gte 1000. When a phrase is genuinely ambiguous,
pick the reading a league would settle on, drop your confidence, and say so."""


def _brief(record: dict, league: dict | None = None) -> str:
    """The trade, flattened to the parts that bear on interpretation."""
    cond = record.get("condition") or {}

    def side(s):
        bits = [f"{p['name']} ({p.get('pos','?')})" for p in s.get("players", [])]
        bits += [f"{p['year']} round {p['round']} pick" for p in s.get("picks", [])]
        return ", ".join(bits) or "nothing"

    shape = league or league_shape()
    lines = [
        f"League: {shape['teams']} teams, top {shape['playoff_teams']} make the playoffs.",
        "",
        f"Trade: {record['side_a']['manager']} sends {side(record['side_a'])}",
        f"       {record['side_b']['manager']} sends {side(record['side_b'])}",
        "",
        f"CONDITION AS WRITTEN: {cond.get('text','')!r}",
        "",
        f"Measured over weeks {cond.get('from_week')}–{cond.get('to_week')} "
        f"({cond.get('window_text','')})",
        f"Ranked by: {cond.get('rank_by')}   Scoring: {cond.get('scoring')}",
    ]
    subject = cond.get("subject_player")
    if subject:
        lines.append(f"Player the managers tagged: {subject['name']} ({subject.get('pos')}, "
                     f"{subject.get('team')})")
    else:
        lines.append("Player the managers tagged: none — read the subject out of the text. "
                     "A condition about a manager's finish won't have one.")

    lines.append("")
    for s in record.get("scenarios", []):
        picks = ", ".join(f"{p['year']} round {p['round']}" for p in s.get("picks", [])) or "nothing"
        # Labels only exist on records filed before the form dropped them. When
        # they're absent the branch is defined structurally and can't be inverted.
        label = s.get("label")
        lines.append(f"  {s['branch']}: {picks} moves"
                     + (f"   [manager's label: {label!r}]" if label else ""))
    return "\n".join(lines)


def interpret(record: dict, client: anthropic.Anthropic | None = None,
              model: str = MODEL, league: dict | None = None) -> Interpretation:
    """One conditional trade record -> one Interpretation."""
    client = client or anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.parse(
        model=model,
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": _brief(record, league)}],
        output_format=Interpretation,
    )
    return response.parsed_output


def explain(record: dict, interp: Interpretation, verdict, *,
            client: anthropic.Anthropic | None = None, model: str = MODEL) -> str:
    """Turn a settled verdict into the note that goes out to the league. Called only
    after the arithmetic is done and handed that arithmetic, so it writes up a result
    rather than deciding one."""
    client = client or anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    cond = record.get("condition") or {}
    branch = "if_true" if verdict.met else "if_false"
    scenario = next((s for s in record.get("scenarios", []) if s["branch"] == branch), {})
    picks = ", ".join(f"{p['year']} round {p['round']}"
                      for p in scenario.get("picks", [])) or "nothing"

    # A standings condition isn't measured over weeks, so a window line would be noise.
    over_weeks = any(r.term.kind != "manager_placement" for r in verdict.terms)
    facts = "\n".join(
        [f"Condition: {cond.get('text')}"]
        + ([f"Window: weeks {cond.get('from_week')}–{cond.get('to_week')}, "
            f"{cond.get('rank_by')}, full PPR"] if over_weeks else
           ["Measured on the final league standings, not a week range."])
        + [f"Result: the condition was {'MET' if verdict.met else 'NOT met'}",
           ""]
        + [f"- {r.detail}" for r in verdict.terms]
        + ["", f"So the {branch} branch applies: {picks} changes hands."]
    )
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        system=("Write the settlement note a fantasy commissioner posts to the league chat. "
                "Three or four sentences. State what was measured, the number it came to, and "
                "which pick moves. Plain and factual — the managers will check your arithmetic, "
                "and one of them just lost a pick. No hype, no emoji, no restating the rules."),
        messages=[{"role": "user", "content": facts}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()
