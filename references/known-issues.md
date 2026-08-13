# Known issues

Defects and limitations carried knowingly. Written by hand; the counts come from `reports/xp-model/m2-ingestion.md` and `reports/xp-model/m2-reconciliation.md`.

## Source data

### 1. `xP` is dropped rather than lagged

The source repo's own `DATA_DICTIONARY.md` documents `xP` as scraped from FPL's `ep_this` field after the gameweek ends, with undocumented update timing, so it may carry post-match information. It is absent from the processed frame entirely, which is stronger than lagging it: a lag would still let it into a feature by accident.

### 2. DefCon has one season of scoring history and a gap in its inputs

`defensive_contribution` exists in `merged_gw.csv` for 2025-26 only. The raw CBIT counts behind it are present for 2016-17 to 2018-19, absent 2019-20 to 2024-25, and return in 2025-26. So the submodel has no out-of-sample season, and a later backfill from another provider would need to cover 2019-20 to 2024-25, not only the years before 2016-17.

The 2016-19 counts are worthless for player-level rates, since essentially no current player appears in them. They are usable for fixing the count distribution family and for role-level priors, both of which are indifferent to player identity.

### 3. Set-piece and penalty duty is snapshot-only

`players_raw.csv` has carried `penalties_order`, `direct_freekicks_order` and `corners_and_indirect_freekicks_order` since 2020-21, populated for the 7-14% of players who hold a set-piece role. There is one such file per season and nothing indicates it was archived per gameweek, so it records whoever held the duty when the scrape ran. Using it for a 2025-26 backtest row would tell a GW3 model who took penalties in GW38. No set-piece column is carried into the processed frame, so it cannot be picked up by accident. It is a decision-time manual adjustment instead.

### 4. Ten rows are repeated verbatim in 2025-26

Junior Kroupi (element 100) in GW1 to GW9 and Ben Gannon-Doak (element 391) in GW1 each appear twice, same fixture id, every column identical. M0 counted these as double gameweeks because it measured rows per `(element, GW)` without checking whether the fixture ids differed. Ingestion drops rows repeated across every column, so the acceptance figures for 2025-26 are 29,747 rows and 409 duplicate `(element, GW)` keys, amended from M0's 29,757 and 419. No other season contains a repeated row.

### 5. `master_team_list.csv` stops at 2023-24

Despite being one file covering all seasons, it has no rows for 2024-25 or 2025-26. Those two seasons take club names from `data/<season>/teams.csv` instead, which exists from 2019-20. The two files agree on every name in every overlapping season.

### 6. 2021-22 GW37 spells goalkeeper `GKP`

That one gameweek labels 101 goalkeeper rows `GKP` where every other gameweek in every season says `GK`. Ingestion normalises it. Left alone it would silently split goalkeepers into two positions for one week.

### 7. The first three seasons are not valid UTF-8

`merged_gw.csv` for 2016-17 to 2018-19 contains bytes that fail strict UTF-8 decoding, in the player-name column. Those files are read with lossy decoding. `name` is not in the column contract, so nothing carried downstream is affected.

### 8. `fixtures.csv` is absent for 2016-17 and 2017-18

Blank and double gameweek detection for those two seasons rests on `merged_gw.csv` row patterns alone, with no independent fixture list to check against. The player's own team for those seasons comes from the `players_raw.csv` snapshot rather than from the fixture's home and away sides. Both seasons sit outside the default training window.

## Identity and position

### 9. `element` and team ids are not stable across seasons

Recorded verbatim from M0 as the cautionary example. Joining Arsenal players on raw `element` across ten seasons returns 620 matched rows, 100% of them apparently reclassified, because`element` is reused for different real players. Joining on `code` gives 35 players present in all ten seasons, 5 of them (14%) with a differing `element_type`. The first number is an artifact of the join, not a finding about position. Both `element` and `code` are carried; every cross-season join keys on `code`.

### 10. Position for 2016-17 to 2019-20 comes from an end-of-season snapshot

`merged_gw.position` starts in 2020-21. Earlier seasons read position from `players_raw.element_type`, which is the same snapshot file that makes the set-piece fields unusable, so a backtest row for those seasons knows the position the player finished the season with. This is the same class of leak as issue 3, at much lower severity, and only for seasons outside the default training window.

## Scoring rules

### 11. A goalkeeper goal scored 6 points, not 10

Reconciliation against `total_points` found the rules table wrong here. Alisson's header in 2020-21 GW36 scored 6 for the goal, where the current API config gives goalkeepers 10. It is the only goalkeeper goal in ten seasons, so the data dates the change to somewhere between 2020-21 and 2026-27 and no further, and no official source for the changeover season was found. The table uses 6 through 2024-25 and 10 from 2025-26. Only 2020-21 = 6 and 2026-27 = 10 are evidenced; the rest is an assumption, and it moves nothing in the backtest, because no goalkeeper scored in 2025-26.

### 12. Fourteen rows are cards without an appearance

A player who never came on can still be booked on the bench. FPL applies the card deduction and no appearance points, giving a recorded total of -1 against zero minutes. The scoring assembly returns 0 for those rows, because a card term exists only inside the two non-zero minutes buckets. Fourteen such rows exist across 253,890, spread over seven seasons. Modelling P(booked | did not play) would add noise to every prediction to fix them, so they stand as documented mismatches in `reports/xp-model/m2-reconciliation.md`.

### 13. The BPS action table cannot be read from an official source

`game_config.scoring.bps` is absent from the public API response, and the rules page renders that table from a config object the public endpoint does not return. `BPS_TABLES` in `fpl/scoring_rules.py` therefore holds only the per-action values the official rules and the 2026-27 patch notes state in prose. That is enough to establish that the seasons differ and not enough to compute a BPS score from match actions.

### 14. BPS rules changed for 2026-27, and 2026-27 has no data

The 2026-27 patch notes remove the -1 for being tackled, drop the penalty save from 8 BPS to 7, change CBI from 1 BPS per 2 actions to per 3, and restructure saves. Any BPS to bonus mapping fit on earlier seasons is fitting a superseded system. The pinned snapshot has no 2026-27 gameweeks, so §7's training window for the bonus component currently selects zero rows. That needs a decision before M4d.

## Modelling

### 15. Minutes are endogenous to in-match performance

Scoring early reduces the chance of being substituted on 60; a poor game increases it. Conditioning on observed minutes as pure exposure therefore carries some post-treatment bias. Whether per-90 rates conditional on 90 minutes look systematically better than the same player's rates conditional on 65 is an open check, to be recorded here once M5 runs it.

### 16. Historical `minutes == 0` conflates several states

Injured, suspended, unavailable and in-squad-but-unused are one value in this dataset, and nothing separates them. Stated rather than solved.

### 17. Train and serve are asymmetric on availability

No injury news or predicted lineups exist in the training data, and both are available live. The backtest is a lower bound on live performance for that reason, which is the point of the live-only feature policy.

### 18. Component independence understates the haul tail

Sampling components independently in the M6 Monte Carlo will understate P(>=10), because goals, assists and bonus co-occur. Until coverage checks against actuals pass, the mean is the decision variable and the distribution is diagnostic only.
