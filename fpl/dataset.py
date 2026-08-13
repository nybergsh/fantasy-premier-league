"""Ingest the pinned vaastav/Fantasy-Premier-League clone to one player-fixture parquet.

The source is ten seasons of CSVs with a moving schema, so the target columns are declared
here and each season is projected onto them, inserting typed nulls where that season lacks a
column. Naive multi-season concat fails on the mismatch; this is why the contract exists.

`reconcile` scores every ingested row through `fpl.scoring` as a degenerate distribution and
compares to the recorded `total_points`. That is what proves the season-keyed rules table
rather than asserting it.
"""

from dataclasses import dataclass
from pathlib import Path
import subprocess

from loguru import logger
import polars as pl
import typer

from fpl.config import EXTERNAL_DATA_DIR, PROCESSED_DATA_DIR, PROJ_ROOT, REPORTS_DIR
from fpl.scoring import score
from fpl.scoring_rules import SCORING_RULES

app = typer.Typer()

VAASTAV_FPL_DIR = EXTERNAL_DATA_DIR / "vaastav-fpl"
SOURCE_DIR = VAASTAV_FPL_DIR / "data"
OUTPUT_PATH = PROCESSED_DATA_DIR / "player_fixtures.parquet"
DICTIONARY_PATH = PROJ_ROOT / "references" / "data-dictionary.md"
INGESTION_REPORT_PATH = REPORTS_DIR / "xp-model" / "m2-ingestion.md"
RECONCILIATION_REPORT_PATH = REPORTS_DIR / "xp-model" / "m2-reconciliation.md"

# 2026-27 is in the clone but has no gws/ directory — the season has not started.
SEASONS = (
    "2016-17",
    "2017-18",
    "2018-19",
    "2019-20",
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
)

# These three seasons contain bytes that are not valid UTF-8 in the player-name column.
# That column is not in the contract, so lossy decoding costs nothing carried downstream.
LOSSY_SEASONS = ("2016-17", "2017-18", "2018-19")

KICKOFF_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# merged_gw.position exists from 2020-21. Earlier seasons fall back to the players_raw
# end-of-season snapshot, which is mildly leaky for a backtest row — see known-issues.md.
POSITION_FROM_MERGED_GW = ("2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26")
ELEMENT_TYPE_POSITION = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD", 5: "AM"}

# fixtures.csv is absent for the first two seasons, so the player's own team comes from the
# players_raw snapshot there instead of from the fixture's home/away sides.
NO_FIXTURES_SEASONS = ("2016-17", "2017-18")

# master_team_list.csv stops at 2023-24 despite covering "all seasons"; teams.csv starts at
# 2019-20. The two agree exactly on every overlapping season.
TEAMS_CSV_SEASONS = ("2024-25", "2025-26")

# From M0 (references/data-sources/vaastav-fpl.md): rows, and (element, GW) keys carrying
# more than one row. Both are acceptance checks, not diagnostics.
#
# 2025-26 is amended from M0's (29_757, 419). The source file repeats 10 rows verbatim — two
# players, same fixture id, every column identical. M0 counted them as double gameweeks
# because it only measured rows per (element, GW). They are dropped at ingestion.
M0_COUNTS = {
    "2016-17": (23_679, 573),
    "2017-18": (22_467, 670),
    "2018-19": (21_790, 656),
    "2019-20": (22_560, 247),
    "2020-21": (24_365, 1_437),
    "2021-22": (25_447, 2_217),
    "2022-23": (26_505, 1_548),
    "2023-24": (29_725, 983),
    "2024-25": (27_605, 374),
    "2025-26": (29_747, 409),
}


@dataclass(frozen=True)
class Column:
    dtype: pl.DataType
    source: str
    meaning: str


