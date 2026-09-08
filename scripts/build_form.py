"""
Inject live league data into the trade form and emit both builds.

  form/trade_form.html   the Artifact body (Claude wraps it in a document)
  docs/index.html        the standalone page for GitHub Pages

Both are the same markup and behaviour; only the document wrapper differs.

Usage:  python scripts/build_form.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The reset the Artifact host applies for us, restated for the standalone page
# so the two builds render identically.
SITE_HEAD = """<!doctype html>
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
SITE_FOOT = "\n</body>\n</html>\n"


def main() -> None:
    cfg = json.loads((ROOT / "config" / "site.json").read_text())
    managers = json.loads((ROOT / "config" / "managers.json").read_text())
    players = json.loads((ROOT / "config" / "players.json").read_text())

    season = cfg["season"]
    pick_years = [season + n for n in range(1, cfg["pick_years_out"] + 1)]

    # `owned` only drove the fetch-time sort; the array order already encodes it.
    slim = [{"id": p["id"], "name": p["name"], "pos": p["pos"], "team": p["team"]} for p in players]

    # The page never needs ESPN owner GUIDs, and the Pages repo is public — leave
    # them in config/managers.json for local tooling, but don't ship them.
    public_managers = [
        # Kept as a backstop: fetch_league_data no longer writes owner ids, and
        # nothing that reaches a public page should ever carry one again.
        {k: v for k, v in m.items() if k != "espn_owner_id"} for m in managers
    ]

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

    artifact = ROOT / "form" / "trade_form.html"
    artifact.write_text(html)

    site_dir = ROOT / "docs"
    site_dir.mkdir(exist_ok=True)
    (site_dir / "index.html").write_text(SITE_HEAD + html + SITE_FOOT)
    (site_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (site_dir / ".nojekyll").write_text("")

    endpoint = cfg.get("endpoint")
    print(f"built form/trade_form.html and docs/index.html ({artifact.stat().st_size / 1024:.0f} KB)")
    print(f"  managers   : {sum(m['claimed'] for m in managers)} claimed of {len(managers)}")
    print(f"  players    : {len(slim)}")
    print(f"  pick years : {pick_years} x rounds 1-{cfg['rookie_draft_rounds']}")
    print(f"  weeks      : regular season 1-{cfg['regular_season_weeks']}, playoffs to {cfg['max_week']}")
    print(f"  endpoint   : {endpoint or 'NOT SET — form runs in copy-only mode'}")


if __name__ == "__main__":
    main()
