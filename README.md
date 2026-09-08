# Life Alert Fantasy — Commissioner Tools

Administrative tooling for the dynasty league (ESPN league `1988439171`, first season 2026).

**ESPN is read-only here.** Nothing writes to ESPN. It is an oracle for three things: canonical
manager identities, the NFL player universe, and — at season's end — the stats that settle
conditional trades. The ledger is the league's own source of truth; ESPN has no concept of
future dynasty picks or conditional clauses.

## How it fits together

```
  GitHub Pages                Cloudflare Worker            Cloudflare D1
  static form      ──POST──>  password check      ──────>  trades table
  (league mates)              + validation                 (the ledger)
                                                                │
                                                                │ read at season's end
                                                                v
                                                      local Python + Claude
                                                      settle conditions, write
                                                      the action log
```

League mates need no accounts of any kind — just the URL and the league password. Claude only
appears at the end, running locally on the commissioner's machine.

## Layout

```
config/managers.json   generated — 10 teams, canonical names + team ids
config/players.json    generated — 1,036 tradeable players, ownership-ranked
config/site.json       hand-edited — endpoint URL and league rules
form/                  the form template + the Artifact build
docs/                  the GitHub Pages build (index.html, robots.txt)
worker/                the Cloudflare Worker + D1 schema
scripts/               data fetch and form build
```

## Setup

### 1. League data

Credentials are shared with the sibling `FantasyWork/FantasyAgent` project (same ESPN account),
read from its `.env`. Drop a local `.env` here to override.

```bash
PY=../FantasyWork/FantasyAgent/venv/bin/python
$PY scripts/fetch_league_data.py   # refresh managers + players from ESPN
```

Re-run when a manager claims a team or the player pool goes stale.

### 2. The backend (once)

Wrangler is a **local** dev dependency, not a global install — that keeps its version pinned
with the repo and means npm never needs `sudo`.

```bash
cd worker
npm install                                 # installs wrangler into worker/node_modules
npx wrangler login

npx wrangler d1 create life-alert-ledger    # paste the database_id into wrangler.toml
npm run schema                              # applies schema.sql to the remote D1
npx wrangler secret put LEAGUE_PASSWORD     # the password you give the league
npm run deploy                              # prints the worker URL
```

Later, `npm run trades` dumps the most recent filed trades straight from D1.

**Requires Node 22+.** If `node -v` is older, install a current Node first — `nvm` keeps it in
your home directory and avoids the root-owned `/usr/local` that the official Node .pkg installer
creates:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
exec $SHELL -l
nvm install 22 && nvm alias default 22
```

If npm fails with `EACCES` on `~/.npm`, a past `sudo npm install` left root-owned files in your
cache. Reclaim them once: `sudo chown -R $(whoami) ~/.npm`.

Put that worker URL into `config/site.json` as `endpoint`.

**Live endpoint:** `https://life-alert-ledger.laf-conditional.workers.dev`
(`/health` is public; everything else needs the `X-League-Password` header.)

Free tier covers this many times over: 100k worker requests/day and 100k D1 row writes/day
against a league that files maybe fifty trades a season.

### 3. Build and publish the form

```bash
$PY scripts/build_form.py    # writes form/trade_form.html and docs/index.html
```

Then push and turn on Pages:

```bash
git remote add origin https://github.com/<you>/life-alert-ledger.git
git push -u origin main
```

HTTPS rather than SSH, since it needs no key setup. GitHub stopped accepting account
passwords for git in 2021, so authenticate once with the GitHub CLI and the macOS keychain
remembers it:

```bash
brew install gh && gh auth login      # choose GitHub.com -> HTTPS -> login with a browser
```

(If origin already exists and points somewhere wrong, `git remote add` won't overwrite it —
use `git remote set-url origin <url>`.)

On GitHub: **Settings → Pages → Source: Deploy from a branch → `main` / `docs`**. The form lands
at `https://<you>.github.io/life-alert-ledger/`. Every later `git push` redeploys it — no CI to
configure.

`robots.txt` and a `noindex` meta keep it out of search results.

**The repo has to be public** for Pages on a free account, so nothing sensitive goes in it. The
league password lives only as a Cloudflare secret, and **ESPN owner ids are never written to disk
at all**. That field is the same value as a manager's SWID cookie — a permanent per-account
identifier nobody can rotate — so `fetch_league_data.py` resolves it to the `claimed` boolean it
was only ever consulted for and drops it. `build_form.py` still strips the field as a backstop.
The worker URL is in the page source by necessity — that's why the password check is server-side.

## The form

