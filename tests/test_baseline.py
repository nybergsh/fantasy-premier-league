"""Baseline contract tests. Data-free: every frame here is built in the test.

Expected values are computed by hand and written as arithmetic, never by calling the baseline
under test.
"""

from datetime import datetime

import numpy as np
import polars as pl
import pytest

from fpl.modeling.baseline import (
    baseline_lag_regression,
    baseline_minutes_times_ppg,
    baseline_positional_mean,
)


def row(code: int, gw: int, season: str = "2025-26", position: str = "MID", **overrides) -> dict:
    base = {
        "season": season,
        "code": code,
        "element": code,
        "GW": gw,
        "fixture": gw * 10 + code,
        "position": position,
        "kickoff_time": datetime(2025, 8, 1) if season == "2025-26" else datetime(2024, 8, 1),
        "minutes": 90,
        "total_points": 2,
    }
    return base | overrides


def frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows)).with_columns(
        # spread kickoff_time across gameweeks so `asof` at a later GW sees only earlier ones
        (pl.col("kickoff_time") + pl.duration(days=7 * (pl.col("GW") - 1))).dt.replace_time_zone("UTC"),
        pl.col("total_points").cast(pl.Float64),
    )


def test_positional_mean_is_the_expanding_mean_before_asof():
    history = frame(
        row(1, 1, position="DEF", total_points=2),
        row(2, 1, position="DEF", total_points=6),
        row(3, 1, position="MID", total_points=10),
    )
    target = frame(row(1, 2, position="DEF", total_points=0))
    asof = target["kickoff_time"].min()
    predicted = baseline_positional_mean(pl.concat([history, target]), asof)
    def_xp = predicted.filter(pl.col("GW") == 2)["xp"].item()
    assert def_xp == pytest.approx((2 + 6) / 2)


def test_positional_mean_does_not_see_rows_after_asof():
    history = frame(row(1, 1, position="DEF", total_points=2))
    future = frame(row(2, 3, position="DEF", total_points=100))
    target = frame(row(1, 2, position="DEF", total_points=0))
    asof = target["kickoff_time"].min()

    without_future = baseline_positional_mean(pl.concat([history, target]), asof)
    with_future = baseline_positional_mean(pl.concat([history, target, future]), asof)
    xp_a = without_future.filter(pl.col("GW") == 2)["xp"].item()
    xp_b = with_future.filter(pl.col("GW") == 2)["xp"].item()
    assert xp_a == xp_b == pytest.approx(2.0)


def test_minutes_times_ppg_multiplies_trailing_minutes_by_season_to_date_rate():
    history = frame(
        row(1, 1, minutes=60, total_points=6),
        row(1, 2, minutes=90, total_points=9),
    )
    target = frame(row(1, 3, minutes=0, total_points=0))
    asof = target["kickoff_time"].min()
    predicted = baseline_minutes_times_ppg(pl.concat([history, target]), asof)
    result = predicted.filter(pl.col("GW") == 3).row(0, named=True)

    expected_minutes = (60 + 90) / 2
    expected_ppg90 = (6 + 9) / (60 + 90) * 90
    assert result["xp_minutes_component"] == pytest.approx(expected_minutes)
    assert result["xp_rate_component"] == pytest.approx(expected_ppg90)
    assert result["xp"] == pytest.approx(expected_minutes / 90 * expected_ppg90)


def test_minutes_times_ppg_cold_start_is_zero_not_an_error():
    target = frame(row(1, 1, minutes=0, total_points=0))
    predicted = baseline_minutes_times_ppg(target, target["kickoff_time"].min())
    assert predicted["xp"].item() == 0.0


def test_lag_regression_cold_start_predicts_zero():
    target = frame(row(1, 1, total_points=0))
    predicted = baseline_lag_regression(target, target["kickoff_time"].min())
    assert predicted["xp"].item() == 0.0


def test_lag_regression_recovers_an_exact_linear_relationship():
    # total_points this gameweek is exactly last gameweek's points doubled, for enough
    # gameweeks that OLS on (lag_1, lag_2, lag_3) recovers it well within the fit tolerance.
    rows = []
    points = 1.0
    for gw in range(1, 12):
        rows.append(row(1, gw, total_points=points))
        points += 1
    target = frame(row(1, 12, total_points=0))
    history = frame(*rows)
    combined = pl.concat([history, target])
    asof = target["kickoff_time"].min()

    predicted = baseline_lag_regression(combined, asof)
    xp_gw12 = predicted.filter(pl.col("GW") == 12)["xp"].item()
    # lag_1 at GW12 is GW11's total (11.0); the series is points = gw, so the honest forecast
    # from a linear fit on the last three lags should land close to 12.
    assert xp_gw12 == pytest.approx(12.0, abs=0.5)


def test_lag_regression_does_not_see_rows_after_asof():
    rows = [row(1, gw, total_points=float(gw)) for gw in range(1, 6)]
    future = row(1, 20, total_points=-1000.0)
    target = frame(row(1, 6, total_points=0))
    asof = target["kickoff_time"].min()

    without_future = baseline_lag_regression(pl.concat([frame(*rows), target]), asof)
    with_future = baseline_lag_regression(pl.concat([frame(*rows), target, frame(future)]), asof)
    xp_a = without_future.filter(pl.col("GW") == 6)["xp"].item()
    xp_b = with_future.filter(pl.col("GW") == 6)["xp"].item()
    assert xp_a == pytest.approx(xp_b)