# Pass-through columns, named as they appear in merged_gw.csv.
PASSTHROUGH = {
    "GW": Column(pl.Int32, "merged_gw.csv", "Gameweek number"),
    "fixture": Column(pl.Int32, "merged_gw.csv", "Season-scoped fixture id"),
    "element": Column(
        pl.Int32, "merged_gw.csv", "Season-scoped player id; never a cross-season key"
    ),
    "opponent_team": Column(pl.Int32, "merged_gw.csv", "Season-scoped id of the opposing team"),
    "was_home": Column(pl.Boolean, "merged_gw.csv", "Player's team was at home"),
    "minutes": Column(pl.Int32, "merged_gw.csv", "Minutes played"),
    "starts": Column(pl.Int32, "merged_gw.csv", "1 if the player started; null before 2022-23"),
    "goals_scored": Column(pl.Int32, "merged_gw.csv", "Goals scored"),
    "assists": Column(pl.Int32, "merged_gw.csv", "Assists"),
    "clean_sheets": Column(pl.Int32, "merged_gw.csv", "1 if a clean sheet was credited"),
    "goals_conceded": Column(pl.Int32, "merged_gw.csv", "Goals conceded while on the pitch"),
    "saves": Column(pl.Int32, "merged_gw.csv", "Saves made"),
    "bonus": Column(pl.Int32, "merged_gw.csv", "Bonus points awarded, 0-3"),
    "bps": Column(pl.Int32, "merged_gw.csv", "Bonus Points System score"),
    "yellow_cards": Column(pl.Int32, "merged_gw.csv", "Yellow cards"),
    "red_cards": Column(pl.Int32, "merged_gw.csv", "Red cards"),
    "own_goals": Column(pl.Int32, "merged_gw.csv", "Own goals"),
    "penalties_missed": Column(pl.Int32, "merged_gw.csv", "Penalties missed"),
    "penalties_saved": Column(pl.Int32, "merged_gw.csv", "Penalties saved"),
    "expected_goals": Column(pl.Float64, "merged_gw.csv", "Opta xG; null before 2022-23"),
    "expected_assists": Column(pl.Float64, "merged_gw.csv", "Opta xA; null before 2022-23"),
    "expected_goals_conceded": Column(
        pl.Float64, "merged_gw.csv", "Opta xGC; null before 2022-23"
    ),
    "clearances_blocks_interceptions": Column(
        pl.Int32, "merged_gw.csv", "CBI count; present 2016-17 to 2018-19 and 2025-26 only"
    ),
    "tackles": Column(pl.Int32, "merged_gw.csv", "Tackles; same seasons as CBI"),
    "recoveries": Column(pl.Int32, "merged_gw.csv", "Recoveries; same seasons as CBI"),
    "defensive_contribution": Column(
        pl.Int32, "merged_gw.csv", "Defcon action count, position-specific; 2025-26 only"
    ),
    "total_points": Column(pl.Int32, "merged_gw.csv", "Points actually scored; the eval target"),
}

DERIVED = {
    "season": Column(pl.String, "derived", "Season label, e.g. 2025-26"),
    "kickoff_time": Column(
        pl.Datetime(time_zone="UTC"),
        "merged_gw.csv",
        "Kickoff, UTC; every asof cutoff keys on this",
    ),
    "code": Column(
        pl.Int32,
        "players_raw.csv",
        "Stable cross-season player id; all cross-season joins use this",
    ),
    "position": Column(pl.String, "merged_gw.csv / players_raw.csv", "GK, DEF, MID, FWD, or AM"),
    "team": Column(
        pl.Int32, "fixtures.csv / players_raw.csv", "Season-scoped id of the player's team"
    ),
    "team_name": Column(
        pl.String, "master_team_list.csv / teams.csv", "Club name for that season"
    ),
}

CONTRACT = DERIVED | {
    ("opponent" if c == "opponent_team" else c): v for c, v in PASSTHROUGH.items()
}


def log_source_lineage(source_dir: Path = VAASTAV_FPL_DIR) -> None:
    """Log the pinned upstream commit SHA so processed output can be traced to a source snapshot.

    See references/data-sources/vaastav-fpl.md for the full provenance record (fetch date,
    file hash manifest, reproduction command) — this only logs the SHA on each run.
    """
    sha = subprocess.check_output(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"], text=True
    ).strip()
    logger.info(f"Source: vaastav/Fantasy-Premier-League @ {sha}")


def team_crosswalk(season: str) -> pl.DataFrame:
    if season in TEAMS_CSV_SEASONS:
        teams = pl.read_csv(SOURCE_DIR / season / "teams.csv")
        return teams.select(
            pl.col("id").cast(pl.Int32).alias("team"), pl.col("name").alias("team_name")
        )
    master = pl.read_csv(SOURCE_DIR / "master_team_list.csv").filter(pl.col("season") == season)
    return master.select(pl.col("team").cast(pl.Int32), "team_name")


