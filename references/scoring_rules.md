# Scoring rules

Generated from `fpl/scoring_rules.py` by `uv run python -m fpl.scoring_rules`.
Edit the module, not this file.

Read 2026-08-13 from:

- point values: https://fantasy.premierleague.com/api/bootstrap-static/ (game_config.scoring)
- defcon thresholds, divisors: https://fantasy.premierleague.com/help/rules
- 2026-27 BPS changes: https://fantasy.premierleague.com/help/new

The DefCon thresholds and the "per 2 conceded" / "per 3 saves" divisors are not in the API
config; they come from the rules page prose.

The API returns 2026-27 values only. Every earlier season below is a transcribed candidate.
M2's `fpl.dataset reconcile` scores each season's rows through this table and compares to the
recorded `total_points`; until that runs, the pre-2026-27 columns are unproved.

## Point values by season

| Rule | 2016-17 | 2017-18 | 2018-19 | 2019-20 | 2020-21 | 2021-22 | 2022-23 | 2023-24 | 2024-25 | 2025-26 | 2026-27 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Playing 1 to 59 minutes | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Playing 60 minutes or more | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| Goal scored (GK) | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 10 | 10 |
| Goal scored (DEF) | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| Goal scored (MID) | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 |
| Goal scored (FWD) | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 |
| Assist | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Clean sheet (GK) | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 |
| Clean sheet (DEF) | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 |
| Clean sheet (MID) | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Per 2 goals conceded (GK/DEF) | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 |
| Per 3 saves | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Penalty saved | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 |
| Penalty missed | -2 | -2 | -2 | -2 | -2 | -2 | -2 | -2 | -2 | -2 | -2 |
| Yellow card | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 |
| Red card | -3 | -3 | -3 | -3 | -3 | -3 | -3 | -3 | -3 | -3 | -3 |
| Own goal | -2 | -2 | -2 | -2 | -2 | -2 | -2 | -2 | -2 | -2 | -2 |
| Defensive contribution | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |
| DefCon threshold (DEF) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 10 | 10 |
| DefCon threshold (MID/FWD) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 12 | 12 |
| BPS version | pre-2025-26 | pre-2025-26 | pre-2025-26 | pre-2025-26 | pre-2025-26 | pre-2025-26 | pre-2025-26 | pre-2025-26 | pre-2025-26 | 2025-26 | 2026-27 |

Positions are per-season attributes. Manager entries (`AM`, 2024-25 only) score under a
separate rule set and have no entry in this table; scoring one raises.

## BPS versions

The public API omits `game_config.scoring.bps`, so the tables below hold only the per-action
values stated outright in the official rules and the 2026-27 patch notes. They establish that
the versions differ; they do not compute a BPS score.

| Version | Documented values |
|---|---|
| pre-2025-26 | `tackled` = -1 |
| 2025-26 | `cbi_per_bps` = 2, `penalties_saved` = 8, `tackled` = -1 |
| 2026-27 | `cbi_per_bps` = 3, `penalties_saved` = 7, `save_from_big_chance` = 1, `save_from_inside_box` = 1, `save_from_outside_box` = 0, `saves` = 2, `tackled` = 0 |

The 2026-27 patch notes state four changes from 2025-26: the -1 for being tackled is removed,
saving a penalty drops from 8 to 7 BPS, CBI earns 1 BPS per 3 rather than per 2, and saves are
restructured — 2 BPS per save, +1 from inside the box, +1 from a Big Chance, with saves from
outside the box dropped. The same notes date the inside/outside-box save metrics to 2025-26,
which is why 2024-25 and earlier are a third version.

No official source was checked for BPS changes *within* 2016-17 to 2024-25. Those seasons share
a version label by assumption, not by evidence.