Managers pick from a dropdown and players from an autocomplete, so every filed trade maps to a
trackable identity rather than a typed-in name. Picks are entered as year × round × origin
(`2028 1st via Topper`) — origin matters because a pick's eventual slot depends on whose it
originally was.

### Conditional trades

A condition is captured in three parts, because all three are needed to settle it without an
argument:

| Part | Field | Why |
|---|---|---|
| What counts | free text, plain English | "top-10 RB", "1,000 yards", "Topper makes the playoffs" — an agent reads this |
| Ranked by | total points or points per game | a part-season leader by average is rarely the leader by total |
| Which weeks | from-week through to-week | handles "from that point forward" after an injury, and whether playoffs count |

Scoring is always the league's full PPR — it isn't a per-trade choice, since that's simply what
the league is. Records store it explicitly (`"scoring": "ppr"`) so a future scoring change can't
retroactively reinterpret an old condition.

The week range is what makes mid-season conditions work: a backup who takes over in Week 8 gets
measured Weeks 8–15, not from Week 1. Setting the end week to 17 includes the playoffs.

The condition is written **once**, in the free-text box. The branches below it only collect the
picks that move each way — they don't ask you to restate the condition, which is what used to send
managers looking for the difference between two boxes that wanted the same sentence.

A condition can hang on a **manager's season** rather than a player — *"if Topper makes the
playoffs"*, *"if Maggie wins it all"*, *"if he finishes dead last"* — and settles off the league's
own standings.

Alongside the box is phrasing guidance, drawn from what actually broke in testing: say *top 20 RB*
rather than *an RB2* (half the league reads that as top 12 and half as top 24), and *top 2 RB*
rather than *the RB2*. Combined pools work too — *top 20 flex* ranks RB/WR/TE together and
*top 30 superflex* adds QBs, matching the league's two flex slots and its superflex. A condition
that names no number — "balls out", "stays healthy" — can still be filed; it just lands on the
commissioner instead of settling itself.

**Conditional picks live only in the condition.** The trade section is for what changes hands no
matter what. A pick listed in both places can't be settled — nobody can tell whether it moves once
or twice — so the form refuses to file it.

### League rules encoded in the form

Edit `config/site.json` and rebuild:

| Rule | Value |
|---|---|
| Rookie draft rounds | 4 |
| Picks tradeable | 3 years out (2027–2029) |
| Regular season | weeks 1–15 |
| Playoffs | weeks 16–17, 4 teams |
| Teams | 10 (`teams`, `playoff_teams` — the settlement agent reads these) |
| League scoring | full PPR (1.0/reception) — assumed by all conditions |
| Trade deadline | 2026-12-16 |

2026 picks are deliberately absent — that's the startup draft, not a rookie draft. Set
`pick_years_out` differently or add the season year if you want them tradeable.

## The league password

Stored in the macOS Keychain, not in a file:

```bash
security add-generic-password -a "$USER" -s life-alert-ledger -w      # prompts; add -U to change
```

`-w` with no value prompts for it, so the password never enters shell history. Once it's there:

```bash
PY=../FantasyWork/FantasyAgent/venv/bin/python
$PY scripts/ledger.py health     # is the worker up?
$PY scripts/ledger.py list       # every filed trade, newest first
$PY scripts/ledger.py pending    # conditional trades still awaiting settlement
$PY scripts/ledger.py raw        # JSON, for piping
```

`scripts/league_secrets.py` resolves it from `$LEAGUE_PASSWORD`, then the Keychain, then a local
`.env`. Nothing prints the secret — scripts read it, sign the request, and print only the
response, so it stays out of transcripts and terminal scrollback.

## Security

The league password is checked **inside the worker**, so a wrong password writes nothing and the
password never ships in the page source. The form's login screen is convenience on top of that
check. This keeps the write endpoint from being an open door for anything that crawls the URL;
it is not meant to withstand a determined attacker, and doesn't need to.

## Settlement

The agent that decides whether a condition hit lives in `scripts/settle/`. Its one design rule:
**the model interprets, code decides.** Claude turns "if Pollard is a top-10 RB" into a typed
predicate and never sees a stat line; deterministic Python runs that predicate against ESPN and
returns the verdict with its arithmetic attached — because the output moves a dynasty first and
has to survive being argued with.

```bash
PY=../FantasyWork/FantasyAgent/venv/bin/python
$PY -m scripts.settle.run_tests --evaluate    # 14 cases against a real completed season
```

See [SETTLEMENT.md](SETTLEMENT.md) for the design and what's deliberately left to the
commissioner. Still to come: `settle.py`, which walks the pending trades and writes
`resolution_json` back — waiting on real filed trades and a finished season.