def load_season(season: str) -> pl.DataFrame:
    encoding = "utf8-lossy" if season in LOSSY_SEASONS else "utf8"
    raw = pl.read_csv(
        SOURCE_DIR / season / "gws" / "merged_gw.csv",
        infer_schema_length=30_000,
        encoding=encoding,
    )
    players = pl.read_csv(
        SOURCE_DIR / season / "players_raw.csv", infer_schema_length=5_000, encoding="utf8-lossy"
    ).select(
        pl.col("id").cast(pl.Int32).alias("element"),
        pl.col("code").cast(pl.Int32),
        "element_type",
        pl.col("team").cast(pl.Int32).alias("snapshot_team"),
    )

    projected = raw.select(
        *(
            pl.col(name).cast(column.dtype)
            if name in raw.columns
            else pl.lit(None, dtype=column.dtype).alias(name)
            for name, column in PASSTHROUGH.items()
        ),
        pl.col("kickoff_time").str.to_datetime(KICKOFF_FORMAT, time_zone="UTC"),
        pl.lit(season).alias("season"),
    ).rename({"opponent_team": "opponent"})

    joined = projected.join(players, on="element", how="left")

    if season in POSITION_FROM_MERGED_GW:
        # 2021-22 GW37 labels goalkeepers GKP, every other gameweek in every season says GK.
        joined = joined.with_columns(
            raw.get_column("position").replace("GKP", "GK").alias("position")
        )
    else:
        joined = joined.with_columns(
            pl.col("element_type").replace_strict(ELEMENT_TYPE_POSITION).alias("position")
        )

    if season in NO_FIXTURES_SEASONS:
        joined = joined.with_columns(pl.col("snapshot_team").alias("team"))
    else:
        fixtures = pl.read_csv(
            SOURCE_DIR / season / "fixtures.csv", infer_schema_length=2_000
        ).select(pl.col("id").cast(pl.Int32).alias("fixture"), "team_h", "team_a")
        joined = joined.join(fixtures, on="fixture", how="left").with_columns(
            pl.when("was_home").then("team_h").otherwise("team_a").cast(pl.Int32).alias("team")
        )

    return joined.join(team_crosswalk(season), on="team", how="left").select(list(CONTRACT))


def check_grain(frame: pl.DataFrame) -> None:
    """Stage boundary: assert and stop, never repair."""
    keys = frame.select("season", "element", "GW", "fixture")
    assert keys.n_unique() == frame.height, "(season, element, GW, fixture) is not unique"

    per_gameweek = frame.group_by("season", "element", "GW").len()
    # 2020-21 has triple gameweeks from COVID rescheduling, so the ceiling is 3, not 2.
    assert per_gameweek["len"].max() <= 3, "more than 3 fixtures for one player in one gameweek"

    assert frame["kickoff_time"].null_count() == 0
    assert frame["minutes"].is_between(0, 130).all(), "minutes outside 0-130"


def check_season_counts(frame: pl.DataFrame) -> None:
    for season, (rows, duplicate_keys) in M0_COUNTS.items():
        season_frame = frame.filter(pl.col("season") == season)
        assert season_frame.height == rows, f"{season}: {season_frame.height} rows, M0 says {rows}"
        duplicates = season_frame.group_by("element", "GW").len().filter(pl.col("len") > 1).height
        assert duplicates == duplicate_keys, (
            f"{season}: {duplicates} duplicate (element, GW) keys, M0 says {duplicate_keys}"
        )


def write_data_dictionary(frame: pl.DataFrame, output_path: Path) -> None:
    rows = []
    for name, column in CONTRACT.items():
        populated = [
            season
            for season in SEASONS
            if frame.filter(pl.col("season") == season)[name].null_count()
            < frame.filter(pl.col("season") == season).height
        ]
        span = "all" if len(populated) == len(SEASONS) else ", ".join(populated) or "none"
        rows.append(f"| `{name}` | {column.dtype} | {column.source} | {column.meaning} | {span} |")
    output_path.write_text(
        "# Data dictionary — `player_fixtures.parquet`\n\n"
        "Generated by `uv run python -m fpl.dataset main`. Edit `fpl/dataset.py`, not this file.\n\n"
        "Grain is one row per player per fixture. The unique key is\n"
        "`(season, element, GW, fixture)`; `(element, GW)` is not unique, because of double\n"
        "and triple gameweeks.\n\n"
        "| Column | Type | Source | Meaning | Seasons populated |\n|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n`xP` is dropped rather than lagged, and no set-piece column is carried. Both are\n"
        "deliberate exclusions — see `references/known-issues.md`.\n",
        encoding="utf-8",
    )


