"""Walk-forward harness tests. Data-free: every frame here is built in the test, and the
predictors under test are trivial stand-ins, not the real baselines (those are covered in
tests/test_baseline.py).
"""

from datetime import datetime, timezone

import polars as pl
import pytest

from fpl.evaluate import gameweek_deadlines, to_gameweek_grain, walk_forward


def fixture_row(code: int, gw: int, fixture: int, kickoff: datetime, **overrides) -> dict:
    row = {
        "season": "2025-26",
        "code": code,
        "GW": gw,
        "fixture": fixture,
        "position": "MID",
        "kickoff_time": kickoff,
        "minutes": 90,
        "total_points": 2,
    }
    return row | overrides


def frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows)).with_columns(pl.col("kickoff_time").dt.replace_time_zone("UTC"))


def test_gameweek_deadlines_are_the_earliest_kickoff_per_gw_sorted():
    data = frame(
        fixture_row(1, 2, 200, datetime(2025, 8, 22)),
        fixture_row(1, 1, 100, datetime(2025, 8, 15)),
        fixture_row(2, 1, 101, datetime(2025, 8, 16)),  # later kickoff, same GW
    )
    deadlines = gameweek_deadlines(data)
    assert deadlines == [
        datetime(2025, 8, 15, tzinfo=timezone.utc),
        datetime(2025, 8, 22, tzinfo=timezone.utc),
    ]


def test_to_gameweek_grain_sums_across_a_double_gameweek():
    predictions = frame(
        fixture_row(1, 1, 100, datetime(2025, 8, 15), total_points=5, minutes=90, xp=3.0),
        fixture_row(1, 1, 101, datetime(2025, 8, 18), total_points=7, minutes=80, xp=4.0),
    )
    grain = to_gameweek_grain(predictions)
    assert grain.height == 1
    row = grain.row(0, named=True)
    assert row["actual"] == 12
    assert row["xp"] == pytest.approx(7.0)
    assert row["minutes"] == 170


def test_walk_forward_scores_each_gameweek_from_its_own_window():
    data = frame(
        fixture_row(1, 1, 100, datetime(2025, 8, 15), total_points=2),
        fixture_row(1, 2, 200, datetime(2025, 8, 22), total_points=9),
    )

    def constant_predict(player_fixtures: pl.DataFrame, asof: datetime, availability=None) -> pl.DataFrame:
        return player_fixtures.with_columns(xp=pl.lit(5.0))

    scored = walk_forward(data, constant_predict)
    assert scored.sort("GW")["actual"].to_list() == [2, 9]
    assert scored["xp"].to_list() == [5.0, 5.0]


def test_walk_forward_passes_each_gameweeks_own_deadline_as_asof():
    data = frame(
        fixture_row(1, 1, 100, datetime(2025, 8, 15)),
        fixture_row(1, 2, 200, datetime(2025, 8, 22)),
    )
    seen_asof = []

    def spy_predict(player_fixtures: pl.DataFrame, asof: datetime, availability=None) -> pl.DataFrame:
        seen_asof.append(asof)
        return player_fixtures.with_columns(xp=pl.lit(0.0))

    walk_forward(data, spy_predict)
    assert seen_asof == [
        datetime(2025, 8, 15, tzinfo=timezone.utc),
        datetime(2025, 8, 22, tzinfo=timezone.utc),
    ]
