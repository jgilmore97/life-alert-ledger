# Settling conditional trades

A conditional trade is a sentence a manager typed in September and a draft pick that changes
hands in December. Something has to connect the two.

## The one design decision

**The model interprets. Code decides.**

Claude never sees a stat line. It reads the condition and produces a *predicate* — a typed,
checkable clause. Deterministic Python then runs that predicate against ESPN's numbers and
returns a verdict with the arithmetic attached.

```
  filed trade                                            settlement
  (natural language)                                     (a pick moves)
        │                                                      ▲
        v                                                      │
   ┌─────────────┐        ┌──────────────┐       ┌──────────────────┐
   │  interpret  │ ─────> │   evaluate   │ ────> │     explain      │
   │   Claude    │  pred  │  pure Python │ facts │     Claude       │
   └─────────────┘        └──────────────┘       └──────────────────┘
   "what counts"          "what happened"        "how to say it"
   interpret.py           predicates.py          interpret.py
                          stats.py
```

The simpler design — hand Claude the condition and the stats, ask "did it happen" — loses the
thing that matters. This output moves a dynasty first-round pick, so when Topper says *"no way
he was RB10"* the answer has to be a table he can read, reproducible in December and again in
March. Splitting the job isolates the judgement call ("does *RB2* mean rank 2 or the second
tier?") from the arithmetic, which then cannot hallucinate. It also means the model can't be
talked into a verdict by a stat line it's staring at.

## What the model is not allowed to decide

The form captured these as structured fields, so they're facts, not readings:

| | |
|---|---|
| the week window | managers picked it explicitly; the agent reads it, never changes it |
| the scoring | full PPR, always |
| the ranking basis | total points or per-game, copied from the trade's `rank_by` |

If the condition text disagrees with the window ("this year" on a Weeks 8–15 trade), the window
wins and the agent says so in its notes. That keeps interpretation to a single question: *what
counts as the condition being met?*

## Ranking pools

A "top 20 flex" finish is the same ranking over more positions. `position` on a `position_rank`
term takes either one position or a composite pool:

| | |
|---|---|
| `FLEX` | RB / WR / TE |
| `SUPERFLEX` | QB / RB / WR / TE |
| `REC_FLEX` | WR / TE |

These are ESPN's own composite lineup slots, and this league starts two flexes plus a superflex
(slot 23 ×2, slot 7 ×1), so both are ordinary conditions rather than edge cases.

They are genuinely different conditions: over 2025, Tony Pollard was **RB23** — inside the RB2
tier — and **60th of 589 in the flex pool**. A trade riding on "top 24 RB" pays out; the same
trade written "top 24 flex" does not.

A player who isn't eligible for the pool he's ranked in (a QB in FLEX) fails loudly rather than
silently missing the board.

## Conditions on a manager, not a player

"If Topper makes the playoffs" hangs on how a *team* finished, and the league's own standings
settle it as cleanly as a stat line — so `manager_placement` is a first-class predicate kind.

It turns on one distinction the model is drilled on:

| | |
|---|---|
| `regular_season_rank` | the seed going **into** the bracket — what "made the playoffs" means |
| `final_rank` | where they came **out** of it — what "won it all" means |

They diverge exactly where it matters: the 4-seed who wins the title is `regular_season_rank` 4
and `final_rank` 1. Treating those as one number with different thresholds settles half these
conditions wrong.

Placement terms are deliberately **window-free**. The trade's week range scopes a player's
production; a season's standings are a season's standings, and the evaluator ignores the window
rather than pretending it applies.

The bracket size comes from `config/site.json` (`teams`, `playoff_teams`) and is handed to the
model in its brief, so "makes the playoffs" becomes `lte 4` without the model having to know the
league by heart. ESPN reports it too, but a league with no playoff format configured reports `0`
— hence the override.

## Saying "I can't settle this"

`settleable: false` is a first-class outcome, not a failure. Everything checkable comes from two
places — this league's scoring and its own standings — and a condition that names no number
("if he balls out", "stays healthy") isn't checkable from either. A plausible guess costs
someone a first-rounder, so those route to the commissioner with a note on what's missing.

The guidance deliberately doesn't enumerate exotic cases the league will never trade on. Naming
them taught the model to look for them; the rule that matters is simply *does it name a number*.

The agent also runs a **branch check**. The form used to ask managers to label each branch in
their own words, and some wrote the `if_true` label as the branch where the condition *fails*.
The form no longer asks — the branches are structural — but records filed under the old form
still exist, and the agent flags an inverted label rather than settling it backwards.

## The stats layer

ESPN detail that shapes everything: `kona_player_info` returns weekly stat splits only for the
`scoringPeriodId` you ask for. There's no "whole season by week" call, so an N-week window costs
N requests. They're cached under `data/stats_cache/` — settlement gets re-run while people
argue, and the cache is the record of what the numbers were on settlement day.

Points come from `appliedTotal` on actual (`statSourceId 0`) weekly (`statSplitTypeId 1`)
entries, requested through the **league** endpoint so they're already in the league's own
scoring. Combined totals that league-speak asks for constantly — scrimmage yards, total
touchdowns — are derived in `stats.py` and are first-class stats, because splitting them isn't
equivalent: 1,100 rushing + 450 receiving clears 1,500 scrimmage yards while failing any fixed
per-stat pair.

## Testing it

```bash
PY=../FantasyWork/FantasyAgent/venv/bin/python
$PY -m scripts.settle.run_tests              # interpretation only
$PY -m scripts.settle.run_tests --evaluate   # + real 2025 numbers
$PY -m scripts.settle.run_tests --case "RB2" # just one
```

`cases.py` holds the phrasings that quietly settle *wrong*, not the easy ones: tier language
that looks like a rank, a stat that spans two stats, an average where the form says total,
labels written backwards, and conditions no box score can settle. Every case asserts on the
**interpretation** — the model is what's under test.

The evaluator is checked separately and without trusting anyone's memory of the season: a
player's rank on the leaderboard must agree with what a top-N clause settles, at every N.
Off-by-ones and pool-filtering bugs show up there.

Test data is the sibling league's completed 2025 season (the dynasty league didn't exist yet).
Both leagues are full PPR, so the numbers behave identically.

**Current state: 22 interpretation cases, evaluator self-checks clean.**

The case that earns its keep — same player, two readings of ordinary league-speak, opposite
outcomes:

```
'if Pollard is an RB2 or better'  -> top 24 -> RB23 -> MET
'if Pollard is a top 10 RB'       -> top 10 -> RB23 -> NOT MET
```

Standings conditions have the same shape of trap — *"a top 2 seed"* is `regular_season_rank`,
*"makes the championship game"* is `final_rank`, and the two are different numbers for the same
team. So does the pool: *"top 20 RB"* must stay RB and not widen into the flex pool.

## Not built yet

`settle.py` — read `status = 'pending_condition'` from the ledger, run each through interpret →
evaluate → explain, write `resolution_json` back. Waiting on real filed trades and a finished
season.

Worth doing before then: run `interpret` at **filing** time and show the manager how the agent
read their condition, while they're still talking to each other. An ambiguity caught in
September is a conversation; the same one in December is an argument.
