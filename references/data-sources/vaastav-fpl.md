# Data source: vaastav/Fantasy-Premier-League

## Provenance

| Field | Value |
|---|---|
| Source URL | https://github.com/vaastav/Fantasy-Premier-League |
| Commit SHA | `8c97b2adb123863c3dd581e730f1360e89815ac2` |
| Fetch timestamp (UTC) | 2026-08-13T07:21:07Z |
| Local path | `data/external/vaastav-fpl/` (gitignored, not committed) |
| Seasons pulled | 2016-17 through 2026-27 (full repo; 2026-27 has no completed gameweeks yet) |
| File hash manifest | `references/data-sources/vaastav-fpl-manifest-sha256.txt` (SHA-256 of every file actually read during M0 reconnaissance) |

A pinned commit SHA alone does not survive an upstream force-push. The manifest is what proves
the analysed bytes haven't changed — re-fetching and re-hashing would catch drift even if
history at that SHA gets rewritten.

## Reproduction command

```bash
git clone --depth 1 https://github.com/vaastav/Fantasy-Premier-League.git data/external/vaastav-fpl
git -C data/external/vaastav-fpl rev-parse HEAD   # should print the commit SHA above
```

Shallow clone (`--depth 1`) — only the current tip is needed, not full history.

## Reconnaissance notebook

`notebooks/xp-model/00_source_recon.ipynb` works through the questions below against the
actual files, not from memory. Open it in Jupyter or VS Code's notebook UI — kernel `fpl`,
registered with `uv run python -m ipykernel install --user --name fpl` after `uv sync` — and
run all cells. GitHub renders the saved outputs without needing to execute anything locally.

To re-execute and refresh the saved outputs:

```bash
PYTHONIOENCODING=utf-8 uv run jupyter nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.kernel_name=fpl notebooks/xp-model/00_source_recon.ipynb
```

(`PYTHONIOENCODING=utf-8` avoids a Windows console `cp1252` crash when printing accented
player names such as Ødegaard.)

## Findings (M0 questions 4-10)

### Q4 — Grain of `merged_gw.csv`

**Not** one row per `(season, player, GW)`. It is one row per `(season, player, fixture)`,
i.e. player-fixture grain — exactly the grain the spec targets. Every season has hundreds of
`(element, GW)` keys with 2-3 rows, all attributable to double gameweeks (a player's team
played twice in the same FPL gameweek). `(element, GW, fixture)` is the correct unique key.

| Season | Rows | Duplicate (element, GW) keys | Max rows per key |
|---|---|---|---|
| 2016-17 | 23,679 | 573 | 2 |
| 2017-18 | 22,467 | 670 | 2 |
| 2018-19 | 21,790 | 656 | 2 |
| 2019-20 | 22,560 | 247 | 2 |
| 2020-21 | 24,365 | 1,437 | 3 |
| 2021-22 | 25,447 | 2,217 | 2 |
| 2022-23 | 26,505 | 1,548 | 2 |
| 2023-24 | 29,725 | 983 | 2 |
| 2024-25 | 27,605 | 374 | 2 |
| 2025-26 | 29,757 | 419 | 2 |

**Blanks confirmed as absent rows, not zero rows.** Cross-referencing `fixtures.csv` (present
2018-19 onward) against `merged_gw.csv`: for every season, a sample team with zero fixtures in
a given gameweek has **zero rows** for any of its players in that gameweek's data — not rows
with `minutes=0`. E.g. 2018-19 GW31: team 1 had no fixture; all 32 of that team's players are
absent from GW31 rows entirely. Confirmed for all 8 seasons with `fixtures.csv` available.
`fixtures.csv` itself is absent for 2016-17 and 2017-18, so blanks/DGWs for those two seasons
are inferred only from the `(element, GW)` duplicate/gap pattern in `merged_gw.csv`, not
independently verified against a fixture list.

### Q5 — `kickoff_time`

Present and 100% populated in every season (2016-17 to 2025-26), zero nulls. Per-gameweek
kickoff windows never overlap out of order — zero cases found where a gameweek's earliest
kickoff precedes a prior gameweek's latest kickoff, across all 10 seasons. `GW` (i.e. `round`)
number is consistent with chronological order in this dataset. Walk-forward cutoffs should
still key on `kickoff_time` rather than `GW`, since `GW` does not disambiguate two fixtures
sharing the same gameweek number in a DGW.

### Q6 — Season × column presence / null rate

| Column | First season present |
|---|---|
| `expected_goals`, `expected_assists`, `expected_goals_conceded`, `starts` | 2022-23 |
| `clearances_blocks_interceptions`, `tackles`, `recoveries` (raw CBIT counts) | 2016-17, then **absent 2019-20 through 2024-25**, reappear 2025-26 |
| `defensive_contribution` (the scoring column) | 2025-26 only |
| `position` | 2020-21 |
| `xP` | 2020-21 |