@app.command()
def main(output_path: Path = OUTPUT_PATH) -> None:
    log_source_lineage()
    logger.info(f"Ingesting {len(SEASONS)} seasons from {SOURCE_DIR}")
    raw = pl.concat([load_season(season) for season in SEASONS])
    frame = raw.unique(maintain_order=True)
    if frame.height < raw.height:
        logger.info(f"Dropped {raw.height - frame.height} rows repeated verbatim in the source")
    check_grain(frame)
    check_season_counts(frame)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(output_path)
    write_data_dictionary(frame, DICTIONARY_PATH)
    write_ingestion_report(frame, INGESTION_REPORT_PATH)
    logger.success(f"Wrote {frame.height} rows to {output_path}")


def write_ingestion_report(frame: pl.DataFrame, output_path: Path) -> None:
    counts = (
        frame.group_by("season")
        .agg(pl.len().alias("rows"))
        .sort("season")
        .join(
            frame.group_by("season", "element", "GW")
            .len()
            .filter(pl.col("len") > 1)
            .group_by("season")
            .agg(pl.len().alias("duplicate_keys")),
            on="season",
        )
        .sort("season")
    )
    rows = "\n".join(
        f"| {s} | {r:,} | {M0_COUNTS[s][0]:,} | {d:,} | {M0_COUNTS[s][1]:,} |"
        for s, r, d in counts.iter_rows()
    )
    schema = "\n".join(f"| `{name}` | {dtype} |" for name, dtype in frame.schema.items())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        f"""# M2 — ingestion

Generated by `uv run python -m fpl.dataset main`.

`data/processed/player_fixtures.parquet`, {frame.height:,} rows at player-fixture grain,
{len(SEASONS)} seasons. The unique key is `(season, element, GW, fixture)`.

## Row counts against M0

Ingestion asserts both expected columns and stops if either differs. 2025-26 is amended from
M0's 29,757 rows and 419 duplicate keys, because ten of those rows are the same row written
twice rather than double gameweeks. See known issue 4.

| Season | Rows | Expected | Duplicate (element, GW) keys | Expected |
|---|---|---|---|---|
{rows}

## Schema

| Column | Type |
|---|---|
{schema}
""",
        encoding="utf-8",
    )


