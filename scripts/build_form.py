"""
Inject live league data into the trade form and write docs/index.html.

Usage:  python scripts/build_form.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<style>
  html { color-scheme: light dark; }
  body { margin: 0; font: 14px system-ui, sans-serif; }
  img { max-width: 100%; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
"""
FOOT = "\n</body>\n</html>\n"


def main() -> None:
    cfg = json.loads((ROOT / "config" / "site.json").read_text())
    managers = json.loads((ROOT / "config" / "managers.json").read_text())
    players = json.loads((ROOT / "config" / "players.json").read_text())

    season = cfg["season"]
    pick_years = [season + n for n in range(1, cfg["pick_years_out"] + 1)]

    # `owned` only drove the fetch-time sort; the array order already encodes it.
    slim = [{"id": p["id"], "name": p["name"], "pos": p["pos"], "team": p["team"]} for p in players]

    # espn_owner_id is a manager's SWID cookie and this repo is public. fetch_league_data
    # no longer writes the field; stripping it here is the backstop.
    public_managers = [{k: v for k, v in m.items() if k != "espn_owner_id"} for m in managers]

    html = (ROOT / "form" / "trade_form.template.html").read_text()
    subs = {
        "__MANAGERS_JSON__": json.dumps(public_managers, separators=(",", ":")),
        "__PLAYERS_JSON__": json.dumps(slim, separators=(",", ":")),
        "__PICK_YEARS__": json.dumps(pick_years),
        "__SEASON__": str(season),
        "__ENDPOINT__": json.dumps(cfg.get("endpoint")),
        "__REG_WEEKS__": str(cfg["regular_season_weeks"]),
        "__MAX_WEEK__": str(cfg["max_week"]),
    }
    for token, value in subs.items():
        if token not in html:
            raise SystemExit(f"Template is missing placeholder {token}")
        html = html.replace(token, value)

    site = ROOT / "docs"
    site.mkdir(exist_ok=True)
    page = site / "index.html"
    page.write_text(HEAD + html + FOOT)
    (site / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (site / ".nojekyll").write_text("")

    print(f"built docs/index.html ({page.stat().st_size / 1024:.0f} KB)")
    print(f"  managers   : {sum(m['claimed'] for m in managers)} claimed of {len(managers)}")
    print(f"  players    : {len(slim)}")
    print(f"  pick years : {pick_years} x rounds 1-{cfg['rookie_draft_rounds']}")
    print(f"  weeks      : regular season 1-{cfg['regular_season_weeks']}, playoffs to {cfg['max_week']}")
    print(f"  endpoint   : {cfg.get('endpoint') or 'NOT SET — form runs in copy-only mode'}")


if __name__ == "__main__":
    main()