CBIT raw counts are not continuously present: they exist in `merged_gw.csv` for 2016-17 to
2018-19, vanish for 2019-20 through 2024-25, then return alongside the new
`defensive_contribution` scoring column in 2025-26. "Defcon has one season of history" is true
for the *scoring* column; the underlying raw defensive counts have a gap, not a single
unbroken pre-history, in this file. A later `understat`/FBref backfill would need to cover
2019-20 to 2024-25 too, not just pre-2016-17. Where a column is "absent" above, it is a
missing column, not a null-filled one — nulls get inserted explicitly at ingestion rather than
left to whatever polars does with a ragged schema.

### Q7 — `xP` column

Present from 2020-21 onward, absent 2016-17 to 2019-20. The source repo's own
`DATA_DICTIONARY.md` documents the same caveat independently: `xP` is scraped from FPL's
`ep_this` field *after* the gameweek ends, with undocumented update timing, so it may leak
post-match information. Dropping the column outright rather than lagging it.

### Q8 — Id-mapping files

`data/<season>/player_idlist.csv` exists for every season but carries only
`first_name, second_name, id` — no `code` column and no team. `id` here is the season-scoped
`element`, not safe as a cross-season key (see Q9). `data/master_team_list.csv` (one file, all
seasons) has `season, team, team_name` and is the team crosswalk directly; no separate
per-season team file is needed. The stable cross-season player identity is `code`, in each
season's `players_raw.csv` — not anything in `player_idlist.csv`. `players_raw.csv` carries
both the season-scoped `id` and the stable `code` side by side, so the crosswalk is
`players_raw.csv[season].{id, code}` joined across seasons on `code`. `player_idlist.csv`,
despite the name, is not the file for this.

### Q9 — Position is per-season; reclassification rate

Position is a per-season attribute, and this dataset makes it easy to get the cross-season
comparison silently wrong: joining directly on raw `element` across all 10 seasons gives 620
matched rows with 100% "reclassified" — because `element` is reused across seasons for
different real players, not because Arsenal fielded 620 players who all changed position. That
result demonstrates why `element` can't be trusted across seasons; it isn't a finding about
position at all.

Joining on the stable `code` field instead: 35 players are present in `players_raw.csv` across
all 10 seasons, and 5 of them (14%) show a differing `element_type` in at least one season.
Position must be read per season, from `players_raw.csv.element_type` or
`merged_gw.csv.position` (present 2020-21+), keyed by `code`, never assumed stable across
seasons.

### Q10 — Set-piece and penalty duty

**Amendment needed to the spec.** §1.3 and §9 state this is absent from the dataset. That's
true for the player-fixture grain file (`merged_gw.csv` never has these columns, confirmed
above) — but **`players_raw.csv` has had `penalties_order`, `direct_freekicks_order`, and
`corners_and_indirect_freekicks_order` since 2020-21**, populated for roughly 7-14% of players
per season (the players who actually hold a set-piece role; everyone else is null). Example,
2025-26: Saka `penalties_order=1`, `direct_freekicks_order=2`; Ødegaard
`penalties_order=3`, `corners_and_indirect_freekicks_order=4`.

The caveat: `players_raw.csv` sits at `data/<season>/`, not inside `gws/` — it is a **current
snapshot** (whatever the FPL API returned when the scrape ran), not a per-gameweek history.
There is exactly one `players_raw.csv` per season, and nothing in the repo indicates it was
archived at each gameweek. So it tells you *today's* penalty order, useful as a live feature
for forward prediction, but there is no way to recover *historical, gameweek-by-gameweek*
set-piece duty from this source for backtesting — a player traded penalty duty mid-season would
show only the final-known state.

Proposed amendment: change §1.3 and known-issue §9.3 from "absent from the dataset" to "no
per-gameweek historical source; a current-snapshot-only field exists in `players_raw.csv` from
2020-21 onward, usable as a live feature only if refreshed from the live API at prediction
time — not reconstructable for walk-forward backtesting." This affects M4c (attacking returns)
and M5's `03-attacking-returns-features.ipynb`. It requires no M0/M1 code change.

## Known caveats carried into `references/known-issues.md`

- `players_raw.csv` set-piece fields are snapshot-only (see Q10 above) — refines known issue 3.
- CBIT raw-count columns have a 2019-20 to 2024-25 gap in `merged_gw.csv` even though they
  existed 2016-17 to 2018-19 — refines known issue 2.
- `fixtures.csv` is absent for 2016-17 and 2017-18, so blank/DGW detection for those two
  seasons relies on `merged_gw.csv` row patterns alone, not an independent fixture list.