def realised_components(frame: pl.DataFrame) -> pl.DataFrame:
    """Each played fixture as a degenerate component distribution, all mass on what happened.

    Feeding these through `fpl.scoring.score` reproduces the deterministic points the player
    earned, so a season-wide comparison against `total_points` tests the rules table and the
    assembly together.
    """
    played = pl.col("minutes") > 0
    sixty_plus = pl.col("minutes") >= 60
    per_90 = pl.when(played).then(90 / pl.col("minutes")).otherwise(0.0)

    def rate(column: str) -> pl.Expr:
        return pl.col(column) * per_90

    def point_mass(column: str) -> pl.Expr:
        return pl.int_ranges(0, pl.col(column) + 1).list.eval(
            (pl.element() == pl.element().max()).cast(pl.Float64)
        )

    def in_bucket(expr: pl.Expr, want_sixty_plus: bool) -> pl.Expr:
        active = sixty_plus if want_sixty_plus else played & ~sixty_plus
        return pl.when(active).then(expr).otherwise(0.0)

    defcon_threshold = pl.col("position").replace_strict(
        SCORING_RULES["2025-26"].defcon_threshold, default=999, return_dtype=pl.Int32
    )
    hit_threshold = (pl.col("defensive_contribution").fill_null(0) >= defcon_threshold).cast(
        pl.Float64
    )
    empty_pmf = pl.lit([1.0], dtype=pl.List(pl.Float64))

    return frame.select(
        "season",
        "position",
        "total_points",
        "element",
        "GW",
        "minutes",
        "yellow_cards",
        "red_cards",
        p_zero=(~played).cast(pl.Float64),
        p_1_59=(played & ~sixty_plus).cast(pl.Float64),
        p_60plus=sixty_plus.cast(pl.Float64),
        exp_min_1_59=in_bucket(pl.col("minutes"), False),
        exp_min_60plus=in_bucket(pl.col("minutes"), True),
        goals_per90=rate("goals_scored"),
        assists_per90=rate("assists"),
        yellow_cards_per90=rate("yellow_cards"),
        red_cards_per90=rate("red_cards"),
        own_goals_per90=rate("own_goals"),
        penalties_missed_per90=rate("penalties_missed"),
        penalties_saved_per90=rate("penalties_saved"),
        p_clean_sheet=pl.col("clean_sheets").cast(pl.Float64),
        p_defcon_1_59=in_bucket(hit_threshold, False),
        p_defcon_60plus=in_bucket(hit_threshold, True),
        e_bonus_1_59=in_bucket(pl.col("bonus"), False),
        e_bonus_60plus=in_bucket(pl.col("bonus"), True),
        saves_pmf_1_59=pl.when(played & ~sixty_plus)
        .then(point_mass("saves"))
        .otherwise(empty_pmf),
        saves_pmf_60plus=pl.when(sixty_plus).then(point_mass("saves")).otherwise(empty_pmf),
        gc_pmf_1_59=pl.when(played & ~sixty_plus)
        .then(point_mass("goals_conceded"))
        .otherwise(empty_pmf),
        gc_pmf_60plus=pl.when(sixty_plus).then(point_mass("goals_conceded")).otherwise(empty_pmf),
    )


@app.command()
def reconcile(
    input_path: Path = OUTPUT_PATH, output_path: Path = RECONCILIATION_REPORT_PATH
) -> None:
    logger.info(f"Reconciling {input_path} against fpl.scoring_rules")
    frame = pl.read_parquet(input_path)
    managers = frame.filter(pl.col("position") == "AM")
    components = realised_components(frame.filter(pl.col("position") != "AM"))

    # A player who never came on can still be booked on the bench: FPL applies the card
    # deduction with no appearance points, and no minutes bucket carries a card term.
    booked_without_playing = (pl.col("minutes") == 0) & (
        pl.col("yellow_cards") + pl.col("red_cards") > 0
    )

    rows = []
    unexplained = []
    for season in SEASONS:
        scored = score(components.filter(pl.col("season") == season), season)
        mismatched = scored.filter((pl.col("xp") - pl.col("total_points")).abs() > 1e-6)
        rest = mismatched.filter(~booked_without_playing)
        rows.append((season, scored.height, mismatched.height, rest.height))
        unexplained.append(rest)
        if rest.height:
            logger.warning(f"{season}: {rest.height} unexplained mismatches")

    table = "\n".join(f"| {s} | {n:,} | {m:,} | {u:,} |" for s, n, m, u in rows)
    listing = pl.concat(unexplained).select(
        "season", "GW", "element", "position", "minutes", "total_points", "xp"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        f"""# M2 — rules reconciliation

Generated by `uv run python -m fpl.dataset reconcile`.

Every ingested row is rebuilt as a degenerate component distribution — all probability mass on
what actually happened — and scored through `fpl/scoring.py` under its own season's rules. A
mismatch means the season-keyed table in `fpl/scoring_rules.py` is wrong for that season, the
assembly is wrong, or the source row is internally inconsistent. Mismatches are counted and
reported, never repaired.

The {managers.height:,} Assistant Manager rows in 2024-25 are excluded. They score under a
separate `mng_*` rule set this model does not carry.

Every mismatch found so far is a player booked while on the bench: zero minutes, a card, and a
points total of -1. The scoring assembly returns 0 for them, because a card term exists only
inside the two non-zero minutes buckets. Those rows are counted separately below rather than
folded into the total, since modelling P(booked | did not play) would add noise to every
prediction to fix 14 rows in 253,890.

| Season | Rows scored | Mismatches | Unexplained |
|---|---|---|---|
{table}

Unexplained mismatches, if any:

```
{listing}
```
""",
        encoding="utf-8",
    )
    logger.success(f"Wrote {output_path}")


if __name__ == "__main__":
    app()
